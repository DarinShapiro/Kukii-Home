"""F4 (Home Assistant down) health probe — architecture §19.

§19 F4: HA connection lost → device actions fail, world-state goes stale,
but cameras/VLM/rules/notifications keep working. This turns the HA
client's connection state into a :class:`ComponentHealth` the watchdog
observes, so an HA outage is detected + logged + alerted and (via the
safe-defaults matrix → :data:`FailureMode.F4_HA_DOWN`) device-control
actions are held while notifications stay allowed.

The probe reads connection state through an injected callable rather than
reaching into the client internals, so it's trivially testable and stays
decoupled from the WS-client implementation. Wire it with something like
``make_ha_health_check(lambda: client.is_connected)`` where the daemon
constructs the watchdog.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from kukiihome_shared.health import ComponentHealth, FailureMode, HealthCheck

_COMPONENT = "home_assistant"


def probe_ha_health(
    *,
    connected: bool,
    detail: str | None = None,
    critical: bool = True,
    now: float | None = None,
) -> ComponentHealth:
    """``ok`` when HA's WebSocket is connected, ``offline`` otherwise.

    ``critical`` defaults True — HA is a critical-class dependency (§19
    lists HA-down as a critical alert), so its loss rolls the system to
    ``critical``. (§19 qualifies this as ">60s"; the watchdog's
    persistent-failure escalation already separates a blip from a real
    outage in the diagnostic level — tune ``critical`` at the wiring site
    if a softer rollup is wanted.)
    """
    ts = now if now is not None else time.time()
    if connected:
        return ComponentHealth(
            component=_COMPONENT,
            status="ok",
            detail="HA WebSocket connected",
            updated_ts=ts,
        )
    return ComponentHealth(
        component=_COMPONENT,
        status="offline",
        detail=detail or "HA WebSocket disconnected; device actions unavailable",
        critical=critical,
        updated_ts=ts,
    )


def make_ha_health_check(
    is_connected: Callable[[], bool],
    *,
    critical: bool = True,
    clock: Callable[[], float] = time.time,
) -> HealthCheck:
    """Build a :class:`HealthCheck` for the watchdog. ``is_connected`` is
    read freshly each poll (e.g. ``lambda: client.is_connected``)."""

    async def probe() -> ComponentHealth:
        return probe_ha_health(connected=is_connected(), critical=critical, now=clock())

    return HealthCheck(
        name=_COMPONENT,
        probe=probe,
        critical=critical,
        failure_mode=FailureMode.F4_HA_DOWN,
    )
