"""Wires the resilience watchdog into the ha-agent + serves /health.

Assembles the §19 resilience backbone for the *ha-agent process* (the
only service the HA Yellow add-on ships) and exposes it over the existing
aiohttp app:

* a :class:`HealthService` bundling the registry / diagnostics /
  degraded-state / watchdog, with the F4 (HA down) check registered;
* ``GET /health``      -> the current :class:`HealthSnapshot` as JSON
  (what the HA integration's health card reads);
* ``GET /diagnostics`` -> the recent failure-trail entries.

Kept out of the 2300-line ``__main__`` so it's independently testable and
so the only edit there is registering the routes + starting the watchdog
task. The VLM (F7) check is NOT wired here — vlm-router runs on the
inference box, not in this add-on image; that process owns its own
watchdog. More ha-agent-local checks (preprocessor reachability, NVR,
camera liveness) register via ``extra_checks`` as they land.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from aiohttp import web
from kukiihome_shared.health import (
    DegradedState,
    DiagnosticEntry,
    DiagnosticRing,
    HealthCheck,
    HealthRegistry,
    HealthSnapshot,
    Watchdog,
)

from kukiihome_ha_agent.health import make_ha_health_check


@dataclass
class HealthService:
    """Everything the resilience surface needs, assembled once at boot."""

    registry: HealthRegistry
    diagnostics: DiagnosticRing
    degraded_state: DegradedState
    watchdog: Watchdog
    clock: Callable[[], float] = time.time
    _checks: list[HealthCheck] = field(default_factory=list)

    async def run(self) -> None:
        """Drive the watchdog poll loop (long-lived task)."""
        await self.watchdog.run()

    async def poll_once(self) -> None:
        """Single poll — the testable unit, and a useful warm-up at boot."""
        await self.watchdog.run_once()


def build_health_service(
    *,
    is_connected: Callable[[], bool],
    extra_checks: Sequence[HealthCheck] = (),
    clock: Callable[[], float] = time.time,
    poll_interval_s: float = 10.0,
    ha_critical: bool = True,
) -> HealthService:
    """Assemble the ha-agent health service.

    ``is_connected`` is the HA WebSocket liveness callable (pass
    ``lambda: client.is_connected``). ``extra_checks`` lets the caller
    register additional ha-agent-local probes without this module
    importing them.
    """
    registry = HealthRegistry()
    diagnostics = DiagnosticRing()
    degraded = DegradedState()
    watchdog = Watchdog(
        registry=registry,
        diagnostics=diagnostics,
        degraded_state=degraded,
        poll_interval_s=poll_interval_s,
        clock=clock,
    )
    checks: list[HealthCheck] = [
        make_ha_health_check(is_connected, critical=ha_critical, clock=clock),
        *extra_checks,
    ]
    for check in checks:
        watchdog.register(check)
    return HealthService(
        registry=registry,
        diagnostics=diagnostics,
        degraded_state=degraded,
        watchdog=watchdog,
        clock=clock,
        _checks=checks,
    )


# ─── JSON payloads (pure; handlers wrap these) ──────────────────────


def health_payload(snapshot: HealthSnapshot) -> dict:
    """Serialize a :class:`HealthSnapshot` to the /health JSON shape."""
    return {
        "overall": snapshot.overall,
        "generated_ts": snapshot.generated_ts,
        "components": [
            {
                "component": c.component,
                "status": c.status,
                "detail": c.detail,
                "critical": c.critical,
                "latency_ms": c.latency_ms,
                "updated_ts": c.updated_ts,
            }
            for c in snapshot.components
        ],
    }


def diagnostics_payload(entries: Sequence[DiagnosticEntry]) -> dict:
    """Serialize recent diagnostic entries to the /diagnostics JSON shape."""
    return {
        "count": len(entries),
        "entries": [e.model_dump() for e in entries],
    }


# ─── aiohttp wiring ─────────────────────────────────────────────────


def attach_health_routes(app: web.Application, service: HealthService) -> None:
    """Register ``GET /health`` + ``GET /diagnostics`` on the app.

    ``/diagnostics`` accepts ``?limit=N`` (default 100) and
    ``?level=warning|critical`` to filter the trail.
    """

    async def health(_request: web.Request) -> web.Response:
        snapshot = await service.registry.snapshot(now=service.clock())
        return web.json_response(health_payload(snapshot))

    async def diagnostics(request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "100"))
        except ValueError:
            limit = 100
        level = request.query.get("level")
        min_level = level if level in ("info", "warning", "critical") else None
        entries = await service.diagnostics.recent(limit=limit, min_level=min_level)
        return web.json_response(diagnostics_payload(entries))

    app.router.add_get("/health", health)
    app.router.add_get("/diagnostics", diagnostics)
