"""Persistent UI-managed notify service selection.

The Web UI's Notifications card writes user-selected notify.* services
here. Lives alongside :mod:`.overrides` (adapter overrides) under
``/data/sentihome/`` so both kinds of user-edited config survive add-on
restarts and updates.

Source-of-truth ordering at boot:

  1. If ``notify_overrides.json`` exists → use it. **Empty list is a
     valid choice** (user unchecked everything) and wins over YAML.
  2. Else if ``topology.notify.alert_services`` (YAML) is non-empty →
     use that as the initial value. First UI save persists it to disk
     and the YAML stops mattering.
  3. Else → no notifications (default).

This means: the YAML config is a one-time seed for users coming from
v0.3.12; everyone else manages notifications from the Web UI.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_NOTIFY_OVERRIDES_PATH = "/data/sentihome/notify_overrides.json"

_SCHEMA_VERSION = 1


def load_notify_services(
    path: str | Path = DEFAULT_NOTIFY_OVERRIDES_PATH,
) -> list[str] | None:
    """Return the UI-selected services list, or None if the file is absent.

    None is meaningful: "no UI choice has been made yet; caller should
    fall back to YAML / defaults." An empty list IS a user choice
    (everything unchecked) — distinct from None.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(
            "notify_overrides.load_failed",
            path=str(p),
            error=str(e),
            hint="ignoring file and falling back to YAML config",
        )
        return None
    if not isinstance(raw, dict):
        return None
    services = raw.get("services")
    if not isinstance(services, list):
        return None
    # Defensive: drop non-string entries.
    return [s for s in services if isinstance(s, str)]


def save_notify_services(
    services: list[str],
    path: str | Path = DEFAULT_NOTIFY_OVERRIDES_PATH,
) -> None:
    """Atomically write the UI-selected services list."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _SCHEMA_VERSION,
        "services": sorted(set(services)),
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    logger.info("notify_overrides.saved", path=str(p), count=len(payload["services"]))


def resolve_initial_services(
    yaml_services: list[str],
    path: str | Path = DEFAULT_NOTIFY_OVERRIDES_PATH,
) -> list[str]:
    """Combine UI overrides with YAML fallback per the documented rules.

    UI file present → UI wins (even if empty). UI file absent →
    YAML is the seed.
    """
    ui = load_notify_services(path)
    if ui is not None:
        return ui
    return list(yaml_services or [])
