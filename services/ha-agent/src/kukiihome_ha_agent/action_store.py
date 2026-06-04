"""Action store — per-camera whitelists + protective-action audit log.

Three SQLite tables co-located with rules.db and alert_log.json under
``/data/kukiihome/``:

  - ``perception_whitelist``      — what the agent may TRANSIENTLY do per
                                    camera (lights it can flick on, PTZ ops
                                    it can request); class 2 in §7.7.
  - ``protective_whitelist``      — what the agent may PERSISTENTLY do per
                                    camera (lock doors, trigger sirens),
                                    with policy gates (min severity / conf /
                                    blackout windows / redundancy); class 3.
  - ``protective_actions_log``    — durable audit of every class-3 attempt,
                                    whether executed, gated, or rejected by
                                    whitelist absence. Read by Diagnostics
                                    + the per-incident trace.

The default authority on a fresh camera is **empty** — no perception, no
protective. The user opts in per action per camera. (See planning/
web-ui-iteration-1.md Task 10 §Open: "Default whitelist on new cameras".)
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

# Severity ordering: lower index = more severe. min_severity gate passes when
# the action's incoming severity is at least as severe as the threshold.
_SEVERITY_RANK = {"critical": 0, "normal": 1, "low": 2, "any": 3}


def _severity_meets(actual: str | None, threshold: str) -> bool:
    """True iff ``actual`` is at least as severe as ``threshold``. Unknown
    actual fails closed (i.e. blocks the action) — we never escalate to a
    protective action on a severity we can't even rank."""
    a = _SEVERITY_RANK.get((actual or "").lower(), 99)
    t = _SEVERITY_RANK.get(threshold.lower(), 3)
    return a <= t


# ─── Dataclasses ────────────────────────────────────────────────────


@dataclass
class PerceptionEntry:
    camera_id: str
    target_kind: Literal["ha_service", "camera_api"]
    target: str   # "light.turn_on:light.front_porch" or "ptz_zoom"
    enabled: bool = True
    max_duration_s: int | None = None


@dataclass
class ProtectiveEntry:
    camera_id: str
    action_class: str   # 'lock', 'siren', 'spotlight', 'announcement', ...
    service: str        # 'lock.lock', 'switch.turn_on', ...
    target: str         # entity_id
    min_severity: str = "critical"   # 'critical' | 'normal' | 'low' | 'any'
    min_confidence: float = 0.7
    enabled: bool = True
    blackout_windows: list[dict[str, Any]] = field(default_factory=list)
    max_duration_s: int | None = None
    redundancy_required: int = 0   # consecutive recommendations needed


@dataclass
class ProtectiveLogRow:
    incident_id: str
    camera_id: str | None
    ts: float
    action_class: str
    service: str
    target: str
    data_json: str | None
    status: Literal["ok", "gated", "failed", "whitelisted_rejected"]
    gate_reason: str | None = None
    vlm_confidence: float | None = None
    vlm_rationale: str | None = None
    id: int | None = None


# ─── Store ──────────────────────────────────────────────────────────


