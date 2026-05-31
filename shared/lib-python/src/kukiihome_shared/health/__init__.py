"""Resilience backbone (architecture §19): health, safe defaults, watchdog.

The framework every failure-mode handler (F1-F10) plugs into:

* :class:`ComponentHealth` / :class:`HealthSnapshot` / :class:`HealthRegistry`
  — uniform health reporting + rollup, served by ``/health`` and the HA
  health card.
* :class:`FailureMode` + :class:`SafeDefaultsMatrix` — the deterministic
  "what's still safe to do" floor under the dispatcher's policy gate.
* :class:`DiagnosticEntry` + :class:`DiagnosticRing` — the queryable
  last-N failure trail.
* :class:`HealthCheck` + :class:`Watchdog` — periodic probing with
  transition detection, diagnostic recording, and operator-alert hooks.

F-mode handlers register a :class:`HealthCheck` (detection + recovery)
with the watchdog and consult :class:`SafeDefaultsMatrix` before acting.
"""

from kukiihome_shared.health.diagnostics import (
    DiagnosticEntry,
    DiagnosticLevel,
    DiagnosticRing,
)
from kukiihome_shared.health.failure_modes import (
    SAFE_DEFAULTS,
    ActionClass,
    FailureMode,
    Permission,
    SafeDefaultsMatrix,
)
from kukiihome_shared.health.models import (
    ComponentHealth,
    ComponentStatus,
    HealthRegistry,
    HealthSnapshot,
    SystemStatus,
    overall_status,
)
from kukiihome_shared.health.watchdog import (
    HealthCheck,
    HealthProbe,
    TransitionCallback,
    Watchdog,
)

__all__ = [
    "SAFE_DEFAULTS",
    "ActionClass",
    "ComponentHealth",
    "ComponentStatus",
    "DiagnosticEntry",
    "DiagnosticLevel",
    "DiagnosticRing",
    "FailureMode",
    "HealthCheck",
    "HealthProbe",
    "HealthRegistry",
    "HealthSnapshot",
    "Permission",
    "SafeDefaultsMatrix",
    "SystemStatus",
    "TransitionCallback",
    "Watchdog",
    "overall_status",
]
