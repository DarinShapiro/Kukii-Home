"""Per-event persistent store under /data/sentihome/events/<event_id>/.

The legacy :class:`AlertLog` is a thin in-memory + flat-JSON-file recent-
alert index used by the HA integration's polling path. EventStore is the
heavier, durable, per-event artifact store the notification tap UX needs:
each event becomes its own directory with frames, metadata, user feedback,
and (eventually) VLM response.

Directory layout per event::

    <root>/<event_id>/
      meta.json            full event record (see _EVENT_KEYS for shape)
      frame.jpg            raw snapshot at alert time (when captured)
      annotated.jpg        marked-up version (when preprocessor produces one)
      feedback.json        user FP/correction feedback (when submitted)
      vlm/                 reserved for Phase 11 VLM artifacts

The schema is deliberately wider than today's needs so the new persistence
slots in cleanly:

* ``triage_decision`` discriminates ``"alert_fired" | "alert_suppressed"
  | "near_miss" | "vlm_flagged_silent"``. Today we only write ``alert_fired``;
  the silent-log path (near-misses + VLM disagreements for FN analysis)
  reuses the same dir structure and reader code.
* ``vlm_response`` is reserved as a top-level field on meta; populated by
  Phase 11's VLM dispatch without breaking older readers.
* ``feedback`` is its own file so it can be updated independently of the
  immutable event meta.

EventStore is intentionally simple — no DB, no index, just filesystem reads.
``recent()`` walks the dir and sorts by mtime. At ~1MB per event and a
handful of events per day, this is fine for years. When we add the silent
log + automatic VLM-on-every-frame, the scale story changes and we'll need
sampling + retention rules; flagged in the planning doc.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# Top-level meta keys we know about. New fields can be added by callers —
# the store doesn't validate the shape (forward-compat over strictness).
_EVENT_KEYS = (
    "event_id",
    "alert_id",  # same as event_id when triage_decision == "alert_fired"
    "triage_decision",  # "alert_fired" | "alert_suppressed" | "near_miss" | "vlm_flagged_silent"
    "recorded_at",
    "camera_id",
    "camera_entity",
    "camera_name",
    "sensor_classification",
    "headline",
    "detections",  # tuple of DetectionTag dicts
    "identified_entities",  # tuple of IdentifiedEntity dicts
    "actor_matches",  # tuple of ActorMatch dicts
    "vlm_response",  # reserved for Phase 11; None today
    "timings",  # per-stage latency (HA->snapshot, etc.)
    "evidence_ref",  # source frame path before we copied it in
)


@dataclass
class EventStore:
    """Filesystem-backed per-event store.

    Construct once at bootstrap with the root directory. Wire
    :meth:`record_from_alert` into ``AlertLog.add_on_record`` so every
    alert recorded in the lightweight log also gets a durable per-event
    directory with copied snapshot + structured meta.
    """

    root: Path
    """Base directory. ``<root>/<event_id>/`` per event. Created on first
    write. Use ``/data/sentihome/events`` in production (HA Supervisor's
    persistent volume)."""

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def record_from_alert(self, alert: dict[str, Any]) -> str | None:
        """Persist an alert as an event.

        Returns the event_id on success, None when the alert lacks an
        id (which it shouldn't — alerts always carry one, but defensive
        here so an upstream bug doesn't crash the recording path).

        Triage decision is ``"alert_fired"`` (this is from the alert-
        firing path). For non-alert events (near-miss / VLM-silent) the
        future producer will call :meth:`record_event` directly.

        Snapshot handling: if the alert has an ``evidence_ref`` pointing
        at an on-disk snapshot, copy it into ``<event_id>/frame.jpg``.
        The original is left in place — the existing /alerts/<id>/snapshot
        route still works against ``evidence_ref`` for backward-compat.
        We copy rather than move so the legacy snapshot retention logic
        doesn't accidentally orphan the event's frame.
        """
        event_id = alert.get("alert_id") or alert.get("event_id")
        if not event_id:
            logger.warning("event_store.alert_missing_id", alert_keys=list(alert.keys()))
            return None

        meta = dict(alert)
        meta["event_id"] = event_id
        meta.setdefault("triage_decision", "alert_fired")
        # vlm_response reserved for Phase 11 — leave as None today so
        # the per-alert page can render "VLM: not yet analyzed" cleanly.
        meta.setdefault("vlm_response", None)

        try:
            event_dir = self._dir_for(event_id)
            event_dir.mkdir(parents=True, exist_ok=True)
            self._write_meta(event_dir, meta)
            evidence = alert.get("evidence_ref")
            if evidence:
                self._copy_frame(Path(evidence), event_dir / "frame.jpg")
        except OSError as e:
            # Persistence failure mustn't break alert recording. The
            # in-memory AlertLog is still authoritative for the recent-
            # alerts list; the per-event page will just 404 for this id.
            logger.warning(
                "event_store.record_failed",
                event_id=event_id,
                error=str(e),
            )
            return None
        return event_id

    def get(self, event_id: str) -> dict[str, Any] | None:
        """Read the event's meta + feedback. Returns None for unknown.

        Merges ``feedback.json`` (if present) into the result under
        ``meta["feedback"]`` so consumers don't have to make two reads.
        """
        event_dir = self._dir_for(event_id)
        meta_path = event_dir / "meta.json"
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("event_store.get_failed", event_id=event_id, error=str(e))
            return None
        feedback = self._read_feedback(event_dir)
        if feedback is not None:
            meta["feedback"] = feedback
        return meta

    def frame_path(self, event_id: str, *, annotated: bool = False) -> Path | None:
        """Filesystem path to the requested frame, or None if missing.

        Used by the per-alert page's image-serving routes to FileResponse
        directly without re-reading the bytes into Python.
        """
        name = "annotated.jpg" if annotated else "frame.jpg"
        path = self._dir_for(event_id) / name
        return path if path.exists() else None

    def record_feedback(self, event_id: str, *, feedback: dict[str, Any]) -> bool:
        """Write the user's FP feedback to ``feedback.json``.

        Returns True on success, False if the event doesn't exist (don't
        create orphan feedback files for unknown ids — that'd be a sign
        of a bug, not a write to swallow).
        """
        event_dir = self._dir_for(event_id)
        if not (event_dir / "meta.json").exists():
            logger.warning(
                "event_store.feedback_for_unknown_event",
                event_id=event_id,
            )
            return False
        try:
            self._write_json(event_dir / "feedback.json", feedback)
        except OSError as e:
            logger.warning(
                "event_store.feedback_write_failed",
                event_id=event_id,
                error=str(e),
            )
            return False
        return True

    def record_enrichment(
        self,
        event_id: str,
        *,
        detections: list[dict[str, Any]] | None = None,
        identified_entities: list[dict[str, Any]] | None = None,
        actor_matches: list[dict[str, Any]] | None = None,
        annotated_jpeg: bytes | None = None,
    ) -> bool:
        """Fold preprocessor recognition into an existing event (Epic
        10.9).

        Called by :class:`AlertEnricher` after the preprocessor
        returns a FrameWindow for the alert's camera + time. Merges
        the detection/identity fields into ``meta.json`` and, when an
        annotated frame is supplied, writes it as ``annotated.jpg``
        (the per-alert page serves that in preference to the raw
        snapshot). Each field is only overwritten when a non-None
        value is passed, so a partial enrichment doesn't blank out
        what's already there.

        Returns False for unknown events (the event dir is written
        synchronously at alert time, before this async enrichment
        runs, so a missing dir signals a real ordering bug).
        """
        event_dir = self._dir_for(event_id)
        meta_path = event_dir / "meta.json"
        if not meta_path.exists():
            logger.warning("event_store.enrich_unknown_event", event_id=event_id)
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if detections is not None:
                meta["detections"] = detections
            if identified_entities is not None:
                meta["identified_entities"] = identified_entities
            if actor_matches is not None:
                meta["actor_matches"] = actor_matches
            from datetime import UTC, datetime

            meta["enriched"] = True
            meta["enriched_at"] = datetime.now(UTC).isoformat()
            self._write_meta(event_dir, meta)
            if annotated_jpeg:
                (event_dir / "annotated.jpg").write_bytes(annotated_jpeg)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("event_store.enrich_write_failed", event_id=event_id, error=str(e))
            return False
        return True

    def record_decision(
        self,
        event_id: str,
        *,
        criticality: str,
        explanation: str,
        confidence: float,
        backend: str,
        notified: bool,
        recognition_status: str,
    ) -> bool:
        """Persist the triage/VLM decision onto an event (Epic 10.6).

        Written by :class:`TriageGate` after the reasoner decides. Stores
        a ``vlm_response`` block (so the per-alert page's "VLM analysis"
        section renders the reasoning — the ``text`` key feeds the
        existing renderer) plus a ``triage_status`` of ``alerted`` /
        ``dismissed`` for the Recent-alerts list and the
        ``recognition_status`` tag (how grounded the decision was).

        Returns False for unknown events (the dir is written
        synchronously at alert time, before this async decision runs).
        """
        event_dir = self._dir_for(event_id)
        meta_path = event_dir / "meta.json"
        if not meta_path.exists():
            logger.warning("event_store.decision_unknown_event", event_id=event_id)
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            from datetime import UTC, datetime

            meta["vlm_response"] = {
                "text": explanation,
                "criticality": criticality,
                "confidence": confidence,
                "backend": backend,
                "stub": backend.startswith("stub"),
            }
            meta["triage_status"] = "alerted" if notified else "dismissed"
            meta["recognition_status"] = recognition_status
            meta["reasoned_at"] = datetime.now(UTC).isoformat()
            self._write_meta(event_dir, meta)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("event_store.decision_write_failed", event_id=event_id, error=str(e))
            return False
        return True

    def mark_dismissed(self, event_id: str) -> bool:
        """Tag the event as user-dismissed.

        Idempotent. Returns False for unknown ids (same rationale as
        :meth:`record_feedback`).
        """
        event_dir = self._dir_for(event_id)
        meta_path = event_dir / "meta.json"
        if not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["dismissed"] = True
            from datetime import UTC, datetime

            meta["dismissed_at"] = datetime.now(UTC).isoformat()
            self._write_meta(event_dir, meta)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "event_store.dismiss_failed",
                event_id=event_id,
                error=str(e),
            )
            return False
        return True

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """All events sorted by recorded_at desc, up to ``limit``.

        Used by the per-alert page's "other recent at this camera" strip
        (eventually) and by tests. Cheap at current volumes — scans the
        root dir, parses each meta.json. Migrate to an index file when
        event count grows past a few thousand.
        """
        if not self.root.exists():
            return []
        events: list[dict[str, Any]] = []
        for event_dir in self.root.iterdir():
            if not event_dir.is_dir():
                continue
            meta_path = event_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                events.append(meta)
            except (OSError, json.JSONDecodeError):
                continue
        events.sort(key=lambda e: e.get("recorded_at", ""), reverse=True)
        return events[:limit]

    # ─── internals ─────────────────────────────────────────────────

    def _dir_for(self, event_id: str) -> Path:
        # Defense in depth: event_id comes from the alert pipeline (our
        # own code) but if a malicious actor ever fed us one with '..'
        # we don't want it escaping the root.
        safe = event_id.replace("..", "_").replace("/", "_").replace("\\", "_")
        return self.root / safe

    def _write_meta(self, event_dir: Path, meta: dict[str, Any]) -> None:
        self._write_json(event_dir / "meta.json", meta)

    def _read_feedback(self, event_dir: Path) -> dict[str, Any] | None:
        path = event_dir / "feedback.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        """Atomic write — tempfile + rename. Survives mid-write crash."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, default=_json_default), encoding="utf-8")
        tmp.replace(path)

    def _copy_frame(self, src: Path, dst: Path) -> None:
        if not src.exists():
            logger.debug(
                "event_store.evidence_missing",
                src=str(src),
                hint="alert recorded without snapshot — common for legacy alerts",
            )
            return
        shutil.copyfile(src, dst)


def _json_default(obj: Any) -> Any:
    """Coerce non-JSON-native types we sometimes see in alert payloads
    (e.g. dataclasses, tuples-from-pydantic) into something json.dumps
    accepts. Falls back to str() so the write never raises."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)
