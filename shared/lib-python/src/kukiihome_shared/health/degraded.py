"""Active failure-mode tracking + the dispatcher-facing safe-action gate.

The watchdog detects *component* health; this turns that into the set of
*failure modes* currently active, and exposes the §19 safe-action floor
the dispatcher consults before executing a device action.

Flow:

    HealthCheck(failure_mode=F4)  --probe-->  ComponentHealth(failure_mode=F4)
        --watchdog-->  DegradedState.activate(F4)  (on non-ok)
        --dispatcher-->  SafeActionGate.is_allowed("lock")  -> False

``DegradedState`` uses a plain threading lock (not asyncio) because it's
written from the watchdog's event loop but read on the dispatcher's hot
path, which may be sync — a lock keeps the set consistent across both
without forcing the reader to be async.
"""

from __future__ import annotations

import threading

from kukiihome_shared.health.failure_modes import (
    ActionClass,
    FailureMode,
    Permission,
    SafeDefaultsMatrix,
)


class DegradedState:
    """The set of failure modes currently active across the system.

    Driven by the watchdog (a component going non-ok activates its declared
    :class:`FailureMode`; recovery deactivates it) and read by the
    :class:`SafeActionGate`. Safe to share across threads / the event loop.
    """

    def __init__(self) -> None:
        self._active: set[FailureMode] = set()
        self._lock = threading.Lock()

    def activate(self, mode: FailureMode) -> None:
        with self._lock:
            self._active.add(mode)

    def deactivate(self, mode: FailureMode) -> None:
        with self._lock:
            self._active.discard(mode)

    def is_active(self, mode: FailureMode) -> bool:
        with self._lock:
            return mode in self._active

    def active(self) -> frozenset[FailureMode]:
        with self._lock:
            return frozenset(self._active)

    def set_active(self, mode: FailureMode, active: bool) -> None:
        """Activate or deactivate in one call — what the watchdog uses
        per health observation (``active = status != "ok"``)."""
        if active:
            self.activate(mode)
        else:
            self.deactivate(mode)


class SafeActionGate:
    """The deterministic §19 safety floor under the dispatcher's policy gate.

    Combines a :class:`SafeDefaultsMatrix` with the live
    :class:`DegradedState`: "given what's broken right now, is this action
    class still safe to auto-execute?" Returns ``allow`` / ``conditional``
    / ``block`` so the dispatcher can refuse (block) or downgrade
    auto→ask (conditional) without re-deriving the matrix each call.
    """

    def __init__(
        self,
        degraded: DegradedState,
        *,
        matrix: SafeDefaultsMatrix | None = None,
    ) -> None:
        self._degraded = degraded
        self._matrix = matrix if matrix is not None else SafeDefaultsMatrix()

    def permission(self, action: ActionClass) -> Permission:
        return self._matrix.permission(action, self._degraded.active())

    def is_allowed(self, action: ActionClass) -> bool:
        """True only when fully ``allow`` — ``conditional`` is not
        auto-allowed (the caller must check pre-authorization)."""
        return self.permission(action) == "allow"

    def blocked_actions(self) -> tuple[ActionClass, ...]:
        return self._matrix.blocked_actions(self._degraded.active())
