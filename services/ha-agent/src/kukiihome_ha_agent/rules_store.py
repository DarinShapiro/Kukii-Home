"""Rules store — SQLite-backed persistence for user-defined intents.

Two tables:

- ``rules``         — the rule definitions the user wrote (NL or shortcut).
- ``rule_matches``  — audit log: every match (and a sample of non-matches)
                      with reasoned severity and the protective actions the
                      dispatcher actually executed.

Co-located with ``alert_log.json`` under ``/data/kukiihome/`` so it survives
add-on restarts and upgrades. Pure SQLite — no external service, no schema
migrations more elaborate than ``CREATE TABLE IF NOT EXISTS``.

The store is the source of truth; :mod:`rules_runtime` keeps an in-memory
copy for hot-path scope filtering and refreshes it via :meth:`mark_dirty`
on every mutation.

Soft-delete: ``DELETE`` sets ``retired_at`` instead of removing the row,
so the per-rule audit trail in ``rule_matches`` stays joinable forever.
``active_rules()`` filters retired + disabled; ``all_rules()`` includes
everything for the *Retired* tab.

The schema in the design doc (planning/web-ui-iteration-1.md Task 9 §"Data
model") is the spec; this module is its straightforward implementation.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

logger = structlog.get_logger(__name__)

Severity = Literal["critical", "normal", "low"]
RuleMode = Literal["nl", "shortcut"]


# ─── Dataclasses ────────────────────────────────────────────────────


@dataclass
class RuleScope:
    """Scope gate — when triage evaluates this rule.

    Empty list on any axis means *any* (no gate on that axis). All three
    axes are AND-combined; values within an axis are OR-combined. See
    Task 9 §"Data model" for the contract.
    """

    cameras: list[str] = field(default_factory=list)
    areas: list[str] = field(default_factory=list)
    time_windows: list[dict[str, Any]] = field(default_factory=list)
    # time_windows entries shape: {"days": [str], "start": "HH:MM", "end": "HH:MM"}

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> RuleScope:
        try:
            d = json.loads(raw or "{}")
        except json.JSONDecodeError:
            d = {}
        return cls(
            cameras=list(d.get("cameras", [])),
            areas=list(d.get("areas", [])),
            time_windows=list(d.get("time_windows", [])),
        )


@dataclass
class Rule:
    """A user-defined intent.

    ``mode == "nl"``        → ``intent_text`` is read by the VLM as guidance;
                              ``severity_static`` is None (VLM reasons it).
    ``mode == "shortcut"``  → ``shortcut_subject`` is an actor/pet id; triage
                              deterministically matches; ``severity_static``
                              fires verbatim on match. ``intent_text`` may be
                              empty or a human-readable default phrase.
    """

    id: str
    name: str
    mode: RuleMode
    intent_text: str
    scope: RuleScope = field(default_factory=RuleScope)
    enabled: bool = True
    shortcut_subject: str | None = None
    severity_static: Severity | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    matched_count: int = 0
    last_matched_at: float | None = None
    retired_at: float | None = None

    @property
    def active(self) -> bool:
        return self.enabled and self.retired_at is None


@dataclass
class RuleMatch:
    """A single rule-evaluation result — match or non-match — recorded for
    the per-rule audit log. The trace UI reads this to show *why* an alert
    fired (and, on a sample of non-matches, *why not*)."""

    rule_id: str
    incident_id: str
    matched_at: float
    severity: Severity | None
    confidence: float | None
    reasoning: str | None
    matched: bool = True
    protective_actions_taken: list[dict[str, Any]] = field(default_factory=list)
    alert_emitted: bool = True
    id: int | None = None


# ─── Slug derivation ────────────────────────────────────────────────


_SLUG_TRIM = re.compile(r"[^a-z0-9]+")


def slug_for(name: str) -> str:
    """Turn a human name into a stable id. Lower-case, alnum-only with ``_``
    separators, trimmed. Empty input → a short uuid suffix so we never write
    an empty primary key."""
    s = _SLUG_TRIM.sub("_", (name or "").lower()).strip("_")
    return s or f"rule_{uuid.uuid4().hex[:8]}"


# ─── Store ──────────────────────────────────────────────────────────


class RulesStore:
    """SQLite-backed rules + rule_matches store.

    ``path = None`` → in-memory DB (fine for tests). Otherwise the parent
    directory is created on construction and persistence happens inline on
    every mutation.

    Thread/async note: aiohttp handlers may call this from the event loop;
    SQLite calls are short and the connection has ``check_same_thread=False``
    so concurrent handler tasks share one connection without ``Lock``
    plumbing. If write contention becomes real we'll move to a thread-pool
    executor — same shape, no API change.
    """

    SCHEMA = (
        """
        CREATE TABLE IF NOT EXISTS rules (
            id               TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            enabled          INTEGER NOT NULL DEFAULT 1,
            mode             TEXT NOT NULL,
            shortcut_subject TEXT,
            scope_json       TEXT NOT NULL DEFAULT '{}',
            intent_text      TEXT NOT NULL DEFAULT '',
            severity_static  TEXT,
            created_at       REAL NOT NULL,
            updated_at       REAL NOT NULL,
            matched_count    INTEGER NOT NULL DEFAULT 0,
            last_matched_at  REAL,
            retired_at       REAL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rule_matches (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id      TEXT NOT NULL,
            incident_id  TEXT NOT NULL,
            matched_at   REAL NOT NULL,
            severity     TEXT,
            confidence   REAL,
            reasoning    TEXT,
            matched      INTEGER NOT NULL DEFAULT 1,
            protective_actions_taken TEXT NOT NULL DEFAULT '[]',
            alert_emitted INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (rule_id) REFERENCES rules(id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_match_rule ON rule_matches(rule_id, matched_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_match_inc  ON rule_matches(incident_id)",
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
        # Dirty flag for the runtime cache; flipped on any write, cleared by
        # the runtime after it reloads. Cheaper than a pub/sub.
        self._dirty = True

    # ── Reads ──────────────────────────────────────────────────────

    def _row_to_rule(self, row: sqlite3.Row) -> Rule:
        return Rule(
            id=row["id"],
            name=row["name"],
            mode=row["mode"],
            intent_text=row["intent_text"],
            scope=RuleScope.from_json(row["scope_json"]),
            enabled=bool(row["enabled"]),
            shortcut_subject=row["shortcut_subject"],
            severity_static=row["severity_static"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            matched_count=row["matched_count"],
            last_matched_at=row["last_matched_at"],
            retired_at=row["retired_at"],
        )

    def get(self, rule_id: str) -> Rule | None:
        row = self._conn.execute(
            "SELECT * FROM rules WHERE id = ?", (rule_id,)
        ).fetchone()
        return self._row_to_rule(row) if row else None

    def all_rules(self, *, include_retired: bool = False) -> list[Rule]:
        """All rules, ordered by ``updated_at`` desc. Retired hidden by default."""
        if include_retired:
            rows = self._conn.execute(
                "SELECT * FROM rules ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM rules WHERE retired_at IS NULL ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_rule(r) for r in rows]

    def active_rules(self) -> list[Rule]:
        """Enabled + non-retired — the set triage cares about."""
        rows = self._conn.execute(
            "SELECT * FROM rules WHERE enabled = 1 AND retired_at IS NULL "
            "ORDER BY updated_at DESC"
        ).fetchall()
        return [self._row_to_rule(r) for r in rows]

    # ── Writes ─────────────────────────────────────────────────────

    def create(self, rule: Rule) -> Rule:
        """Insert a new rule. If ``rule.id`` is empty, a slug is derived from
        ``name`` (with a uuid suffix on collision)."""
        now = time.time()
        if not rule.id:
            rule.id = slug_for(rule.name)
        # Slug-collision guard: append a short suffix if taken.
        existing = self.get(rule.id)
        if existing is not None:
            rule.id = f"{rule.id}_{uuid.uuid4().hex[:6]}"
        rule.created_at = rule.created_at or now
        rule.updated_at = now
        self._conn.execute(
            "INSERT INTO rules (id, name, enabled, mode, shortcut_subject, "
            "scope_json, intent_text, severity_static, created_at, updated_at, "
            "matched_count, last_matched_at, retired_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rule.id, rule.name, int(rule.enabled), rule.mode,
                rule.shortcut_subject, rule.scope.to_json(), rule.intent_text,
                rule.severity_static, rule.created_at, rule.updated_at,
                rule.matched_count, rule.last_matched_at, rule.retired_at,
            ),
        )
        self._conn.commit()
        self._dirty = True
        logger.info("rules.created", rule_id=rule.id, name=rule.name, mode=rule.mode)
        return rule

    def update(self, rule_id: str, **fields: Any) -> Rule | None:
        """Patch any subset of fields. ``scope`` may be a :class:`RuleScope`;
        we serialize it to ``scope_json``. Unknown fields are ignored so
        the form layer can pass HTML form keys verbatim without filtering."""
        rule = self.get(rule_id)
        if rule is None:
            return None
        allowed = {
            "name", "enabled", "mode", "shortcut_subject",
            "intent_text", "severity_static",
        }
        for k, v in fields.items():
            if k in allowed:
                setattr(rule, k, v)
        if "scope" in fields:
            rule.scope = fields["scope"] if isinstance(fields["scope"], RuleScope) \
                else RuleScope.from_json(str(fields["scope"]))
        rule.updated_at = time.time()
        self._conn.execute(
            "UPDATE rules SET name=?, enabled=?, mode=?, shortcut_subject=?, "
            "scope_json=?, intent_text=?, severity_static=?, updated_at=? "
            "WHERE id=?",
            (
                rule.name, int(rule.enabled), rule.mode, rule.shortcut_subject,
                rule.scope.to_json(), rule.intent_text, rule.severity_static,
                rule.updated_at, rule_id,
            ),
        )
        self._conn.commit()
        self._dirty = True
        logger.info("rules.updated", rule_id=rule_id, fields=list(fields.keys()))
        return rule

    def set_enabled(self, rule_id: str, enabled: bool) -> Rule | None:
        return self.update(rule_id, enabled=enabled)

    def soft_delete(self, rule_id: str) -> Rule | None:
        """Mark retired; preserves audit history. Reversible via
        :meth:`undelete`."""
        rule = self.get(rule_id)
        if rule is None:
            return None
        now = time.time()
        self._conn.execute(
            "UPDATE rules SET retired_at=?, updated_at=? WHERE id=?",
            (now, now, rule_id),
        )
        self._conn.commit()
        self._dirty = True
        rule.retired_at = now
        logger.info("rules.retired", rule_id=rule_id)
        return rule

    def undelete(self, rule_id: str) -> Rule | None:
        self._conn.execute(
            "UPDATE rules SET retired_at=NULL, updated_at=? WHERE id=?",
            (time.time(), rule_id),
        )
        self._conn.commit()
        self._dirty = True
        return self.get(rule_id)

    # ── Match audit log ─────────────────────────────────────────────

    def record_match(self, m: RuleMatch) -> int:
        """Insert one match (or non-match) row, bump the rule's denormalized
        counter if matched. Returns the assigned row id."""
        cur = self._conn.execute(
            "INSERT INTO rule_matches (rule_id, incident_id, matched_at, "
            "severity, confidence, reasoning, matched, "
            "protective_actions_taken, alert_emitted) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                m.rule_id, m.incident_id, m.matched_at, m.severity,
                m.confidence, m.reasoning, int(m.matched),
                json.dumps(m.protective_actions_taken),
                int(m.alert_emitted),
            ),
        )
        match_id = cur.lastrowid or 0
        if m.matched:
            self._conn.execute(
                "UPDATE rules SET matched_count = matched_count + 1, "
                "last_matched_at = ? WHERE id = ?",
                (m.matched_at, m.rule_id),
            )
        self._conn.commit()
        return match_id

    def matches_for_rule(
        self, rule_id: str, *, limit: int = 50, only_matched: bool = False
    ) -> list[RuleMatch]:
        sql = "SELECT * FROM rule_matches WHERE rule_id = ?"
        if only_matched:
            sql += " AND matched = 1"
        sql += " ORDER BY matched_at DESC LIMIT ?"
        rows = self._conn.execute(sql, (rule_id, limit)).fetchall()
        out: list[RuleMatch] = []
        for r in rows:
            try:
                actions = json.loads(r["protective_actions_taken"] or "[]")
            except json.JSONDecodeError:
                actions = []
            out.append(RuleMatch(
                id=r["id"], rule_id=r["rule_id"], incident_id=r["incident_id"],
                matched_at=r["matched_at"], severity=r["severity"],
                confidence=r["confidence"], reasoning=r["reasoning"],
                matched=bool(r["matched"]),
                protective_actions_taken=actions,
                alert_emitted=bool(r["alert_emitted"]),
            ))
        return out

    def matches_for_incident(self, incident_id: str) -> list[RuleMatch]:
        """All rule evaluations for one incident — what the trace page reads."""
        rows = self._conn.execute(
            "SELECT * FROM rule_matches WHERE incident_id = ? ORDER BY matched_at",
            (incident_id,),
        ).fetchall()
        return [
            RuleMatch(
                id=r["id"], rule_id=r["rule_id"], incident_id=r["incident_id"],
                matched_at=r["matched_at"], severity=r["severity"],
                confidence=r["confidence"], reasoning=r["reasoning"],
                matched=bool(r["matched"]),
                protective_actions_taken=json.loads(
                    r["protective_actions_taken"] or "[]"
                ),
                alert_emitted=bool(r["alert_emitted"]),
            )
            for r in rows
        ]

    # ── Cache-coherence helpers ─────────────────────────────────────

    def take_dirty(self) -> bool:
        """Called by :mod:`rules_runtime` — atomically read+clear the dirty
        bit so the cache knows to refresh exactly once per change."""
        was, self._dirty = self._dirty, False
        return was

    def close(self) -> None:
        self._conn.close()
