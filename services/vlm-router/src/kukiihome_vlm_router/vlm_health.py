"""F7 (local VLM down) health probe — architecture §19.

§19 F7: "VLM process exits / inference times out → circuit breaker opens
→ try next backend → if none, detector-only fallback." The router already
runs a per-backend :class:`CircuitBreaker`; this turns the breaker states
into a :class:`ComponentHealth` the watchdog can observe, so VLM
degradation is detected, logged, alerted, and (via the safe-defaults
matrix) reflected in what the dispatcher will auto-execute.

Mapping (breaker OPEN = backend unusable right now):

* every backend OPEN          -> ``offline``  (no VLM; detector-only)
* some OPEN, ≥1 usable        -> ``degraded`` (fallback chain active)
* all usable                  -> ``ok``

Not system-critical: §19 keeps the system running on rule/detector-only
when VLM is down, so its offline degrades rather than halts (``critical``
stays False; it maps to :data:`FailureMode.F7_VLM_DOWN`, which the matrix
turns into rule-only / conditional actions — not a blanket block).
"""

from __future__ import annotations

import time
from collections.abc import Mapping

from kukiihome_shared.health import ComponentHealth, FailureMode, HealthCheck

from kukiihome_vlm_router.breaker import CircuitBreaker, CircuitState

_COMPONENT = "vlm_router"


def probe_vlm_health(
    breakers: Mapping[str, CircuitBreaker],
    *,
    now: float | None = None,
) -> ComponentHealth:
    """Map per-backend breaker states to one VLM-component health."""
    ts = now if now is not None else time.time()
    if not breakers:
        return ComponentHealth(
            component=_COMPONENT,
            status="offline",
            detail="no VLM backends configured",
            updated_ts=ts,
        )

    # A backend is usable unless its breaker is OPEN (HALF_OPEN allows a
    # probe through, so it still counts as usable).
    open_backends = [name for name, b in breakers.items() if b.state == CircuitState.OPEN]
    total = len(breakers)

    if len(open_backends) == total:
        return ComponentHealth(
            component=_COMPONENT,
            status="offline",
            detail=f"all {total} VLM backend(s) circuit-open; detector-only fallback",
            updated_ts=ts,
        )
    if open_backends:
        return ComponentHealth(
            component=_COMPONENT,
            status="degraded",
            detail=f"{len(open_backends)}/{total} VLM backend(s) circuit-open: "
            + ", ".join(sorted(open_backends)),
            updated_ts=ts,
        )
    return ComponentHealth(
        component=_COMPONENT,
        status="ok",
        detail=f"{total} VLM backend(s) healthy",
        updated_ts=ts,
    )


def make_vlm_health_check(
    breakers_provider: Mapping[str, CircuitBreaker] | object,
    *,
    clock: object = time.time,
) -> HealthCheck:
    """Build a :class:`HealthCheck` for the watchdog.

    ``breakers_provider`` may be the router's ``breakers`` mapping
    directly, or anything exposing a ``breakers`` property (e.g. the
    :class:`~kukiihome_vlm_router.router.VLMRouter`) — read freshly each
    poll so the probe reflects current breaker state, not a stale snapshot.
    """

    async def probe() -> ComponentHealth:
        breakers = getattr(breakers_provider, "breakers", breakers_provider)
        now = clock() if callable(clock) else None
        return probe_vlm_health(breakers, now=now)

    return HealthCheck(name=_COMPONENT, probe=probe, failure_mode=FailureMode.F7_VLM_DOWN)
