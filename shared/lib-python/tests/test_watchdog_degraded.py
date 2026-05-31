"""The watchdog drives DegradedState from health checks that declare a
failure_mode (the link from detection to the safe-action gate)."""

from __future__ import annotations

import pytest
from kukiihome_shared.health import (
    ComponentHealth,
    DegradedState,
    DiagnosticRing,
    FailureMode,
    HealthCheck,
    HealthRegistry,
    Watchdog,
)


def _probe(statuses: list[str]):
    it = iter(statuses)

    async def probe() -> ComponentHealth:
        s = next(it)
        return ComponentHealth(component="home_assistant", status=s, updated_ts=0.0)

    return probe


def _watchdog(degraded: DegradedState) -> Watchdog:
    return Watchdog(
        registry=HealthRegistry(),
        diagnostics=DiagnosticRing(),
        degraded_state=degraded,
        clock=lambda: 0.0,
    )


@pytest.mark.asyncio
async def test_offline_activates_mode_recovery_clears_it():
    d = DegradedState()
    wd = _watchdog(d)
    wd.register(
        HealthCheck(
            name="home_assistant",
            probe=_probe(["offline", "ok"]),
            failure_mode=FailureMode.F4_HA_DOWN,
        )
    )
    await wd.run_once()
    assert d.is_active(FailureMode.F4_HA_DOWN)
    await wd.run_once()
    assert not d.is_active(FailureMode.F4_HA_DOWN)


@pytest.mark.asyncio
async def test_degraded_status_also_activates_mode():
    d = DegradedState()
    wd = _watchdog(d)
    wd.register(
        HealthCheck(
            name="vlm_router",
            probe=_probe(["degraded"]),
            failure_mode=FailureMode.F7_VLM_DOWN,
        )
    )
    await wd.run_once()
    # Degraded (e.g. GPU saturated / partial VLM) still activates the mode.
    assert d.is_active(FailureMode.F7_VLM_DOWN)


@pytest.mark.asyncio
async def test_check_without_failure_mode_does_not_touch_degraded():
    d = DegradedState()
    wd = _watchdog(d)
    wd.register(HealthCheck(name="preprocessor", probe=_probe(["offline"])))
    await wd.run_once()
    assert d.active() == frozenset()
