"""Failure-mode inventory + the safe-defaults matrix (architecture §19).

When the system is degraded, some actions are still safe to take
automatically and some are not — turning a light on with HA down is
impossible; unlocking a door with no camera context is unsafe. §19
specifies this as a table; this module encodes it verbatim and lets the
dispatcher ask "given the failure modes active right now, is this action
class allowed?".

The matrix is the deterministic safety floor: it sits *under* the normal
policy gate. Even when a rule would auto-execute, an active failure mode
can downgrade ``allow`` → ``conditional`` (require pre-authorization /
ask) or → ``block`` (refuse). Multiple active modes combine to the most
restrictive verdict per action.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Literal


class FailureMode(Enum):
    """The §19 failure-mode inventory (F1-F10).

    Value is ``(code, label)``; access via ``.code`` / ``.label``. Not
    every mode restricts actions — F2 (stutter), F9 (memory pressure),
    F10 (power loss) degrade capability without changing the safe-action
    set, so they're absent from :data:`SAFE_DEFAULTS` (→ no restriction).
    """

    F1_CAMERA_OFFLINE = ("F1", "Camera offline")
    F2_RTSP_STUTTER = ("F2", "RTSP stutter / packet loss")
    F3_DVR_DOWN = ("F3", "NVR / DVR down")
    F4_HA_DOWN = ("F4", "Home Assistant down")
    F5_BUS_DOWN = ("F5", "Event bus down")
    F6_GPU_SATURATED = ("F6", "GPU saturated")
    F7_VLM_DOWN = ("F7", "Local VLM down")
    F8_INTERNET_DOWN = ("F8", "Internet down")
    F9_MEMORY_PRESSURE = ("F9", "Memory pressure")
    F10_POWER_LOSS = ("F10", "Power loss / restart")

    def __init__(self, code: str, label: str) -> None:
        self.code = code
        self.label = label


# Action classes the dispatcher can take, matching the §19 matrix columns.
ActionClass = Literal["lights", "notifications", "lock", "unlock", "siren", "speaker"]

# allow = full capability; block = refuse; conditional = allowed only if a
# rule pre-authorizes, else ask (§19 legend).
Permission = Literal["allow", "block", "conditional"]

_ALL_ACTIONS: tuple[ActionClass, ...] = (
    "lights",
    "notifications",
    "lock",
    "unlock",
    "siren",
    "speaker",
)

# Restrictiveness ordering — combining active modes takes the max.
_RANK: dict[Permission, int] = {"allow": 0, "conditional": 1, "block": 2}


def _row(lights, notifications, lock, unlock, siren, speaker) -> dict[ActionClass, Permission]:
    return {
        "lights": lights,
        "notifications": notifications,
        "lock": lock,
        "unlock": unlock,
        "siren": siren,
        "speaker": speaker,
    }


# The §19 "Safe defaults matrix" (table), verbatim. Modes not listed here
# impose no action restriction (an absent mode contributes ``allow``).
SAFE_DEFAULTS: dict[FailureMode, dict[ActionClass, Permission]] = {
    # Camera/DVR down: no visual context → don't lock/unlock/siren; lights
    # + notifications + speaker still safe.
    FailureMode.F1_CAMERA_OFFLINE: _row("allow", "allow", "block", "block", "block", "allow"),
    FailureMode.F3_DVR_DOWN: _row("allow", "allow", "block", "block", "block", "allow"),
    # HA down: can't control any device; notifications independent of HA.
    FailureMode.F4_HA_DOWN: _row("block", "allow", "block", "block", "block", "block"),
    # Bus down: nothing can be dispatched or queued.
    FailureMode.F5_BUS_DOWN: _row("block", "block", "block", "block", "block", "block"),
    # GPU saturated / VLM down: rule-only operation — lock conditional on a
    # pre-authorizing rule; unlock/siren held; lights/notify/speaker ok.
    FailureMode.F6_GPU_SATURATED: _row("allow", "allow", "conditional", "block", "block", "allow"),
    FailureMode.F7_VLM_DOWN: _row("allow", "allow", "conditional", "block", "block", "allow"),
    # Internet down: fully local, everything still safe.
    FailureMode.F8_INTERNET_DOWN: _row("allow", "allow", "allow", "allow", "allow", "allow"),
}


class SafeDefaultsMatrix:
    """Resolve which action classes are safe given the active failure modes.

    Construct once (default table = §19's :data:`SAFE_DEFAULTS`); query per
    dispatch. Combining multiple active modes takes the **most restrictive**
    verdict per action — a door is only auto-lockable if *every* active
    mode allows it.
    """

    def __init__(
        self, table: dict[FailureMode, dict[ActionClass, Permission]] | None = None
    ) -> None:
        self._table = table if table is not None else SAFE_DEFAULTS

    def permission(self, action: ActionClass, active: Iterable[FailureMode]) -> Permission:
        """Most-restrictive permission for ``action`` across ``active``
        modes. Empty / all-permissive → ``allow``."""
        worst: Permission = "allow"
        for mode in active:
            p = self._table.get(mode, {}).get(action, "allow")
            if _RANK[p] > _RANK[worst]:
                worst = p
        return worst

    def is_allowed(self, action: ActionClass, active: Iterable[FailureMode]) -> bool:
        """True only when the action is fully ``allow`` (``conditional``
        is NOT auto-allowed — the caller must check pre-authorization)."""
        return self.permission(action, active) == "allow"

    def verdicts(self, active: Iterable[FailureMode]) -> dict[ActionClass, Permission]:
        """The full ``action -> permission`` map for the active modes —
        handy for surfacing "what's still safe" on the health card."""
        active = tuple(active)
        return {a: self.permission(a, active) for a in _ALL_ACTIONS}

    def blocked_actions(self, active: Iterable[FailureMode]) -> tuple[ActionClass, ...]:
        active = tuple(active)
        return tuple(a for a in _ALL_ACTIONS if self.permission(a, active) == "block")
