"""Build SystemViewModel from live state (Part IX §30).

Disk scanner + counter assembly that the route handler hands to the
``render_system_page`` renderer. Tolerant of missing directories
(returns empty rows) — the page renders even when /data/kukiihome
doesn't exist yet (fresh install, test runs)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from kukiihome_ha_agent.web_ui.system import (
    StorageClassRow,
    SystemViewModel,
)

# Paths checked under /data/kukiihome (or a test-provided alt).
DEFAULT_DATA_ROOT = "/data/kukiihome"

# (label, glob pattern relative to root, fallback detail formatter)
_STORE_DBS = [
    ("rules.db", "rules.db"),
    ("actions.db", "actions.db"),
    ("areas.db", "areas.db"),
    ("preferences.db", "preferences.db"),
    ("policies.db", "policies.db"),
    ("sessions.db", "sessions.db"),
    ("retention.db", "retention.db"),
]


def _safe_stat_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except (OSError, ValueError):
        return 0


def _count_glob_bytes(root: Path, pattern: str) -> tuple[int, int]:
    """Return (count, total_bytes) for files matching the glob."""
    if not root.exists():
        return 0, 0
    total = 0
    n = 0
    try:
        for p in root.glob(pattern):
            if p.is_file():
                n += 1
                total += _safe_stat_bytes(p)
    except OSError:
        return 0, 0
    return n, total


def _stores_row(root: Path) -> StorageClassRow:
    total = 0
    present = 0
    detail_parts: list[str] = []
    for label, fname in _STORE_DBS:
        f = root / fname
        size = _safe_stat_bytes(f)
        if size:
            present += 1
            total += size
            detail_parts.append(f"{label}: {size // 1024} KB")
    detail = " · ".join(detail_parts[:5]) + (
        f" · +{len(detail_parts) - 5} more"
        if len(detail_parts) > 5 else ""
    )
    return StorageClassRow(
        label="Stores (SQLite)",
        count=present, bytes_used=total, detail=detail,
    )


def _events_row(root: Path) -> StorageClassRow:
    n, sz = _count_glob_bytes(root / "events", "**/event.json")
    return StorageClassRow(
        label="Episodic events",
        count=n, bytes_used=sz,
        detail=f"under {root / 'events'}" if n else "",
    )


def _frames_row(root: Path) -> StorageClassRow:
    n, sz = _count_glob_bytes(root / "events", "**/*.jpg")
    return StorageClassRow(
        label="Frame snapshots",
        count=n, bytes_used=sz,
        detail=f"under {root / 'events'}" if n else "",
    )


def _clips_row(root: Path) -> StorageClassRow:
    n_mp4, sz_mp4 = _count_glob_bytes(root / "events", "**/clip.mp4")
    n_gif, sz_gif = _count_glob_bytes(root / "events", "**/clip.gif")
    return StorageClassRow(
        label="Clip files",
        count=n_mp4 + n_gif, bytes_used=sz_mp4 + sz_gif,
        detail=(
            f"{n_mp4} mp4 ({sz_mp4 // 1024 ** 2} MB) · "
            f"{n_gif} gif ({sz_gif // 1024 ** 2} MB)"
            if n_mp4 + n_gif else ""
        ),
    )


def build_system_vm(
    *, data_root: str | None = None,
    policy: Any | None = None,
    audit_log: list[Any] | None = None,
    cameras: list[tuple[str, str]] | None = None,
    now_ts: float | None = None,
) -> SystemViewModel:
    root = Path(data_root or DEFAULT_DATA_ROOT)
    rows = [
        _stores_row(root),
        _events_row(root),
        _frames_row(root),
        _clips_row(root),
    ]
    total = sum(r.bytes_used for r in rows)
    return SystemViewModel(
        storage_rows=rows, total_bytes=total,
        policy=policy, audit_log=audit_log or [],
        cameras=cameras or [],
        now_ts=now_ts if now_ts is not None else time.time(),
    )
