"""AreaStore — conceptual zones that group cameras.

An **area** is a household concept the reasoner uses to shape its judgment
(Pool, Driveway, Front porch, Backyard). Each area carries:

  - **AttentionMode** (memory model §AttentionMode) — vigilance posture:
    ``normal``     : default — VLM only fires on triage-eligible events
    ``attention``  : life-safety zones (pool, child's room, fall-risk
                     spaces). Continuous specialized monitoring bypasses
                     the triage queue and runs at 2-4 fps. The VLM
                     enriches *after* an alert fires, not before — it
                     does not gate.
    ``unattended`` : opt-in suppression — area's events are dismissed
                     without VLM, with only motion + classification
                     logged. For quiet indoor zones the user doesn't
                     want reasoning on by default.
  - **normal-hours** — time windows the area is expected to be active.
    Outside-hours activity gets a confidence boost in reasoning. Same
    JSON shape as :mod:`rules_store` time_windows for reuse.
  - **role** — privacy posture: ``public`` (faces public, e.g. driveway),
    ``shared`` (visible by household), ``private`` (bedroom). Optional;
    when set, narrows VLM persona prompts and capture/retention defaults.

Cameras → areas is a many-to-many via the ``area_cameras`` join table so
a hallway camera can sit in two areas (Living + Entry, say).

Co-located with ``rules.db`` / ``actions.db`` under ``/data/kukiihome/``.
Soft-delete via ``retired_at`` matches the rules pattern so the activity
trace can still resolve area_ids referenced in old events.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

logger = structlog.get_logger(__name__)


AttentionMode = Literal["normal", "attention", "unattended"]
AreaRole = Literal["public", "shared", "private"]


# ─── Dataclasses ────────────────────────────────────────────────────


@dataclass
class Area:
    id: str
    name: str
    attention_mode: AttentionMode = "normal"
    role: AreaRole | None = None
    description: str = ""
    normal_hours: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    retired_at: float | None = None
    cameras: list[str] = field(default_factory=list)  # populated by store reads


# ─── Slug derivation ────────────────────────────────────────────────


_SLUG_TRIM = re.compile(r"[^a-z0-9]+")


def slug_for(name: str) -> str:
    s = _SLUG_TRIM.sub("_", (name or "").lower()).strip("_")
    return s or f"area_{uuid.uuid4().hex[:8]}"


# ─── Store ──────────────────────────────────────────────────────────


class AreaStore:
    SCHEMA = (
        """
        CREATE TABLE IF NOT EXISTS areas (
          id              TEXT PRIMARY KEY,
          name            TEXT NOT NULL,
          attention_mode  TEXT NOT NULL DEFAULT 'normal',
          role            TEXT,
          description     TEXT NOT NULL DEFAULT '',
          normal_hours    TEXT NOT NULL DEFAULT '[]',
          created_at      REAL NOT NULL,
          updated_at      REAL NOT NULL,
          retired_at      REAL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS area_cameras (
          area_id     TEXT NOT NULL,
          camera_id   TEXT NOT NULL,
          PRIMARY KEY (area_id, camera_id),
          FOREIGN KEY (area_id) REFERENCES areas(id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_area_cam_camera ON area_cameras(camera_id)",
    )

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
        else:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        for stmt in self.SCHEMA:
            self._conn.execute(stmt)
        self._conn.commit()

    # ── Reads ─────────────────────────────────────────────────────

    def _row_to_area(self, row: sqlite3.Row) -> Area:
        try:
            hours = json.loads(row["normal_hours"] or "[]")
        except json.JSONDecodeError:
            hours = []
        cams = [
            r["camera_id"] for r in self._conn.execute(
                "SELECT camera_id FROM area_cameras WHERE area_id = ? "
                "ORDER BY camera_id",
                (row["id"],),
            ).fetchall()
        ]
        return Area(
            id=row["id"], name=row["name"],
            attention_mode=row["attention_mode"], role=row["role"],
            description=row["description"], normal_hours=hours,
            created_at=row["created_at"], updated_at=row["updated_at"],
            retired_at=row["retired_at"], cameras=cams,
        )

    def get(self, area_id: str) -> Area | None:
        row = self._conn.execute(
            "SELECT * FROM areas WHERE id = ?", (area_id,)
        ).fetchone()
        return self._row_to_area(row) if row else None

    def all_areas(self, *, include_retired: bool = False) -> list[Area]:
        sql = "SELECT * FROM areas"
        if not include_retired:
            sql += " WHERE retired_at IS NULL"
        sql += " ORDER BY name"
        return [self._row_to_area(r) for r in self._conn.execute(sql).fetchall()]

    def area_for_camera(self, camera_id: str) -> list[Area]:
        """Reverse-lookup: which areas does this camera belong to?
        Used by the Cameras detail page's Identity & role line."""
        rows = self._conn.execute(
            "SELECT a.* FROM areas a JOIN area_cameras ac ON a.id = ac.area_id "
            "WHERE ac.camera_id = ? AND a.retired_at IS NULL "
            "ORDER BY a.name",
            (camera_id,),
        ).fetchall()
        return [self._row_to_area(r) for r in rows]

    # ── Writes ────────────────────────────────────────────────────

    def create(self, area: Area) -> Area:
        now = time.time()
        if not area.id:
            area.id = slug_for(area.name)
        if self.get(area.id) is not None:
            area.id = f"{area.id}_{uuid.uuid4().hex[:6]}"
        area.created_at = area.created_at or now
        area.updated_at = now
        self._conn.execute(
            "INSERT INTO areas (id, name, attention_mode, role, description, "
            "normal_hours, created_at, updated_at, retired_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                area.id, area.name, area.attention_mode, area.role,
                area.description, json.dumps(area.normal_hours),
                area.created_at, area.updated_at, area.retired_at,
            ),
        )
        for cid in area.cameras:
            self._conn.execute(
                "INSERT OR IGNORE INTO area_cameras (area_id, camera_id) "
                "VALUES (?, ?)", (area.id, cid),
            )
        self._conn.commit()
        logger.info("areas.created", area_id=area.id, name=area.name)
        return self.get(area.id) or area

    def update(self, area_id: str, **fields: Any) -> Area | None:
        area = self.get(area_id)
        if area is None:
            return None
        allowed = {"name", "attention_mode", "role", "description"}
        for k, v in fields.items():
            if k in allowed:
                setattr(area, k, v)
        if "normal_hours" in fields:
            area.normal_hours = fields["normal_hours"] or []
        area.updated_at = time.time()
        self._conn.execute(
            "UPDATE areas SET name=?, attention_mode=?, role=?, description=?, "
            "normal_hours=?, updated_at=? WHERE id=?",
            (
                area.name, area.attention_mode, area.role, area.description,
                json.dumps(area.normal_hours), area.updated_at, area_id,
            ),
        )
        if "cameras" in fields:
            self._conn.execute(
                "DELETE FROM area_cameras WHERE area_id = ?", (area_id,),
            )
            for cid in (fields["cameras"] or []):
                self._conn.execute(
                    "INSERT OR IGNORE INTO area_cameras (area_id, camera_id) "
                    "VALUES (?, ?)", (area_id, cid),
                )
        self._conn.commit()
        logger.info("areas.updated", area_id=area_id, fields=list(fields.keys()))
        return self.get(area_id)

    def soft_delete(self, area_id: str) -> Area | None:
        now = time.time()
        self._conn.execute(
            "UPDATE areas SET retired_at=?, updated_at=? WHERE id=?",
            (now, now, area_id),
        )
        self._conn.commit()
        logger.info("areas.retired", area_id=area_id)
        return self.get(area_id)

    def close(self) -> None:
        self._conn.close()
