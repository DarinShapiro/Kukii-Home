"""HTTP API exposed by ha-agent for the SentiHome custom integration.

The custom_components/sentihome/ HA integration runs inside HA Core. It
needs a network seam to read SentiHome state (alerts, recent events,
system health) and to invoke SentiHome services (acknowledge_alert,
run_optimization, label_person).

This module ships a small starlette app the ha-agent service hosts on
``http://0.0.0.0:8765``. The integration's coordinator polls + subscribes
to it. NATS would be richer but the SentiHome custom component runs in
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
    """In-memory recent-alert cache exposed to the HA integration."""

    max_entries: int = 100
    _entries: list[dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._entries = []

    def record(self, alert: dict[str, Any]) -> None:
        self._entries.append(alert)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]

    def recent(self, limit: int) -> list[dict[str, Any]]:
        return list(self._entries[-limit:])

    def acknowledge(self, alert_id: str, *, feedback: str) -> None:
        for e in self._entries:
            if e.get("alert_id") == alert_id:
                e["acknowledged"] = True
                e["feedback"] = feedback
                return


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
