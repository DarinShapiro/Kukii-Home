"""Tests for the memory ORM models + retention policy.

Uses SQLite in-memory for fast unit tests. Real Postgres exercising happens
in integration tests (Epic 6 #104).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from kukiihome_memory.models import (
    AuditLog,
    Base,
    CameraRecord,
    CloudEgressAudit,
    EpisodicSummary,
    IdentityRecord,
    KnownActor,
    RuleRecord,
    Session,
    SessionSegment,
    VisitLedger,
    ZoneRecord,
)
from kukiihome_memory.retention import DataClass, RetentionPolicy, SoftDeleteGracePeriod
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def session():
    """In-memory SQLite session for fast model tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


# ─────────────────────────────────────────────────────────────────────
# Model persistence
# ─────────────────────────────────────────────────────────────────────


async def test_session_persists(session: AsyncSession) -> None:
    s = Session(
        session_id="sess_1",
        subject_descriptor="unknown adult",
        opened_at=datetime.utcnow(),
        privacy_tier="cloud_eligible",
    )
    session.add(s)
    await session.commit()

    loaded = await session.get(Session, "sess_1")
    assert loaded is not None
    assert loaded.subject_descriptor == "unknown adult"
    assert loaded.status == "open"


async def test_session_segment_relationship(session: AsyncSession) -> None:
    s = Session(
        session_id="sess_2",
        opened_at=datetime.utcnow(),
        privacy_tier="cloud_eligible",
    )
    session.add(s)
    await session.commit()

    seg = SessionSegment(
        session_id="sess_2",
        camera_id="front_door",
        started_at=datetime.utcnow(),
        detection_count=3,
    )
    session.add(seg)
    await session.commit()

    loaded = await session.get(SessionSegment, seg.id)
    assert loaded is not None
    assert loaded.session_id == "sess_2"


async def test_rule_record_with_json_fields(session: AsyncSession) -> None:
    r = RuleRecord(
        rule_id="rule_1",
        text="Alert if dog in front yard without person",
        scope="area",
        scope_ref="front_yard",
        conditions={"subject_type": "pet", "context_required": ["alone"]},
        actions=[{"type": "notify", "targets": ["resident_1"]}],
        severity="alert",
        confidence_required=0.6,
    )
    session.add(r)
    await session.commit()

    loaded = await session.get(RuleRecord, "rule_1")
    assert loaded is not None
    assert loaded.conditions["subject_type"] == "pet"
    assert loaded.actions[0]["type"] == "notify"


async def test_known_actor_with_resident_flag(session: AsyncSession) -> None:
    a = KnownActor(
        actor_id="sarah_doe",
        display_name="Sarah",
        is_resident=True,
        privacy_level="high",
    )
    session.add(a)
    await session.commit()

    loaded = await session.get(KnownActor, "sarah_doe")
    assert loaded is not None
    assert loaded.is_resident is True


async def test_identity_record_persists(session: AsyncSession) -> None:
    a = KnownActor(actor_id="bob", display_name="Bob")
    session.add(a)
    await session.commit()

    rec = IdentityRecord(
        actor_id="bob",
        camera_id="front_door",
        observed_at=datetime.utcnow(),
        confidence=0.87,
        method="face",
        evidence={"face_score": 0.87, "reid_score": 0.7},
    )
    session.add(rec)
    await session.commit()

    loaded = await session.get(IdentityRecord, rec.id)
    assert loaded is not None
    assert loaded.method == "face"


async def test_visit_ledger(session: AsyncSession) -> None:
    v = VisitLedger(
        actor_id="bob",
        area_id="front_door",
        visited_at=datetime.utcnow(),
        duration_seconds=42.5,
    )
    session.add(v)
    await session.commit()
    assert v.id is not None


async def test_episodic_summary(session: AsyncSession) -> None:
    s = Session(
        session_id="sess_3",
        opened_at=datetime.utcnow(),
        privacy_tier="cloud_eligible",
    )
    session.add(s)
    await session.commit()

    e = EpisodicSummary(
        session_id="sess_3",
        summary="Mailman delivered at 14:30, no anomalies.",
        privacy_tier="cloud_eligible",
    )
    session.add(e)
    await session.commit()
    assert e.id is not None


