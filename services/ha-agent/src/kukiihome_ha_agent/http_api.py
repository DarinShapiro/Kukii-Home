"""HTTP API exposed by ha-agent for the Kukii-Home custom integration.

The custom_components/kukiihome/ HA integration runs inside HA Core. It
needs a network seam to read Kukii-Home state (alerts, recent events,
system health) and to invoke Kukii-Home services (acknowledge_alert,
run_optimization, label_person).

This module ships a small starlette app the ha-agent service hosts on
``http://0.0.0.0:8765``. The integration's coordinator polls + subscribes
to it. NATS would be richer but the Kukii-Home custom component runs in
HA's restricted Python sandbox where adding NATS as a dep is awkward; an
HTTP loopback (or LAN call) is the simplest contract.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class APIRoute:
    method: str
    path: str
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class HAAgentAPI:
    """Thin async HTTP API.

    Routes (all JSON in + JSON out):
      GET  /healthz                   → {"ok": True}
      GET  /snapshot                  → {entity_id: state, ...}
      GET  /capabilities              → [{domain, count, samples}, ...]
      POST /service                   → {ok: True, called: [...]}
      POST /acknowledge_alert         → {ok: True}
      GET  /recent_alerts?limit=20    → [...]

    Designed to be host-agnostic; a real server-side runtime (aiohttp /
    starlette) wraps :meth:`dispatch` in :mod:`__main__`. Keeping the
    dispatch surface in-process means unit tests don't need a network
    socket.
    """

    def __init__(self, *, tools, alert_log) -> None:
        self._tools = tools
        self._alert_log = alert_log

    async def dispatch(
        self, *, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        body = body or {}
        try:
            if method == "GET" and path == "/healthz":
                return 200, {"ok": True}

            if method == "GET" and path == "/snapshot":
                states = await self._tools.get_snapshot()
                return 200, {
                    "entities": [
                        {
                            "entity_id": s.entity_id,
                            "state": s.state,
                            "attributes": s.attributes,
                        }
                        for s in states
                    ]
                }

            if method == "GET" and path == "/capabilities":
                caps = await self._tools.list_capabilities()
                return 200, {
                    "capabilities": [
                        {"domain": c.domain, "count": c.entity_count, "samples": c.sample_entities}
                        for c in caps
                    ]
                }

            if method == "GET" and path == "/ha_cameras":
                discovery = await self._tools.discover_ha_cameras()
                return 200, {
                    "cameras": [
                        {
                            "camera_entity": c.camera_entity,
                            "friendly_name": c.friendly_name,
                            "state": c.state,
                            "motion_candidates": c.motion_candidates,
                        }
                        for c in discovery.cameras
                    ],
                    "unmatched_motion_sensors": discovery.unmatched_motion_sensors,
                }

            if method == "POST" and path == "/service":
                domain = body.get("domain")
                service = body.get("service")
                entity_id = body.get("entity_id")
                data = body.get("data") or {}
                if not domain or not service:
                    return 400, {"error": "domain + service required"}
                result = await self._tools.call_service(
                    domain, service, entity_id=entity_id, data=data
                )
                return 200, {"ok": True, "result": result}

            if method == "POST" and path == "/acknowledge_alert":
                alert_id = body.get("alert_id")
                if not alert_id:
                    return 400, {"error": "alert_id required"}
                self._alert_log.acknowledge(alert_id, feedback=body.get("feedback", "correct"))
                return 200, {"ok": True}

            if method == "GET" and path == "/recent_alerts":
                limit = int(body.get("limit", 20))
                return 200, {"alerts": self._alert_log.recent(limit)}

            return 404, {"error": f"no route for {method} {path}"}
        except Exception as e:
            logger.exception("ha_agent.api_dispatch_failed", path=path)
            return 500, {"error": str(e)}


@dataclass
class AlertLog:
    """Recent-alert cache exposed to the HA integration.

    Optionally persists to disk so alerts survive add-on restarts. The
    persist file is written atomically (tempfile + rename) on every
    mutation. Read once at construction; in-memory list is the working
    copy.

    persist_path = None → pure in-memory (default; used in tests).
    persist_path = "/data/kukiihome/alerts.json" → durable.
    """

    max_entries: int = 100
    persist_path: str | None = None
    """When set, alerts are loaded from this path on startup and re-
    saved after every :meth:`record` / :meth:`acknowledge` call. Use
    a path under ``/data`` so the file survives add-on updates."""

    _entries: list[dict[str, Any]] = None  # type: ignore[assignment]
    _on_record: list[Callable[[dict[str, Any]], None]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._entries = []
        self._on_record = []
        if self.persist_path:
            self._load_from_disk()

    def add_on_record(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register a synchronous callback fired after each :meth:`record`.

        Used to plumb HA notifications (v0.3.12) without making AlertLog
        directly depend on the HAClient. Callbacks run synchronously
        in the recording context; if they need to do async work, they
        should ``asyncio.create_task(...)`` and return.

        Exceptions in callbacks are caught + logged — a broken
        notifier should never block alert recording.

        Idempotent: registering the same callback twice is a no-op, so
        an accidental double-wire (e.g. a re-bind on reconnect) can't
        silently multiply notifications per alert.
        """
        if callback in self._on_record:
            return
        self._on_record.append(callback)

    def record(self, alert: dict[str, Any]) -> None:
        # Always stamp a `recorded_at` ISO-8601 timestamp so the Web UI
        # can display when each alert fired without having to derive it
        # from alert_id formatting.
        if "recorded_at" not in alert:
            from datetime import UTC, datetime

            alert["recorded_at"] = datetime.now(UTC).isoformat()
        self._entries.append(alert)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]
        self._save_to_disk()
        # Fire callbacks AFTER persistence so subscribers see the same
        # alert that's now on disk + visible to other readers.
        for cb in self._on_record:
            try:
                cb(alert)
            except Exception as e:
                logger.warning("alert_log.on_record_callback_failed", error=str(e))

    def recent(self, limit: int) -> list[dict[str, Any]]:
        return list(self._entries[-limit:])

    def get(self, alert_id: str) -> dict[str, Any] | None:
        """Look up a specific alert by id. Returns None if no match."""
        for e in self._entries:
            if e.get("alert_id") == alert_id:
                return e
        return None

    def acknowledge(self, alert_id: str, *, feedback: str) -> None:
        for e in self._entries:
            if e.get("alert_id") == alert_id:
                e["acknowledged"] = True
                e["feedback"] = feedback
                self._save_to_disk()
                return

    def set_triage(
        self,
        alert_id: str,
        *,
        status: str,
        explanation: str,
        criticality: str,
    ) -> None:
        """Fold the triage/VLM outcome back onto a recorded alert.

        Called by :class:`~kukiihome_ha_agent.triage.TriageGate` after
        reasoning. ``status`` is ``alerted`` or ``dismissed`` (drives the
        Recent-alerts Status column); ``explanation`` + ``criticality``
        let the list show *why* an event was silenced without opening it.
        No-op for unknown ids (alert may have aged out of the ring).
        """
        for e in self._entries:
            if e.get("alert_id") == alert_id:
                e["triage_status"] = status
                e["triage_explanation"] = explanation
                e["criticality"] = criticality
                self._save_to_disk()
                return

    # ─── persistence ────────────────────────────────────────────────

    def _save_to_disk(self) -> None:
        if not self.persist_path:
            return
        from pathlib import Path

        try:
            p = Path(self.persist_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(json.dumps(self._entries), encoding="utf-8")
            tmp.replace(p)
        except OSError as e:
            # Don't let a transient disk error break alert recording.
            # The in-memory copy is still authoritative.
            logger.warning("alert_log.persist_failed", path=self.persist_path, error=str(e))

    def _load_from_disk(self) -> None:
        if not self.persist_path:
            return
        from pathlib import Path

        p = Path(self.persist_path)
        if not p.exists():
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                # Trim to max_entries in case the file's larger than
                # the current cap (e.g. cap was reduced post-upgrade).
                self._entries = [e for e in raw if isinstance(e, dict)][-self.max_entries :]
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "alert_log.load_failed",
                path=self.persist_path,
                error=str(e),
                hint="starting with empty alert history",
            )


def make_ha_caller(client) -> Callable:
    """Build the ``HACaller`` notify dispatchers use, backed by an HAClient."""

    async def caller(service: str, data: dict[str, Any]) -> dict[str, Any]:
        # service is a full "domain.service" string (e.g. "notify.mobile_app_x").
        if "." not in service:
            raise ValueError(f"expected 'domain.service', got {service!r}")
        domain, service_name = service.split(".", 1)
        return await client.call_service(domain, service_name, data=data)

    return caller


def stringify_json(payload: dict[str, Any]) -> str:
    """Convenience encoder for the HTTP server wrapper."""
    return json.dumps(payload)
