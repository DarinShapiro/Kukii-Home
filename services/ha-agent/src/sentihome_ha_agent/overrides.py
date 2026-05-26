"""Persistent per-device discovery overrides.

The Web UI's /ha_cameras card writes user choices (enable/disable,
stream, motion sensors, cooldown) here. Read fresh by the reconciler
on every change so overrides take effect immediately.

Lives at ``/data/sentihome/adapter_overrides.json`` — the ``/data``
volume is the Supervisor's persistent add-on storage, so overrides
survive add-on updates and container restarts.

File schema (versioned for future migrations):

  .. code-block:: json

     {
       "version": 1,
       "devices": {
         "front_south": {
           "enabled": true,
           "stream_override": "camera.front_south_camera_fluent",
           "motion_override": ["binary_sensor.front_south_person"],
           "cooldown_override": 15.0
         }
       }
     }

Concurrency: writes are atomic (tempfile + os.replace). The Web UI is
single-process and aiohttp serialises POST handlers per worker, so we
don't need a file lock. If we ever go multi-worker, switch to flock.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


DEFAULT_OVERRIDES_PATH = "/data/sentihome/adapter_overrides.json"

_SCHEMA_VERSION = 1


def load_overrides(path: str | Path = DEFAULT_OVERRIDES_PATH) -> dict[str, dict]:
    """Read the per-device overrides map.

    Returns ``{device_id: {field: value}}`` (the inner dict shape is
    described in :func:`.discovery.build_decisions`).

    On any read or parse failure, logs the error and returns an empty
    dict — discovery falls back to pure AI picks. Never raises: a
    corrupt overrides file should not break the add-on at boot.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(
            "overrides.load_failed",
            path=str(p),
            error=str(e),
            hint="ignoring file and falling back to pure auto-discovery",
        )
        return {}

    if not isinstance(raw, dict):
        logger.warning("overrides.load_unexpected_shape", path=str(p), got=type(raw).__name__)
        return {}

    # Future-proofing: if we bump the schema, do the migration here.
    devices = raw.get("devices")
    if not isinstance(devices, dict):
        return {}
    # Defensive: drop any non-dict entries so callers can rely on shape.
    return {k: v for k, v in devices.items() if isinstance(v, dict)}


def save_overrides(overrides: dict[str, dict], path: str | Path = DEFAULT_OVERRIDES_PATH) -> None:
    """Atomically write the per-device overrides map.

    Creates the parent directory if missing. Writes to a tempfile in
    the same directory, then ``os.replace()`` for atomic swap (POSIX
    guarantees the rename is atomic, so a crash during write can't
    leave a half-written file in place of a good one).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "version": _SCHEMA_VERSION,
        "devices": overrides,
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    # Path.replace is atomic on POSIX (same semantics as os.replace).
    tmp.replace(p)
    logger.info("overrides.saved", path=str(p), devices=len(overrides))


def set_device_override(
    overrides: dict[str, dict],
    device_id: str,
    *,
    enabled: bool | None = None,
    stream_override: str | None = None,
    motion_override: list[str] | None = None,
    cooldown_override: float | None = None,
    clear_stream: bool = False,
    clear_motion: bool = False,
    clear_cooldown: bool = False,
) -> dict[str, dict]:
    """Apply a partial update to a device's override entry.

    Returns the new overrides dict (caller saves it). ``None`` for a
    field means "don't touch"; the ``clear_*`` flags explicitly remove
    a field so the AI pick takes over again.

    Mutates ``overrides`` in place and also returns it for fluent
    chaining in tests.
    """
    entry = dict(overrides.get(device_id, {}))

    if enabled is not None:
        entry["enabled"] = enabled
    if clear_stream:
        entry.pop("stream_override", None)
    elif stream_override is not None:
        entry["stream_override"] = stream_override
    if clear_motion:
        entry.pop("motion_override", None)
    elif motion_override is not None:
        entry["motion_override"] = sorted(motion_override)
    if clear_cooldown:
        entry.pop("cooldown_override", None)
    elif cooldown_override is not None:
        entry["cooldown_override"] = float(cooldown_override)

    # Prune empty entries (a device with no overrides should disappear
    # from the file so the next AI pick isn't accidentally pinned).
    if not entry:
        overrides.pop(device_id, None)
    else:
        overrides[device_id] = entry
    return overrides


def reset_device(overrides: dict[str, dict], device_id: str) -> dict[str, dict]:
    """Drop the override entry for ``device_id`` entirely.

    Use when the user clicks "Reset to AI defaults" in the UI.
    """
    overrides.pop(device_id, None)
    return overrides
