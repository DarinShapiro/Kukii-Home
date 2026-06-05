"""RetentionStore — per-class retention policy + admin audit log.

Part IX §30. Two tables sharing one SQLite database:

  - **retention_policy** — singleton row holding the global knobs:
    days + size caps for episodic events, frame snapshots, audit logs.
    Identity embeddings are never auto-pruned and don't appear here.
  - **admin_audit** — every privacy / storage operation gets a row.
    Timestamp + actor + operation + scope + bytes_removed.

Co-located with the other Kukii stores under /data/kukiihome/. The
retention policy values are *advisory* in v1 — enforcement (the
nightly pruning job) lands as a separate worker that reads this store
on each tick. For v1 we ship the policy editor + the audit log,
which is enough for the trust contract ("if I want to know what got
deleted, this page shows it").
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


# Defaults match Part IX §30 Card 2 — events 90d/10GB, frames 14d, audit 365d.
DEFAULT_EVENTS_DAYS = 90
DEFAULT_EVENTS_MAX_GB = 10
DEFAULT_FRAMES_DAYS = 14
DEFAULT_AUDIT_DAYS = 365


@dataclass
class RetentionPolicy:
    events_days: int = DEFAULT_EVENTS_DAYS
    events_max_gb: int = DEFAULT_EVENTS_MAX_GB
    frames_days: int = DEFAULT_FRAMES_DAYS
    audit_days: int = DEFAULT_AUDIT_DAYS
    updated_at: float = 0.0


@dataclass
class AdminAudit:
    id: int | None
    ts: float
    actor: str
    operation: str
    scope: str  # JSON-ish description of what was scoped
    bytes_removed: int = 0
    rows_removed: int = 0
    notes: str = ""


class RetentionStore:
    SCHEMA = (
        """
        CREATE TABLE IF NOT EXISTS retention_policy (
          id              INTEGER PRIMARY KEY CHECK (id = 1),
          events_days     INTEGER NOT NULL,
          events_max_gb   INTEGER NOT NULL,
          frames_days     INTEGER NOT NULL,
          audit_days      INTEGER NOT NULL,
          updated_at      REAL NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS admin_audit (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          ts            REAL NOT NULL,
          actor         TEXT NOT NULL,
          operation     TEXT NOT NULL,
          scope         TEXT NOT NULL DEFAULT '',
          bytes_removed INTEGER NOT NULL DEFAULT 0,
          rows_removed  INTEGER NOT NULL DEFAULT 0,
          notes         TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_audit_ts ON admin_audit(ts DESC)",
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
        # Seed the singleton row with defaults if missing.
        self._conn.execute(
            "INSERT OR IGNORE INTO retention_policy "
            "(id, events_days, events_max_gb, frames_days, audit_days, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?)",
            (
                DEFAULT_EVENTS_DAYS,
                DEFAULT_EVENTS_MAX_GB,
                DEFAULT_FRAMES_DAYS,
                DEFAULT_AUDIT_DAYS,
                time.time(),
            ),
        )
        self._conn.commit()

    # ── Policy ────────────────────────────────────────────────────

    def get_policy(self) -> RetentionPolicy:
        row = self._conn.execute(
            "SELECT * FROM retention_policy WHERE id = 1",
        ).fetchone()
        return RetentionPolicy(
            events_days=row["events_days"],
            events_max_gb=row["events_max_gb"],
            frames_days=row["frames_days"],
            audit_days=row["audit_days"],
            updated_at=row["updated_at"],
        )

    def update_policy(
        self,
        *,
        events_days: int | None = None,
        events_max_gb: int | None = None,
        frames_days: int | None = None,
        audit_days: int | None = None,
    ) -> RetentionPolicy:
        cur = self.get_policy()
        if events_days is not None:
            cur.events_days = max(1, int(events_days))
        if events_max_gb is not None:
            cur.events_max_gb = max(1, int(events_max_gb))
        if frames_days is not None:
            cur.frames_days = max(1, int(frames_days))
        if audit_days is not None:
            cur.audit_days = max(1, int(audit_days))
        cur.updated_at = time.time()
        self._conn.execute(
            "UPDATE retention_policy SET events_days=?, events_max_gb=?, "
            "frames_days=?, audit_days=?, updated_at=? WHERE id = 1",
            (cur.events_days, cur.events_max_gb, cur.frames_days, cur.audit_days, cur.updated_at),
        )
        self._conn.commit()
        logger.info(
            "retention.policy.updated",
            events_days=cur.events_days,
            frames_days=cur.frames_days,
        )
        return cur

    # ── Admin audit log ───────────────────────────────────────────

    def record_audit(self, audit: AdminAudit) -> int:
        cur = self._conn.execute(
            "INSERT INTO admin_audit "
            "(ts, actor, operation, scope, bytes_removed, rows_removed, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                audit.ts,
                audit.actor,
                audit.operation,
                audit.scope,
                audit.bytes_removed,
                audit.rows_removed,
                audit.notes,
            ),
        )
        self._conn.commit()
        logger.info(
            "retention.audit.recorded",
            operation=audit.operation,
            actor=audit.actor,
        )
        return cur.lastrowid or 0

    def recent_audits(self, limit: int = 50) -> list[AdminAudit]:
        rows = self._conn.execute(
            "SELECT * FROM admin_audit ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            AdminAudit(
                id=r["id"],
                ts=r["ts"],
                actor=r["actor"],
                operation=r["operation"],
                scope=r["scope"],
                bytes_removed=r["bytes_removed"],
                rows_removed=r["rows_removed"],
                notes=r["notes"],
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