class ActionStore:
    """SQLite store: whitelists (read on every VLM round) + audit log
    (append-only). Same shape + sharing rules as :class:`RulesStore` —
    ``path=None`` is in-memory; on-disk path opens with
    ``check_same_thread=False`` for handler-task concurrency."""

    SCHEMA = (
        """
        CREATE TABLE IF NOT EXISTS perception_whitelist (
          camera_id      TEXT NOT NULL,
          target_kind    TEXT NOT NULL,
          target         TEXT NOT NULL,
          enabled        INTEGER NOT NULL DEFAULT 1,
          max_duration_s INTEGER,
          PRIMARY KEY (camera_id, target_kind, target)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS protective_whitelist (
          camera_id          TEXT NOT NULL,
          action_class       TEXT NOT NULL,
          service            TEXT NOT NULL,
          target             TEXT NOT NULL,
          enabled            INTEGER NOT NULL DEFAULT 1,
          min_severity       TEXT NOT NULL DEFAULT 'critical',
          min_confidence     REAL NOT NULL DEFAULT 0.7,
          blackout_windows   TEXT NOT NULL DEFAULT '[]',
          max_duration_s     INTEGER,
          redundancy_required INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (camera_id, action_class, service, target)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS protective_actions_log (
          id           INTEGER PRIMARY KEY AUTOINCREMENT,
          incident_id  TEXT NOT NULL,
          camera_id    TEXT,
          ts           REAL NOT NULL,
          action_class TEXT NOT NULL,
          service      TEXT NOT NULL,
          target       TEXT NOT NULL,
          data_json    TEXT,
          status       TEXT NOT NULL,
          gate_reason  TEXT,
          vlm_confidence REAL,
          vlm_rationale  TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_log_incident ON protective_actions_log(incident_id)",
        "CREATE INDEX IF NOT EXISTS idx_log_camera   ON protective_actions_log(camera_id, ts DESC)",
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

    # ── Perception whitelist ────────────────────────────────────────

    def upsert_perception(self, e: PerceptionEntry) -> None:
        self._conn.execute(
            "INSERT INTO perception_whitelist (camera_id, target_kind, target, "
            "enabled, max_duration_s) VALUES (?,?,?,?,?) "
            "ON CONFLICT(camera_id, target_kind, target) DO UPDATE SET "
            "enabled = excluded.enabled, "
            "max_duration_s = excluded.max_duration_s",
            (e.camera_id, e.target_kind, e.target,
             int(e.enabled), e.max_duration_s),
        )
        self._conn.commit()

    def perception_for(self, camera_id: str) -> list[PerceptionEntry]:
        rows = self._conn.execute(
            "SELECT * FROM perception_whitelist WHERE camera_id = ? AND enabled = 1",
            (camera_id,),
        ).fetchall()
        return [
            PerceptionEntry(
                camera_id=r["camera_id"], target_kind=r["target_kind"],
                target=r["target"], enabled=bool(r["enabled"]),
                max_duration_s=r["max_duration_s"],
            )
            for r in rows
        ]

    def delete_perception(self, camera_id: str, target_kind: str, target: str) -> None:
        self._conn.execute(
            "DELETE FROM perception_whitelist WHERE camera_id=? "
            "AND target_kind=? AND target=?",
            (camera_id, target_kind, target),
        )
        self._conn.commit()

    # ── Protective whitelist ────────────────────────────────────────

    def upsert_protective(self, e: ProtectiveEntry) -> None:
        self._conn.execute(
            "INSERT INTO protective_whitelist (camera_id, action_class, service, "
            "target, enabled, min_severity, min_confidence, blackout_windows, "
            "max_duration_s, redundancy_required) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(camera_id, action_class, service, target) DO UPDATE SET "
            "enabled=excluded.enabled, "
            "min_severity=excluded.min_severity, "
            "min_confidence=excluded.min_confidence, "
            "blackout_windows=excluded.blackout_windows, "
            "max_duration_s=excluded.max_duration_s, "
            "redundancy_required=excluded.redundancy_required",
            (
                e.camera_id, e.action_class, e.service, e.target,
                int(e.enabled), e.min_severity, e.min_confidence,
                json.dumps(e.blackout_windows), e.max_duration_s,
                e.redundancy_required,
            ),
        )
        self._conn.commit()

    def protective_for(self, camera_id: str) -> list[ProtectiveEntry]:
        rows = self._conn.execute(
            "SELECT * FROM protective_whitelist WHERE camera_id = ? AND enabled = 1",
            (camera_id,),
        ).fetchall()
        return [self._row_to_protective(r) for r in rows]

    def find_protective(
        self, *, camera_id: str, service: str, target: str, action_class: str
    ) -> ProtectiveEntry | None:
        row = self._conn.execute(
            "SELECT * FROM protective_whitelist WHERE camera_id=? "
            "AND service=? AND target=? AND action_class=? AND enabled=1",
            (camera_id, service, target, action_class),
        ).fetchone()
        return self._row_to_protective(row) if row else None

    def delete_protective(
        self, camera_id: str, action_class: str, service: str, target: str
    ) -> None:
        self._conn.execute(
            "DELETE FROM protective_whitelist WHERE camera_id=? "
            "AND action_class=? AND service=? AND target=?",
            (camera_id, action_class, service, target),
        )
        self._conn.commit()

    # ── Audit log ──────────────────────────────────────────────────

    def log_protective(self, row: ProtectiveLogRow) -> int:
        cur = self._conn.execute(
            "INSERT INTO protective_actions_log "
            "(incident_id, camera_id, ts, action_class, service, target, "
            "data_json, status, gate_reason, vlm_confidence, vlm_rationale) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                row.incident_id, row.camera_id, row.ts, row.action_class,
                row.service, row.target, row.data_json, row.status,
                row.gate_reason, row.vlm_confidence, row.vlm_rationale,
            ),
        )
        self._conn.commit()
        return cur.lastrowid or 0

    def log_for_incident(self, incident_id: str) -> list[ProtectiveLogRow]:
        rows = self._conn.execute(
            "SELECT * FROM protective_actions_log WHERE incident_id = ? "
            "ORDER BY ts",
            (incident_id,),
        ).fetchall()
        return [self._row_to_log(r) for r in rows]

    def recent_log(self, *, limit: int = 50) -> list[ProtectiveLogRow]:
        rows = self._conn.execute(
            "SELECT * FROM protective_actions_log ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_log(r) for r in rows]

    # ── Row → dataclass helpers ─────────────────────────────────────

    @staticmethod
    def _row_to_protective(row: sqlite3.Row) -> ProtectiveEntry:
        try:
            blackouts = json.loads(row["blackout_windows"] or "[]")
        except json.JSONDecodeError:
            blackouts = []
        return ProtectiveEntry(
            camera_id=row["camera_id"], action_class=row["action_class"],
            service=row["service"], target=row["target"],
            enabled=bool(row["enabled"]),
            min_severity=row["min_severity"], min_confidence=row["min_confidence"],
            blackout_windows=blackouts, max_duration_s=row["max_duration_s"],
            redundancy_required=row["redundancy_required"],
        )

    @staticmethod
    def _row_to_log(row: sqlite3.Row) -> ProtectiveLogRow:
        return ProtectiveLogRow(
            id=row["id"], incident_id=row["incident_id"],
            camera_id=row["camera_id"], ts=row["ts"],
            action_class=row["action_class"], service=row["service"],
            target=row["target"], data_json=row["data_json"],
            status=row["status"], gate_reason=row["gate_reason"],
            vlm_confidence=row["vlm_confidence"],
            vlm_rationale=row["vlm_rationale"],
        )

    def close(self) -> None:
        self._conn.close()


# ─── Policy gate helper ─────────────────────────────────────────────


@dataclass(frozen=True)
class GateDecision:
    """Result of running a protective-action recommendation through the
    whitelist + policy. ``execute=True`` means the action_runtime should
    fire the HA call; otherwise ``reason`` captures why it didn't."""

    execute: bool
    reason: str
    matched_entry: ProtectiveEntry | None = None


def gate_recommendation(
    *,
    store: ActionStore,
    camera_id: str | None,
    action_class: str,
    service: str,
    target: str,
    severity: str | None,
    confidence: float | None,
    now_ts: float | None = None,
) -> GateDecision:
    """Decide whether to execute a VLM-recommended protective action.

    Rejection path (the most important shape):
      1. No camera scope → reject (we don't authorize unscoped lock fires).
      2. Whitelist absent for (camera, class, service, target) → reject
         with "no_authorization".
      3. Severity gate fails → reject with "severity_below_threshold".
      4. Confidence gate fails → reject with "confidence_below_threshold".
      5. Blackout window active → reject with "blackout_window".
      (Redundancy is enforced at the runtime layer where call history lives.)

    Returns the matched whitelist entry on accept so the runtime can read
    max_duration_s without a second store query.
    """
    if not camera_id:
        return GateDecision(False, "no_camera_scope")
    entry = store.find_protective(
        camera_id=camera_id, service=service, target=target,
        action_class=action_class,
    )
    if entry is None:
        return GateDecision(False, "no_authorization")
    if not _severity_meets(severity, entry.min_severity):
        return GateDecision(False, "severity_below_threshold", entry)
    conf = float(confidence or 0.0)
    if conf < entry.min_confidence:
        return GateDecision(False, "confidence_below_threshold", entry)
    if _in_blackout(entry.blackout_windows, now_ts):
        return GateDecision(False, "blackout_window", entry)
    return GateDecision(True, "ok", entry)


def _in_blackout(windows: list[dict[str, Any]], now_ts: float | None) -> bool:
    """Same time-window matcher as :mod:`rules_runtime` but with the *inverse*
    semantic — within a window means the action is SUPPRESSED. Empty/None
    list → never suppressed (always pass)."""
    if not windows:
        return False
    # Local import keeps action_store independent of rules_runtime at module
    # load time — they're sibling utilities; coupling at import would force
    # rule plumbing on action-only callers.
    from datetime import datetime as _datetime

    from .rules_runtime import _in_time_window

    when = _datetime.fromtimestamp(now_ts) if now_ts else _datetime.now()
    return any(_in_time_window(w, when) for w in windows)


# Convenience: a stamp helper for log rows so callers don't repeat time.time()
def now() -> float:
    return time.time()
