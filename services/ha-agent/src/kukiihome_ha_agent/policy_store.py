"""PolicyStore — dismissal policies + transient intents.

Two kinds of *policies* the user accumulates over time, both queried by the
reasoner before it commits a final criticality:

  - **DismissalPolicy** — *"suppress this pattern."* Created when the user
    ✗-es an event ("not interesting"): a narrow descriptor of the dismissed
    situation is stored + applied to future matching incidents so they
    short-circuit to a passive timeline row instead of an alert. Per the
    memory model's TransientIntent doc, dismissals carry a TTL and a
    sanity-check countdown so they're not silent forever.

  - **TransientIntent** — *"keep an eye out for X."* Conversational
    forward-looking watch ("notify me when Bob's car arrives"). Self-prunes
    on fire (fire_once=True) or TTL expiry. Boosts the priority of matching
    events.

Co-located with rules.db / actions.db / areas.db under /data/kukiihome/.
Both tables soft-delete via ``revoked_at`` so the user can see what's
been revoked recently and undo. The reverse-link from passive activity
rows (Part VII §"reverse-link") reads ``policy_hits`` to surface which
policy suppressed an event.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

logger = structlog.get_logger(__name__)


PolicyKind = Literal["dismissal", "transient_intent"]


# ─── Dataclasses ────────────────────────────────────────────────────


@dataclass
class Policy:
    """Common shape for dismissal + transient-intent rows. ``kind`` carries
    the type; ``descriptor`` is JSON shaped per-kind:

    Dismissal descriptor:
      {"camera_id": "...", "actor_id": "...", "kind": "dog", ... }

    TransientIntent descriptor:
      {"prompt": "notify when Bob's car arrives", "actor_id": "bob",
       "fire_once": true}
    """

    id: str
    kind: PolicyKind
    name: str  # user-facing label
    descriptor: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""  # human-readable why created
    created_at: float = 0.0
    expires_at: float | None = None  # None = no TTL
    last_applied_at: float | None = None
    apply_count: int = 0
    revoked_at: float | None = None


@dataclass
class PolicyHit:
    """Audit row for *"this policy applied to this incident"*. Read by the
    Trace page (Part III §22) and the Policies list's *"recent hits"*
    sidebar; not directly editable."""

    policy_id: str
    incident_id: str
    applied_at: float
    outcome: str  # 'dismissed' | 'boosted' | 'noop'
    id: int | None = None


# ─── Store ──────────────────────────────────────────────────────────


class PolicyStore:
    SCHEMA = (
        """
        CREATE TABLE IF NOT EXISTS policies (
          id              TEXT PRIMARY KEY,
          kind            TEXT NOT NULL,
          name            TEXT NOT NULL,
          descriptor      TEXT NOT NULL DEFAULT '{}',
          rationale       TEXT NOT NULL DEFAULT '',
          created_at      REAL NOT NULL,
          expires_at      REAL,
          last_applied_at REAL,
          apply_count     INTEGER NOT NULL DEFAULT 0,
          revoked_at      REAL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS policy_hits (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          policy_id   TEXT NOT NULL,
          incident_id TEXT NOT NULL,
          applied_at  REAL NOT NULL,
          outcome     TEXT NOT NULL,
          FOREIGN KEY (policy_id) REFERENCES policies(id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_hits_policy ON policy_hits(policy_id, applied_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_hits_incident ON policy_hits(incident_id)",
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

    def _row_to_policy(self, row: sqlite3.Row) -> Policy:
        try:
            desc = json.loads(row["descriptor"] or "{}")
        except json.JSONDecodeError:
            desc = {}
        return Policy(
            id=row["id"],
            kind=row["kind"],
            name=row["name"],
            descriptor=desc,
            rationale=row["rationale"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_applied_at=row["last_applied_at"],
            apply_count=row["apply_count"],
            revoked_at=row["revoked_at"],
        )

    def get(self, policy_id: str) -> Policy | None:
        row = self._conn.execute("SELECT * FROM policies WHERE id = ?", (policy_id,)).fetchone()
        return self._row_to_policy(row) if row else None

    def all_policies(
        self,
        *,
        kind: PolicyKind | None = None,
        include_revoked: bool = False,
        now_ts: float | None = None,
    ) -> list[Policy]:
        """Returns active (non-revoked, non-expired) policies by default.
        Expired TTL → treated as revoked even without a ``revoked_at``."""
        now = now_ts or time.time()
        sql = "SELECT * FROM policies"
        params: list[Any] = []
        clauses: list[str] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if not include_revoked:
            clauses.append("revoked_at IS NULL")
            clauses.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(now)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        return [self._row_to_policy(r) for r in self._conn.execute(sql, params).fetchall()]

    def hits_for_policy(
        self,
        policy_id: str,
        *,
        limit: int = 50,
    ) -> list[PolicyHit]:
        rows = self._conn.execute(
            "SELECT * FROM policy_hits WHERE policy_id = ? ORDER BY applied_at DESC LIMIT ?",
            (policy_id, limit),
        ).fetchall()
        return [
            PolicyHit(
                id=r["id"],
                policy_id=r["policy_id"],
                incident_id=r["incident_id"],
                applied_at=r["applied_at"],
                outcome=r["outcome"],
            )
            for r in rows
        ]

    def hits_for_incident(self, incident_id: str) -> list[PolicyHit]:
        """Reverse-link from passive activity rows."""
        rows = self._conn.execute(
            "SELECT * FROM policy_hits WHERE incident_id = ?",
            (incident_id,),
        ).fetchall()
        return [
            PolicyHit(
                id=r["id"],
                policy_id=r["policy_id"],
                incident_id=r["incident_id"],
                applied_at=r["applied_at"],
                outcome=r["outcome"],
            )
            for r in rows
        ]

    # ── Writes ────────────────────────────────────────────────────

    def create(self, policy: Policy) -> Policy:
        if not policy.id:
            policy.id = f"pol_{uuid.uuid4().hex[:10]}"
        policy.created_at = policy.created_at or time.time()
        self._conn.execute(
            "INSERT INTO policies (id, kind, name, descriptor, rationale, "
            "created_at, expires_at, last_applied_at, apply_count, revoked_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                policy.id,
                policy.kind,
                policy.name,
                json.dumps(policy.descriptor),
                policy.rationale,
                policy.created_at,
                policy.expires_at,
                policy.last_applied_at,
                policy.apply_count,
                policy.revoked_at,
            ),
        )
        self._conn.commit()
        logger.info("policies.created", policy_id=policy.id, kind=policy.kind)
        return policy

    def revoke(self, policy_id: str) -> Policy | None:
        now = time.time()
        self._conn.execute(
            "UPDATE policies SET revoked_at = ? WHERE id = ?",
            (now, policy_id),
        )
        self._conn.commit()
        logger.info("policies.revoked", policy_id=policy_id)
        return self.get(policy_id)

    def reinstate(self, policy_id: str) -> Policy | None:
        self._conn.execute(
            "UPDATE policies SET revoked_at = NULL WHERE id = ?",
            (policy_id,),
        )
        self._conn.commit()
        return self.get(policy_id)

    def record_hit(self, hit: PolicyHit) -> int:
        cur = self._conn.execute(
            "INSERT INTO policy_hits (policy_id, incident_id, applied_at, outcome) "
            "VALUES (?, ?, ?, ?)",
            (hit.policy_id, hit.incident_id, hit.applied_at, hit.outcome),
        )
        # Bump denormalized counters on the policy.
        if hit.outcome in ("dismissed", "boosted"):
            self._conn.execute(
                "UPDATE policies SET apply_count = apply_count + 1, "
                "last_applied_at = ? WHERE id = ?",
                (hit.applied_at, hit.policy_id),
            )
        self._conn.commit()
        return cur.lastrowid or 0

    def close(self) -> None:
        self._conn.close()
