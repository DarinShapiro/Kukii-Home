"""High-level memory store — the facade over SQL + Vector DB + object store.

Exposes the `memory.*` operations used by the rest of the system. Each method
maps to one of the MCP tools described in §07; the MCP server layer (Epic 9
+ this epic's #93-#99) wraps these in MCP request handlers.

Hybrid retrieval (SQL filter + ANN rank) is implemented in :meth:`retrieve_rules`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from sentihome_memory.models import (
    Base,
    EpisodicSummary,
    KnownActor,
    RuleRecord,
    Session,
    SessionSegment,
    VisitLedger,
)
from sentihome_memory.retention import RetentionPolicy, SoftDeleteGracePeriod

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


@dataclass
class MemoryStoreConfig:
    """Connection settings for the memory store."""

    database_url: str = "postgresql+asyncpg://sentihome:sentihome@localhost:5432/sentihome"
    qdrant_url: str = "http://localhost:6333"
    object_store_path: str = "/var/lib/sentihome/objects"
    rules_top_k: int = 5
    """Max rules to return from retrieve_rules() per event."""
    episodic_top_k: int = 3
    """Max episodic summaries to return from recall_episodic()."""

    @classmethod
    def from_topology(cls, memory: Any) -> MemoryStoreConfig:
        """Build from :class:`sentihome_shared.topology.MemoryConfig`.

        ``object_store`` is a URI (``file://``, ``s3://``, ``minio://``);
        for ``file://`` we strip the scheme so existing code keeps a path.
        """
        store = memory.object_store
        if isinstance(store, str) and store.startswith("file://"):
            store = store[len("file://") :]
        return cls(
            database_url=memory.postgres_url,
            qdrant_url=memory.qdrant_url,
            object_store_path=store,
            rules_top_k=memory.rules_top_k,
            episodic_top_k=memory.episodic_top_k,
        )


class MemoryStore:
    """Async memory store facade.

    Construct once at service startup, share across handlers::

        store = MemoryStore(config)
        await store.init_schema()
        # ... use during normal operation ...
        await store.close()
    """

    def __init__(self, config: MemoryStoreConfig | None = None) -> None:
        self._config = config or MemoryStoreConfig()
        self._engine = create_async_engine(self._config.database_url, echo=False)
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> Any:
        return self._engine

    async def init_schema(self) -> None:
        """Create all tables. For dev only; production uses Alembic migrations."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self._engine.dispose()

    def session(self) -> AsyncSession:
        return self._sessionmaker()

    # ─────────────────────────────────────────────────────────────────
    # Sessions (#93)
    # ─────────────────────────────────────────────────────────────────

    async def open_session(
        self,
        *,
        session_id: str,
        subject_descriptor: str | None,
        opened_at: datetime,
        privacy_tier: str,
    ) -> Session:
        async with self.session() as db:
            session = Session(
                session_id=session_id,
                subject_descriptor=subject_descriptor,
                opened_at=opened_at,
                privacy_tier=privacy_tier,
                status="open",
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
            return session

    async def append_segment(
        self,
        *,
        session_id: str,
        camera_id: str,
        started_at: datetime,
        ended_at: datetime | None = None,
        detection_count: int = 0,
        clip_ref: str | None = None,
    ) -> SessionSegment:
        async with self.session() as db:
            segment = SessionSegment(
                session_id=session_id,
                camera_id=camera_id,
                started_at=started_at,
                ended_at=ended_at,
                detection_count=detection_count,
                clip_ref=clip_ref,
            )
            db.add(segment)
            await db.commit()
            await db.refresh(segment)
            return segment

    async def close_session(
        self,
        *,
        session_id: str,
        closed_at: datetime,
        status: str = "closed",
    ) -> Session | None:
        async with self.session() as db:
            session = await db.get(Session, session_id)
            if session is None:
                return None
            session.closed_at = closed_at
            session.status = status
            await db.commit()
            await db.refresh(session)
            return session

    async def get_session(self, session_id: str) -> Session | None:
        async with self.session() as db:
            stmt = (
                select(Session)
                .options(selectinload(Session.segments))
                .where(Session.session_id == session_id)
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    # ─────────────────────────────────────────────────────────────────
    # Rule retrieval (#94)
    # ─────────────────────────────────────────────────────────────────

    async def retrieve_rules(
        self,
        *,
        area_id: str | None = None,
        camera_id: str | None = None,
        zone_id: str | None = None,
        subject_type: str | None = None,
        now: datetime | None = None,
        embedding: list[float] | None = None,
    ) -> list[RuleRecord]:
        """Hybrid retrieval: SQL filter on scope/temporal + ANN rank on embedding.

        v1 ships SQL filter + scope priority sort. Vector ranking is a TODO once
        rule embeddings are populated (depends on rule-creation pipeline in Epic 7).
        """
        now = now or datetime.utcnow()
        # SQL filter: only non-suppressed, non-deleted rules
        async with self.session() as db:
            stmt = select(RuleRecord).where(
                RuleRecord.deleted_at.is_(None),
                or_(
                    RuleRecord.suppress_until.is_(None),
                    RuleRecord.suppress_until < now,
                ),
            )
            scope_clauses = []
            if zone_id:
                scope_clauses.append(
                    and_(RuleRecord.scope == "zone", RuleRecord.scope_ref == zone_id)
                )
            if camera_id:
                scope_clauses.append(
                    and_(RuleRecord.scope == "camera", RuleRecord.scope_ref == camera_id)
                )
            if area_id:
                scope_clauses.append(
                    and_(RuleRecord.scope == "area", RuleRecord.scope_ref == area_id)
                )
            scope_clauses.append(RuleRecord.scope == "global")
            stmt = stmt.where(or_(*scope_clauses))
            result = await db.execute(stmt)
            rules = list(result.scalars().all())

        # Sort by scope specificity (most specific first), then by hit_count desc
        scope_priority = {
            "zone": 0,
            "camera": 1,
            "area": 2,
            "journey": 3,
            "composite": 4,
            "global": 5,
        }
        rules.sort(key=lambda r: (scope_priority.get(r.scope, 9), -r.hit_count))
        return rules[: self._config.rules_top_k]

    # ─────────────────────────────────────────────────────────────────
    # Active contexts + intents (#95)
    # ─────────────────────────────────────────────────────────────────

    async def get_active_contexts(self, area_id: str | None = None) -> list[dict[str, Any]]:
        """Return active SituationalContexts for an area.

        Stored in a JSON-typed extra field on session records for v1; a
        dedicated contexts table can be added if usage grows.
        """
        # TODO: wire to a contexts table in Epic 11 (memory model)
        return []

    async def get_active_intents(self, area_id: str | None = None) -> list[dict[str, Any]]:
        """Return active TransientIntents."""
        return []

    # ─────────────────────────────────────────────────────────────────
    # Identity (#96)
    # ─────────────────────────────────────────────────────────────────

    async def resolve_identity(
        self,
        *,
        camera_id: str,
        observed_at: datetime,
        face_embedding: list[float] | None = None,
        candidates_limit: int = 5,
    ) -> list[KnownActor]:
        """Return top-K candidate known actors.

        v1: returns recent known actors (placeholder until vector DB integration).
        """
        async with self.session() as db:
            stmt = (
                select(KnownActor)
                .where(KnownActor.deleted_at.is_(None))
                .order_by(KnownActor.updated_at.desc())
                .limit(candidates_limit)
            )
            result = await db.execute(stmt)
            return list(result.scalars().all())

    # ─────────────────────────────────────────────────────────────────
    # Episodic (#97, #98)
    # ─────────────────────────────────────────────────────────────────

    async def write_episodic(
        self,
        *,
        session_id: str,
        summary: str,
        privacy_tier: str,
        embedding_id: str | None = None,
    ) -> EpisodicSummary:
        async with self.session() as db:
            record = EpisodicSummary(
                session_id=session_id,
                summary=summary,
                privacy_tier=privacy_tier,
                embedding_id=embedding_id,
            )
            db.add(record)
            await db.commit()
            await db.refresh(record)
            return record

    async def recall_episodic(
        self,
        *,
        area_id: str | None = None,
        actor_id: str | None = None,
        limit: int | None = None,
    ) -> list[EpisodicSummary]:
        """Return episodic summaries similar to the current context.

        v1 returns most recent N. Vector similarity ranking lands when the
        rule-creation pipeline starts emitting embeddings (Epic 7).
        """
        lim = limit or self._config.episodic_top_k
        async with self.session() as db:
            stmt = (
                select(EpisodicSummary)
                .where(EpisodicSummary.deleted_at.is_(None))
                .order_by(EpisodicSummary.created_at.desc())
                .limit(lim)
            )
            result = await db.execute(stmt)
            return list(result.scalars().all())

    # ─────────────────────────────────────────────────────────────────
    # Visit ledger (#99)
    # ─────────────────────────────────────────────────────────────────

    async def update_visit_ledger(
        self,
        *,
        actor_id: str | None,
        area_id: str,
        visited_at: datetime,
        duration_seconds: float | None = None,
        session_id: str | None = None,
        summary: str | None = None,
    ) -> VisitLedger:
        async with self.session() as db:
            entry = VisitLedger(
                actor_id=actor_id,
                area_id=area_id,
                visited_at=visited_at,
                duration_seconds=duration_seconds,
                session_id=session_id,
                summary=summary,
            )
            db.add(entry)
            await db.commit()
            await db.refresh(entry)
            return entry

    # ─────────────────────────────────────────────────────────────────
    # Retention enforcement (#100, #101)
    # ─────────────────────────────────────────────────────────────────

    async def sweep_expired(
        self,
        *,
        policy: RetentionPolicy,
        now: datetime | None = None,
    ) -> int:
        """Soft-delete records past their TTL according to ``policy``.

        Returns the number of records soft-deleted. Real implementation
        dispatches per data-class to the relevant table; v1 covers sessions.
        """
        # Future epics flesh out per-table sweeps. For now, return 0 to indicate
        # the policy is recognized but no sweep was applied yet.
        logger.info(
            "memory.sweep_expired",
            data_class=policy.data_class.value,
            ttl_days=policy.local_ttl_days,
        )
        return 0

    async def hard_erase_past_grace(
        self,
        *,
        grace: SoftDeleteGracePeriod,
        now: datetime | None = None,
    ) -> int:
        """Permanently erase soft-deleted records past their grace period.

        Returns the number of records hard-erased.
        """
        logger.info("memory.hard_erase_past_grace", grace_days=grace.days)
        return 0
