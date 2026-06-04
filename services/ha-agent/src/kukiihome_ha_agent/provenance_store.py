"""ProvenanceStore — sessions + transcripts + per-guidance audit trail.

Part X §36. Three tables sharing one SQLite database:

  - **sessions** — one row per active drawer thread. A session opens when
    the user first interacts with the drawer; it closes after 24h of
    inactivity. Carries page_context + alert_context for the auto-load
    behavior (Part X §40).
  - **transcripts** — every utterance (user) + proposal (system) +
    confirmation event, ordered by ``turn_index`` within a session.
    Persists forever — the audit value depends on it.
  - **guidance_provenance** — one row per committed guidance entry,
    cross-referencing back to the originating session + transcript turn
    + the LLM's one-sentence placement_reasoning. This is the audit
    primitive (Part X §38).

The store is *separate* from the per-class guidance stores (RulesStore,
PreferencesStore, ...). When ``commit_guidance`` writes a Rule, it
writes the row to RulesStore AND a provenance row here keyed by the
new Rule's id. Reads on the /memory page join across both.
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


Origin = Literal["conversation", "form", "system_proposed", "pre_provenance"]
TurnRole = Literal["user", "system"]


# ─── Dataclasses ────────────────────────────────────────────────────


@dataclass
class Session:
    id: str
    user_id: str
    opened_at: float
    closed_at: float | None = None
    page_context: str = ""
    alert_context: str = ""


@dataclass
class TranscriptTurn:
    id: str
    session_id: str
    turn_index: int
    role: TurnRole
    utterance: str
    proposal_json: str = ""               # JSON-serialized PlacementProposal when role='system'
    committed_to: str = ""                # guidance_id when this turn produced a commit
    ts: float = 0.0


@dataclass
class Provenance:
    guidance_id: str
    origin: Origin
    transcript_id: str = ""               # originating turn (system role with proposal_json)
    user_utterance: str = ""              # denormalized for fast audit reads
    placement_reasoning: str = ""         # the LLM's one-sentence justification
    user_confirmed_at: float = 0.0
    refinement_transcript_ids: list[str] = field(default_factory=list)


# ─── Store ──────────────────────────────────────────────────────────


# 24 hours of drawer inactivity closes the session. The next utterance
# opens a fresh one. Transcripts persist either way.
SESSION_IDLE_TIMEOUT_S = 24 * 3600.0


class ProvenanceStore:
    SCHEMA = (
        """
        CREATE TABLE IF NOT EXISTS sessions (
          id            TEXT PRIMARY KEY,
          user_id       TEXT NOT NULL,
          opened_at     REAL NOT NULL,
          closed_at     REAL,
          page_context  TEXT NOT NULL DEFAULT '',
          alert_context TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS transcripts (
          id            TEXT PRIMARY KEY,
          session_id    TEXT NOT NULL,
          turn_index    INTEGER NOT NULL,
          role          TEXT NOT NULL,
          utterance     TEXT NOT NULL,
          proposal_json TEXT NOT NULL DEFAULT '',
          committed_to  TEXT NOT NULL DEFAULT '',
          ts            REAL NOT NULL,
          FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS guidance_provenance (
          guidance_id              TEXT PRIMARY KEY,
          origin                   TEXT NOT NULL,
          transcript_id            TEXT NOT NULL DEFAULT '',
          user_utterance           TEXT NOT NULL DEFAULT '',
          placement_reasoning      TEXT NOT NULL DEFAULT '',
          user_confirmed_at        REAL NOT NULL DEFAULT 0,
          refinement_transcript_ids TEXT NOT NULL DEFAULT '[]'
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_transcripts_session ON transcripts(session_id, turn_index)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_active ON sessions(user_id, closed_at)",
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

    # ── Session management ──────────────────────────────────────────

    def active_session_for(
        self, user_id: str, *, now_ts: float | None = None,
    ) -> Session | None:
        """Return the user's most recent unclosed session if it's still
        within the idle window; otherwise None (caller should ``open_session``)."""
        now = now_ts or time.time()
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? AND closed_at IS NULL "
            "ORDER BY opened_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        last_turn = self._conn.execute(
            "SELECT MAX(ts) AS last_ts FROM transcripts WHERE session_id = ?",
            (row["id"],),
        ).fetchone()
        last_activity = last_turn["last_ts"] or row["opened_at"]
        if now - last_activity > SESSION_IDLE_TIMEOUT_S:
            # Idle timeout — close the stale session
            self._conn.execute(
                "UPDATE sessions SET closed_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            self._conn.commit()
            return None
        return Session(
            id=row["id"], user_id=row["user_id"], opened_at=row["opened_at"],
            closed_at=row["closed_at"], page_context=row["page_context"],
            alert_context=row["alert_context"],
        )

    def open_session(
        self, user_id: str, *,
        page_context: str = "", alert_context: str = "",
        now_ts: float | None = None,
    ) -> Session:
        sid = f"sess_{uuid.uuid4().hex[:12]}"
        opened = now_ts or time.time()
        self._conn.execute(
            "INSERT INTO sessions (id, user_id, opened_at, page_context, alert_context) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, user_id, opened, page_context, alert_context),
        )
        self._conn.commit()
        logger.info("provenance.session.opened", session_id=sid, user_id=user_id)
        return Session(
            id=sid, user_id=user_id, opened_at=opened,
            page_context=page_context, alert_context=alert_context,
        )

    def close_session(self, session_id: str, *, now_ts: float | None = None) -> None:
        self._conn.execute(
            "UPDATE sessions SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
            (now_ts or time.time(), session_id),
        )
        self._conn.commit()

    def get_or_open_session(
        self, user_id: str, *,
        page_context: str = "", alert_context: str = "",
        now_ts: float | None = None,
    ) -> Session:
        """The convenience the drawer route handler wants — one call returns
        a usable session, opening one only if the active one is stale or
        missing. Pre-existing sessions inherit their original page/alert
        context; this method does NOT clobber them."""
        existing = self.active_session_for(user_id, now_ts=now_ts)
        if existing:
            return existing
        return self.open_session(
            user_id, page_context=page_context, alert_context=alert_context,
            now_ts=now_ts,
        )

    # ── Transcript turns ──────────────────────────────────────────

    def append_turn(
        self, session_id: str, *,
        role: TurnRole, utterance: str,
        proposal_json: str = "", committed_to: str = "",
        now_ts: float | None = None,
    ) -> TranscriptTurn:
        ts = now_ts or time.time()
        # Compute next turn_index for this session
        row = self._conn.execute(
            "SELECT COALESCE(MAX(turn_index), -1) + 1 AS next_index "
            "FROM transcripts WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        next_idx = row["next_index"]
        tid = f"trn_{uuid.uuid4().hex[:12]}"
        self._conn.execute(
            "INSERT INTO transcripts (id, session_id, turn_index, role, "
            "utterance, proposal_json, committed_to, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, session_id, next_idx, role, utterance,
             proposal_json, committed_to, ts),
        )
        self._conn.commit()
        return TranscriptTurn(
            id=tid, session_id=session_id, turn_index=next_idx,
            role=role, utterance=utterance, proposal_json=proposal_json,
            committed_to=committed_to, ts=ts,
        )

    def turns_for_session(self, session_id: str) -> list[TranscriptTurn]:
        rows = self._conn.execute(
            "SELECT * FROM transcripts WHERE session_id = ? "
            "ORDER BY turn_index ASC",
            (session_id,),
        ).fetchall()
        return [
            TranscriptTurn(
                id=r["id"], session_id=r["session_id"],
                turn_index=r["turn_index"], role=r["role"],
                utterance=r["utterance"], proposal_json=r["proposal_json"],
                committed_to=r["committed_to"], ts=r["ts"],
            )
            for r in rows
        ]

    def get_turn(self, turn_id: str) -> TranscriptTurn | None:
        r = self._conn.execute(
            "SELECT * FROM transcripts WHERE id = ?", (turn_id,),
        ).fetchone()
        if not r:
            return None
        return TranscriptTurn(
            id=r["id"], session_id=r["session_id"],
            turn_index=r["turn_index"], role=r["role"],
            utterance=r["utterance"], proposal_json=r["proposal_json"],
            committed_to=r["committed_to"], ts=r["ts"],
        )

    # ── Provenance per guidance entry ─────────────────────────────

    def record_provenance(self, provenance: Provenance) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO guidance_provenance "
            "(guidance_id, origin, transcript_id, user_utterance, "
            " placement_reasoning, user_confirmed_at, refinement_transcript_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                provenance.guidance_id, provenance.origin,
                provenance.transcript_id, provenance.user_utterance,
                provenance.placement_reasoning, provenance.user_confirmed_at,
                json.dumps(provenance.refinement_transcript_ids),
            ),
        )
        self._conn.commit()

    def get_provenance(self, guidance_id: str) -> Provenance | None:
        r = self._conn.execute(
            "SELECT * FROM guidance_provenance WHERE guidance_id = ?",
            (guidance_id,),
        ).fetchone()
        if not r:
            return None
        try:
            refs = json.loads(r["refinement_transcript_ids"] or "[]")
        except json.JSONDecodeError:
            refs = []
        return Provenance(
            guidance_id=r["guidance_id"], origin=r["origin"],
            transcript_id=r["transcript_id"],
            user_utterance=r["user_utterance"],
            placement_reasoning=r["placement_reasoning"],
            user_confirmed_at=r["user_confirmed_at"],
            refinement_transcript_ids=refs,
        )

    def append_refinement(
        self, guidance_id: str, transcript_id: str,
    ) -> Provenance | None:
        """Add a new refinement turn id to an existing provenance row."""
        p = self.get_provenance(guidance_id)
        if p is None:
            return None
        if transcript_id not in p.refinement_transcript_ids:
            p.refinement_transcript_ids.append(transcript_id)
        self.record_provenance(p)
        return p

    def backfill_pre_provenance(self, guidance_ids: list[str]) -> int:
        """One-time migration: stamp pre-existing guidance entries with a
        sentinel provenance so the audit view doesn't 404. Skips entries
        that already have provenance."""
        n = 0
        for gid in guidance_ids:
            if self.get_provenance(gid):
                continue
            self.record_provenance(Provenance(
                guidance_id=gid, origin="pre_provenance",
            ))
            n += 1
        return n

    # ── Misc ────────────────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()


# ─── PlacementProposal (Part X §35) ────────────────────────────────


StorageClass = Literal[
    "rule", "preference", "transient_intent", "dismissal_policy",
    "situational_context", "access_profile", "area_posture",
]
Lifecycle = Literal["persistent", "temporal", "fire_once"]
FireAffordance = Literal["alert", "shift_prior", "dismiss", "metadata"]
Severity = Literal["low", "normal", "critical"]


@dataclass
class PlacementProposal:
    """The schema-validated payload the dispatcher (Part X §35) returns
    and ``commit_guidance`` writes. The drawer renders this as a preview
    card before any write happens.

    All fields except ``clarifying_questions`` are required. If
    ``confidence < 0.7``, ``clarifying_questions`` is non-empty and the
    drawer asks before allowing confirm.
    """

    storage_class: StorageClass
    name: str
    scope: dict[str, str]                # actor/area/camera/kind/pattern
    lifecycle: Lifecycle
    fire_affordance: FireAffordance
    intent_text: str
    reasoning: str                       # one-sentence audit primitive
    confidence: float                    # 0..1
    lifecycle_ttl_iso: str | None = None
    severity: Severity | None = None
    clarifying_questions: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({
            "storage_class": self.storage_class,
            "name": self.name,
            "scope": self.scope,
            "lifecycle": self.lifecycle,
            "lifecycle_ttl_iso": self.lifecycle_ttl_iso,
            "fire_affordance": self.fire_affordance,
            "severity": self.severity,
            "intent_text": self.intent_text,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "clarifying_questions": self.clarifying_questions,
        })

    @classmethod
    def from_json(cls, blob: str) -> PlacementProposal:
        data = json.loads(blob)
        return cls(
            storage_class=data["storage_class"],
            name=data["name"],
            scope=data.get("scope") or {},
            lifecycle=data["lifecycle"],
            lifecycle_ttl_iso=data.get("lifecycle_ttl_iso"),
            fire_affordance=data["fire_affordance"],
            severity=data.get("severity"),
            intent_text=data["intent_text"],
            reasoning=data["reasoning"],
            confidence=float(data.get("confidence", 1.0)),
            clarifying_questions=data.get("clarifying_questions") or [],
        )

    def needs_disambiguation(self) -> bool:
        return self.confidence < 0.7 or bool(self.clarifying_questions)


def validate_proposal(data: Any) -> PlacementProposal:
    """Defensive validator for LLM-provided structured output. Raises
    ``ValueError`` with the first failing field name so the dispatcher
    can retry with the error in the retry prompt."""
    if not isinstance(data, dict):
        raise ValueError("proposal must be a JSON object")
    required = (
        "storage_class", "name", "scope", "lifecycle",
        "fire_affordance", "intent_text", "reasoning",
    )
    for k in required:
        if k not in data:
            raise ValueError(f"missing required field: {k}")
    if data["storage_class"] not in (
        "rule", "preference", "transient_intent", "dismissal_policy",
        "situational_context", "access_profile", "area_posture",
    ):
        raise ValueError(f"bad storage_class: {data['storage_class']}")
    if data["lifecycle"] not in ("persistent", "temporal", "fire_once"):
        raise ValueError(f"bad lifecycle: {data['lifecycle']}")
    if data["fire_affordance"] not in (
        "alert", "shift_prior", "dismiss", "metadata",
    ):
        raise ValueError(f"bad fire_affordance: {data['fire_affordance']}")
    if data["lifecycle"] in ("temporal", "fire_once") and not data.get("lifecycle_ttl_iso"):
        # fire_once policies typically still need a ttl as a safety
        if data["lifecycle"] == "temporal":
            raise ValueError("lifecycle=temporal requires lifecycle_ttl_iso")
    sev = data.get("severity")
    if sev is not None and sev not in ("low", "normal", "critical"):
        raise ValueError(f"bad severity: {sev}")
    if not isinstance(data["scope"], dict):
        raise ValueError("scope must be an object")
    return PlacementProposal(
        storage_class=data["storage_class"],
        name=str(data["name"]).strip(),
        scope=data["scope"],
        lifecycle=data["lifecycle"],
        lifecycle_ttl_iso=data.get("lifecycle_ttl_iso"),
        fire_affordance=data["fire_affordance"],
        severity=sev,
        intent_text=str(data["intent_text"]),
        reasoning=str(data["reasoning"]),
        confidence=float(data.get("confidence", 1.0)),
        clarifying_questions=list(data.get("clarifying_questions") or []),
    )
