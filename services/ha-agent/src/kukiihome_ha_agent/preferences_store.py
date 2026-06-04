"""PreferencesStore — household-wide reasoner guidance.

The *Preferences* half of /intent. Single-row global state plus a small
per-actor relationships map. Captures:

  - **vigilance**: low / normal / high — the baseline tilt of every VLM
    judgment. Low → more dismissals, "boring" gets generous treatment.
    High → fewer dismissals, errs toward alerting.
  - **what_i_care_about**: free-text prose the VLM reads as part of every
    prompt. The single largest UX lever for "tell the system what
    matters to me" without authoring rules.
  - **quiet_hours**: time windows where alerts are suppressed (recorded
    still, but no push). Per the memory-model §AttentionMode, "attention"
    areas IGNORE quiet hours — life-safety alerts always fire.
  - **relationships**: per-actor relationship label
    (resident / guest / household / vendor / stranger / unknown) that
    KnownActor reads via access_profile. The reasoner uses this to shape
    expected-pattern judgments.

Single row in SQLite (id=1) for the global prefs — overkill for a JSON
file but matches the rest of the store conventions; the relationships
table is its own row-per-actor table.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

logger = structlog.get_logger(__name__)


Vigilance = Literal["low", "normal", "high"]
Relationship = Literal[
    "resident", "household", "guest", "vendor", "stranger", "unknown"
]


# ─── Dataclass ──────────────────────────────────────────────────────


@dataclass
class Preferences:
    vigilance: Vigilance = "normal"
    what_i_care_about: str = ""
    quiet_hours: list[dict[str, Any]] = field(default_factory=list)
    relationships: dict[str, Relationship] = field(default_factory=dict)
    # ``relationships`` keys are actor_ids; values are the labels above.
    updated_at: float = 0.0


# ─── Store ──────────────────────────────────────────────────────────


class PreferencesStore:
    SCHEMA = (
        """
        CREATE TABLE IF NOT EXISTS preferences (
          id              INTEGER PRIMARY KEY CHECK (id = 1),
          vigilance       TEXT NOT NULL DEFAULT 'normal',
          what_i_care_about TEXT NOT NULL DEFAULT '',
          quiet_hours     TEXT NOT NULL DEFAULT '[]',
          updated_at      REAL NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS actor_relationships (
          actor_id     TEXT PRIMARY KEY,
          relationship TEXT NOT NULL,
          updated_at   REAL NOT NULL
        )
        """,
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
        # Seed the singleton row if missing.
        self._conn.execute(
            "INSERT OR IGNORE INTO preferences (id, updated_at) VALUES (1, ?)",
            (time.time(),),
        )
        self._conn.commit()

    def get(self) -> Preferences:
        row = self._conn.execute(
            "SELECT * FROM preferences WHERE id = 1"
        ).fetchone()
        try:
            quiet = json.loads(row["quiet_hours"] or "[]")
        except (json.JSONDecodeError, TypeError):
            quiet = []
        rel_rows = self._conn.execute(
            "SELECT actor_id, relationship FROM actor_relationships"
        ).fetchall()
        relationships = {r["actor_id"]: r["relationship"] for r in rel_rows}
        return Preferences(
            vigilance=row["vigilance"],
            what_i_care_about=row["what_i_care_about"],
            quiet_hours=quiet,
            relationships=relationships,
            updated_at=row["updated_at"],
        )

    def update(
        self, *,
        vigilance: Vigilance | None = None,
        what_i_care_about: str | None = None,
        quiet_hours: list[dict[str, Any]] | None = None,
    ) -> Preferences:
        cur = self.get()
        if vigilance is not None:
            cur.vigilance = vigilance
        if what_i_care_about is not None:
            cur.what_i_care_about = what_i_care_about
        if quiet_hours is not None:
            cur.quiet_hours = quiet_hours
        cur.updated_at = time.time()
        self._conn.execute(
            "UPDATE preferences SET vigilance=?, what_i_care_about=?, "
            "quiet_hours=?, updated_at=? WHERE id = 1",
            (
                cur.vigilance, cur.what_i_care_about,
                json.dumps(cur.quiet_hours), cur.updated_at,
            ),
        )
        self._conn.commit()
        logger.info("preferences.updated", vigilance=cur.vigilance)
        return cur

    def set_relationship(
        self, actor_id: str, relationship: Relationship,
    ) -> None:
        now = time.time()
        self._conn.execute(
            "INSERT INTO actor_relationships (actor_id, relationship, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(actor_id) DO UPDATE SET "
            "relationship = excluded.relationship, updated_at = excluded.updated_at",
            (actor_id, relationship, now),
        )
        self._conn.commit()

    def clear_relationship(self, actor_id: str) -> None:
        self._conn.execute(
            "DELETE FROM actor_relationships WHERE actor_id = ?", (actor_id,),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
