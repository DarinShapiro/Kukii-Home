"""AlertEnricher — fold preprocessor recognition into HA-fired alerts.

Epic 10.9. When HA's own AI motion sensor fires (person/vehicle/
animal), the HA-agent records an alert with a camera snapshot — but
it has no idea *who/what* it is. The preprocessor, running on a
separate inference box, has been continuously buffering RTSP frames
for that same camera and running YOLO + the identity router over
them. This enricher closes the loop: after an alert is recorded, it
pulls the preprocessor's :class:`FrameWindow` for that camera around
the event time and folds the detections + identified entities (and a
boxes-drawn annotated frame) into the stored event.

The wiring mirrors :class:`AlertNotifier`: :meth:`on_alert` is a
synchronous :meth:`AlertLog.add_on_record` callback that fires-and-
forgets an async task so alert recording stays fast. It is registered
*after* the EventStore callback so the event directory already exists
when :meth:`EventStore.record_enrichment` runs.

Everything degrades gracefully: a sleeping/unreachable inference box,
an empty window, or a parse error simply leaves the alert with its
original HA snapshot + rule-that-fired. Enrichment is a bonus, never
a dependency.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

import structlog

from .event_store import EventStore
from .preprocessor_client import PreprocessorClient

logger = structlog.get_logger(__name__)


@dataclass
class AlertEnricher:
    """Pull preprocessor recognition for an alert and store it.

    Construct with a :class:`PreprocessorClient` (pointed at the
    inference box) and the :class:`EventStore`. Wire :meth:`on_alert`
    into :meth:`AlertLog.add_on_record` at boot, AFTER the EventStore
    record callback.
    """

    client: PreprocessorClient
    event_store: EventStore

    window_before_s: float = 4.0
    """Seconds before the event time to include. The motion may have
    begun a beat before HA's sensor latched, and the best-recognized
    frame is often slightly earlier than the snapshot."""

    window_after_s: float = 2.0
    """Seconds after the event time. Covers the snapshot-fetch lag and
    a little follow-through so a turning face / re-id has a chance."""

    _pending_tasks: set[asyncio.Task] = field(default_factory=set)
    """Holds task refs so create_task results aren't GC'd mid-flight."""

    def on_alert(self, alert: dict) -> None:
        """Synchronous entry point — bridge to the async enrich task.

        Called from :meth:`AlertLog.record` (sync, inside the event
        loop). Fires-and-forgets so recording isn't blocked on a
        network round-trip to the inference box.
        """
        camera_id = alert.get("camera_id")
        event_id = alert.get("alert_id")
        if not camera_id or not event_id:
            return
        task = asyncio.create_task(self._enrich(str(event_id), str(camera_id), alert))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _enrich(self, event_id: str, camera_id: str, alert: dict) -> None:
        event_ts = _event_unix_ts(alert)
        if event_ts is None:
            logger.info("enricher.no_event_time", event_id=event_id, camera_id=camera_id)
            return

        fw = await self.client.get_frame_window(
            camera_id=camera_id,
            ts_start=event_ts - self.window_before_s,
            ts_end=event_ts + self.window_after_s,
        )
        if fw is None:
            # Preprocessor unreachable / errored — already logged there.
            # Alert keeps its HA snapshot + rule. Nothing to do.
            return
        if not fw.detections and not fw.identified_entities:
            # Preprocessor saw nothing in the window (camera silent, or
            # this camera isn't ingested there). No enrichment to add.
            logger.info(
                "enricher.empty_window",
                event_id=event_id,
                camera_id=camera_id,
                frames=len(fw.frames),
            )
            return

        annotated = await self._best_annotated_frame(fw)

        ok = self.event_store.record_enrichment(
            event_id,
            detections=[d.model_dump(mode="json") for d in fw.detections],
            identified_entities=[e.model_dump(mode="json") for e in fw.identified_entities],
            actor_matches=[m.model_dump(mode="json") for m in fw.actor_matches],
            annotated_jpeg=annotated,
        )
        logger.info(
            "enricher.recorded",
            event_id=event_id,
            camera_id=camera_id,
            detections=len(fw.detections),
            identities=len(fw.identified_entities),
            annotated=bool(annotated),
            stored=ok,
        )

    async def _best_annotated_frame(self, fw) -> bytes | None:
        """Fetch the annotated JPEG for the most-recognized frame.

        The preprocessor only sets ``annotated_uri`` on frames that
        have ≥1 identified entity (boxes are drawn for recognized
        entities only). Pick the frame with the most identified
        entities — that's the one whose markup best grounds the VLM /
        informs the user — and fetch its annotated bytes. Returns None
        when no frame carries an annotated URI.
        """
        best_ts = _pick_best_frame_ts(fw)
        if best_ts is None:
            return None
        ref = next(
            (f for f in fw.frames if f.ts == best_ts and f.annotated_uri),
            None,
        )
        if ref is None or not ref.annotated_uri:
            return None
        return await self.client.fetch_frame_image(ref.annotated_uri)


def _event_unix_ts(alert: dict) -> float | None:
    """Best event time as a unix epoch, for the frame-window query.

    Prefers ``ha_last_changed`` (when the sensor actually flipped —
    the truest motion time, and what the preprocessor's wall-clock
    frame buffer is keyed on) over ``recorded_at`` (when we wrote the
    alert, a beat later). Both are ISO-8601 strings on the alert.
    """
    for key in ("ha_last_changed", "recorded_at"):
        raw = alert.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw)).timestamp()
        except (ValueError, TypeError):
            continue
    return None


def _pick_best_frame_ts(fw) -> float | None:
    """Frame timestamp with the most identified entities.

    Tie-break by the frame's quality_score when available, else by the
    later timestamp (fresher). Returns None when nothing was
    identified (no annotated frame to fetch)."""
    if not fw.identified_entities:
        return None
    counts: dict[float, int] = {}
    for e in fw.identified_entities:
        counts[e.frame_ts] = counts.get(e.frame_ts, 0) + 1
    quality = {f.ts: (f.quality_score or 0.0) for f in fw.frames}
    return max(counts, key=lambda ts: (counts[ts], quality.get(ts, 0.0), ts))
