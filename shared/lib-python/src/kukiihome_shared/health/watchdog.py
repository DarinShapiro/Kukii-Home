"""The watchdog — polls component health, records transitions (§19).

§19's principle is "degrade, don't fail": every failure must be detected,
logged, alerted, and recovered. The watchdog is the detection + logging
half. It periodically runs a set of :class:`HealthCheck` probes, reports
each result into the :class:`HealthRegistry`, and — on a *transition*
(ok→degraded/offline or recovery back to ok) — writes a
:class:`DiagnosticEntry` and fires the operator-alert callback.

Recovery (reconnect loops, process restarts) lives in the per-failure-mode
handlers (F1-F10) that register their probes here; the watchdog's job is
to make state changes observable and alertable. Persistent failures
(N consecutive offline observations) escalate the diagnostic to
``critical`` so a flaky blip and a real outage read differently.

Clock is injected (``clock``) so the loop is testable without real time —
the same pattern the memory harness + DAG simulator use.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from kukiihome_shared.health.diagnostics import DiagnosticEntry, DiagnosticLevel, DiagnosticRing
from kukiihome_shared.health.models import ComponentHealth, ComponentStatus, HealthRegistry

if TYPE_CHECKING:
    from kukiihome_shared.health.degraded import DegradedState
    from kukiihome_shared.health.failure_modes import FailureMode

logger = structlog.get_logger(__name__)

# Probe returns the component's current health. May raise — the watchdog
# treats an exception as "offline" with the error as detail.
HealthProbe = Callable[[], Awaitable[ComponentHealth]]

# Operator-alert hook, fired once per transition with the diagnostic that
# was recorded. Sync or async; the watchdog awaits coroutines.
TransitionCallback = Callable[[DiagnosticEntry], Awaitable[None] | None]


@dataclass
class HealthCheck:
    """A registered probe for one component."""

    name: str
    probe: HealthProbe
    critical: bool = False
    """Marks the component critical (its offline → system ``critical`` +
    a ``critical`` diagnostic). Stamped onto the reported health if the
    probe didn't already set it."""

    persistent_failure_threshold: int = 3
    """Consecutive offline observations before the diagnostic escalates
    from ``warning`` to ``critical`` (a transient blip vs. a real outage).
    Critical components escalate immediately."""

    failure_mode: FailureMode | None = None
    """The §19 failure mode this component represents (e.g. F4 for
    home_assistant, F7 for vlm_router). When set + the watchdog has a
    :class:`DegradedState`, a non-ok observation activates this mode and a
    recovery clears it — so the safe-defaults gate sees what's broken.
    ``None`` for components that don't map to an action-restricting mode
    (e.g. the preprocessor: its loss skips enrichment, not actions)."""


@dataclass
class Watchdog:
    """Polls health checks and records transitions.

    Use :meth:`run_once` to poll all checks a single time (the testable
    unit); :meth:`run` loops it on ``poll_interval_s`` until cancelled.
    """

    registry: HealthRegistry
    diagnostics: DiagnosticRing
    on_transition: TransitionCallback | None = None
    poll_interval_s: float = 10.0
    clock: Callable[[], float] = time.time
    degraded_state: DegradedState | None = None

    _checks: list[HealthCheck] = field(default_factory=list)
    _last_status: dict[str, ComponentStatus] = field(default_factory=dict)
    _consecutive_offline: dict[str, int] = field(default_factory=dict)

    def register(self, check: HealthCheck) -> None:
        self._checks.append(check)

    async def run_once(self) -> None:
        """Run every registered probe once; report + process transitions."""
        for check in self._checks:
            health = await self._probe(check)
            await self.registry.report(health)
            await self._process(check, health)

    async def run(self) -> None:
        """Loop :meth:`run_once` every ``poll_interval_s`` until cancelled."""
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the watchdog die on a probe bug
                logger.exception("watchdog.run_once_failed")
            await asyncio.sleep(self.poll_interval_s)

    async def _probe(self, check: HealthCheck) -> ComponentHealth:
        try:
            health = await check.probe()
        except Exception as e:
            return ComponentHealth(
                component=check.name,
                status="offline",
                detail=f"probe error: {e}",
                critical=check.critical,
                updated_ts=self.clock(),
            )
        # Honor the check's criticality if the probe didn't assert it.
        if check.critical and not health.critical:
            health = health.model_copy(update={"critical": True})
        return health

    async def _process(self, check: HealthCheck, health: ComponentHealth) -> None:
        # First observation baselines to "ok" so a first-seen failure still
        # registers as a transition worth logging.
        previous = self._last_status.get(check.name, "ok")
        self._last_status[check.name] = health.status

        # Reflect this component's mode in the live degraded set every
        # observation (not just on transitions) so the safe-action gate
        # stays correct even if a transition's callback is missed.
        if self.degraded_state is not None and check.failure_mode is not None:
            self.degraded_state.set_active(check.failure_mode, health.status != "ok")

        if health.status == "offline":
            self._consecutive_offline[check.name] = self._consecutive_offline.get(check.name, 0) + 1
        else:
            self._consecutive_offline[check.name] = 0

        if health.status == previous:
            return  # steady state — nothing to record

        entry = self._build_entry(check, health, previous)
        await self.diagnostics.record(entry)
        logger.info(
            "watchdog.transition",
            component=check.name,
            **{"from": previous},
            to=health.status,
            level=entry.level,
        )
        if self.on_transition is not None:
            result = self.on_transition(entry)
            if asyncio.iscoroutine(result):
                await result

    def _build_entry(
        self, check: HealthCheck, health: ComponentHealth, previous: ComponentStatus
    ) -> DiagnosticEntry:
        if health.status == "ok":
            return DiagnosticEntry(
                ts=self.clock(),
                level="info",
                component=check.name,
                message=f"{check.name} recovered (was {previous})",
                recovery="resumed normal operation",
            )

        level: DiagnosticLevel = "warning"
        if health.status == "offline":
            persistent = (
                self._consecutive_offline.get(check.name, 0) >= check.persistent_failure_threshold
            )
            if check.critical or persistent:
                level = "critical"
        return DiagnosticEntry(
            ts=self.clock(),
            level=level,
            component=check.name,
            message=f"{check.name} {health.status}: {health.detail}".rstrip(": "),
            impact=health.detail or None,
        )
