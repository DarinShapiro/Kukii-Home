"""Component + system health models and the in-process registry.

The resilience backbone (architecture §19) is built on a simple idea:
every critical component reports a :class:`ComponentHealth`, a
:class:`HealthRegistry` aggregates them, and an overall
:class:`HealthSnapshot` rolls up to one of three system states. The
watchdog (``watchdog.py``) drives reporting; the safe-defaults matrix
(``failure_modes.py``) decides what's still safe to do in a degraded
state; the diagnostic ring (``diagnostics.py``) records the trail.

Kept dependency-free (pydantic only) so any service — core daemon,
ha-agent, preprocessor — can import and report uniformly, and so the
``/health`` surface + the future HA health card read one shape.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# A single component's health. "degraded" = working with reduced
# capability (e.g. RTSP stutter); "offline" = not usable right now.
ComponentStatus = Literal["ok", "degraded", "offline"]

# Rolled-up system health, mirroring §19's "Healthy / Degraded / Critical".
SystemStatus = Literal["healthy", "degraded", "critical"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComponentHealth(_Strict):
    """Health of one monitored component at a point in time.

    Reported by a :class:`~kukiihome_shared.health.watchdog.HealthCheck`
    probe (or directly by a service) into the :class:`HealthRegistry`.
    """

    component: str
    """Stable component id, e.g. ``camera:driveway``, ``home_assistant``,
    ``event_bus``, ``vlm:ollama``."""

    status: ComponentStatus

    detail: str = ""
    """Human-readable context — the error message when offline, the
    degradation reason when degraded, empty when ok."""

    critical: bool = False
    """Whether this component being *offline* makes the WHOLE system
    ``critical`` (§19: bus, HA, storage). Non-critical components only
    degrade the system. Carried on the health so the rollup doesn't need
    a separate criticality table."""

    latency_ms: float | None = None
    """Optional probe latency / observed component latency, for the
    health card (e.g. "Internet: 850ms latency, slow but ok")."""

    updated_ts: float
    """Unix-seconds the observation was made."""


class HealthSnapshot(_Strict):
    """Point-in-time rollup of every component's health.

    Returned by ``HealthRegistry.snapshot`` and served by a service's
    ``/health`` endpoint; the HA integration renders it as the health
    dashboard card (§19 "User-visible health surface")."""

    overall: SystemStatus
    components: tuple[ComponentHealth, ...] = ()
    generated_ts: float = Field(default=0.0)


def overall_status(components: tuple[ComponentHealth, ...]) -> SystemStatus:
    """Roll component health up to a single system status.

    * ``critical`` — any **critical** component is offline (§19: bus /
      HA / storage down are critical-class failures).
    * ``degraded`` — anything else is wrong (a non-critical component
      offline, or any component degraded).
    * ``healthy`` — everything ok.
    """
    critical_offline = any(c.critical and c.status == "offline" for c in components)
    if critical_offline:
        return "critical"
    any_problem = any(c.status != "ok" for c in components)
    return "degraded" if any_problem else "healthy"


class HealthRegistry:
    """Async-safe store of the latest :class:`ComponentHealth` per
    component.

    The watchdog writes (via :meth:`report`); ``/health`` + the HA card
    read (via :meth:`snapshot`). Last-write-wins per ``component`` id.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, ComponentHealth] = {}
        self._lock = asyncio.Lock()

    async def report(self, health: ComponentHealth) -> None:
        async with self._lock:
            self._by_id[health.component] = health

    async def get(self, component: str) -> ComponentHealth | None:
        async with self._lock:
            return self._by_id.get(component)

    async def snapshot(self, *, now: float) -> HealthSnapshot:
        """Build a :class:`HealthSnapshot`. ``now`` is injected (rather
        than read from the clock here) so callers — and tests — control
        the timestamp + so the watchdog can share its own clock."""
        async with self._lock:
            components = tuple(sorted(self._by_id.values(), key=lambda c: c.component))
        return HealthSnapshot(
            overall=overall_status(components),
            components=components,
            generated_ts=now,
        )