async def test_audit_log_append(session: AsyncSession) -> None:
    log = AuditLog(
        event_type="rule_fire",
        actor="system",
        subject_id="rule_1",
        trace_id="a" * 32,
    )
    session.add(log)
    await session.commit()
    assert log.id is not None


async def test_cloud_egress_audit(session: AsyncSession) -> None:
    e = CloudEgressAudit(
        sent_at=datetime.utcnow(),
        data_type="scene_json",
        privacy_tier="cloud_eligible",
        size_bytes=45_000,
        destination="cloud_vlm_api",
        scrubbed=True,
        scrub_details="face blurred",
        initiated_by="system",
    )
    session.add(e)
    await session.commit()
    assert e.id is not None


async def test_camera_record(session: AsyncSession) -> None:
    c = CameraRecord(
        camera_id="front_door",
        name="Front Door",
        role="fixed",
        area_id="entry",
        streams={"main": "rtsp://192.168.1.10/main"},
    )
    session.add(c)
    await session.commit()
    loaded = await session.get(CameraRecord, "front_door")
    assert loaded is not None
    assert loaded.streams["main"].startswith("rtsp://")


async def test_zone_record(session: AsyncSession) -> None:
    z = ZoneRecord(
        zone_id="entry_mat",
        area_id="entry",
        name="Entry mat",
        zone_type="image_space",
        polygon=[[100.0, 200.0], [400.0, 200.0], [400.0, 400.0], [100.0, 400.0]],
    )
    session.add(z)
    await session.commit()
    loaded = await session.get(ZoneRecord, "entry_mat")
    assert loaded is not None
    assert len(loaded.polygon) == 4


async def test_soft_delete_flag(session: AsyncSession) -> None:
    a = KnownActor(actor_id="visitor_x", display_name="Visitor X")
    session.add(a)
    await session.commit()
    assert a.is_deleted is False
    a.deleted_at = datetime.utcnow()
    await session.commit()
    await session.refresh(a)
    assert a.is_deleted is True


# ─────────────────────────────────────────────────────────────────────
# Retention policy (pure-function)
# ─────────────────────────────────────────────────────────────────────


def test_default_retention_for_each_data_class() -> None:
    a = RetentionPolicy.default_for(DataClass.A_RESIDENT_BIOMETRIC)
    assert a.local_ttl_days is None  # indefinite

    b = RetentionPolicy.default_for(DataClass.B_INTERIOR)
    assert b.local_ttl_days == 14

    c = RetentionPolicy.default_for(DataClass.C_EXTERIOR)
    assert c.local_ttl_days == 30
    assert c.cloud_ttl_days == 90

    d = RetentionPolicy.default_for(DataClass.D_DETECTION)
    assert d.local_ttl_days == 90


def test_is_expired_returns_true_past_ttl() -> None:
    policy = RetentionPolicy.default_for(DataClass.B_INTERIOR)
    old = datetime.utcnow() - timedelta(days=20)
    assert policy.is_expired(old) is True


def test_is_expired_returns_false_within_ttl() -> None:
    policy = RetentionPolicy.default_for(DataClass.B_INTERIOR)
    recent = datetime.utcnow() - timedelta(days=5)
    assert policy.is_expired(recent) is False


def test_class_a_never_expires() -> None:
    policy = RetentionPolicy.default_for(DataClass.A_RESIDENT_BIOMETRIC)
    long_ago = datetime.utcnow() - timedelta(days=10_000)
    assert policy.is_expired(long_ago) is False


def test_grace_period_past_window() -> None:
    grace = SoftDeleteGracePeriod(days=7)
    long_ago = datetime.utcnow() - timedelta(days=10)
    assert grace.is_past_grace(long_ago) is True


def test_grace_period_within_window() -> None:
    grace = SoftDeleteGracePeriod(days=7)
    recent = datetime.utcnow() - timedelta(days=3)
    assert grace.is_past_grace(recent) is False
