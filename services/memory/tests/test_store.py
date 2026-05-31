"""Tests for the MemoryStore facade — uses SQLite-in-memory for unit-test speed.

Real Postgres exercising lives in integration tests.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from kukiihome_memory.models import RuleRecord
from kukiihome_memory.store import MemoryStore, MemoryStoreConfig

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def store():
    """MemoryStore backed by SQLite in-memory."""
    store = MemoryStore(MemoryStoreConfig(database_url="sqlite+aiosqlite:///:memory:"))
    await store.init_schema()
    yield store
    await store.close()


async def test_open_and_get_session(store: MemoryStore) -> None:
    s = await store.open_session(
        session_id="sess_a",
        subject_descriptor="dog",
        opened_at=datetime.utcnow(),
        privacy_tier="cloud_eligible",
    )
    assert s.session_id == "sess_a"

    loaded = await store.get_session("sess_a")
    assert loaded is not None
    assert loaded.subject_descriptor == "dog"


async def test_close_session(store: MemoryStore) -> None:
    await store.open_session(
        session_id="sess_b",
        subject_descriptor=None,
        opened_at=datetime.utcnow(),
        privacy_tier="cloud_eligible",
    )
    closed = await store.close_session(session_id="sess_b", closed_at=datetime.utcnow())
    assert closed is not None
    assert closed.status == "closed"
    assert closed.closed_at is not None


async def test_append_segment(store: MemoryStore) -> None:
    await store.open_session(
        session_id="sess_c",
        subject_descriptor=None,
        opened_at=datetime.utcnow(),
        privacy_tier="cloud_eligible",
    )
    seg = await store.append_segment(
        session_id="sess_c",
        camera_id="front_door",
        started_at=datetime.utcnow(),
        detection_count=5,
    )
    assert seg.detection_count == 5

    loaded = await store.get_session("sess_c")
    assert loaded is not None
    assert len(loaded.segments) == 1


async def test_close_session_unknown_id_returns_none(store: MemoryStore) -> None:
    result = await store.close_session(session_id="does_not_exist", closed_at=datetime.utcnow())
    assert result is None


async def test_retrieve_rules_filters_by_scope(store: MemoryStore) -> None:
    # Seed three rules at different scopes
    async with store.session() as db:
        db.add(
            RuleRecord(
                rule_id="r_zone",
                text="zone rule",
                scope="zone",
                scope_ref="entry_mat",
                severity="alert",
            )
        )
        db.add(
            RuleRecord(
                rule_id="r_area",
                text="area rule",
                scope="area",
                scope_ref="front_door",
                severity="warning",
            )
        )
        db.add(
            RuleRecord(
                rule_id="r_global",
                text="global rule",
                scope="global",
                scope_ref=None,
                severity="info",
            )
        )
        db.add(
            RuleRecord(
                rule_id="r_other_area",
                text="unrelated",
                scope="area",
                scope_ref="backyard",
                severity="info",
            )
        )
        await db.commit()

    rules = await store.retrieve_rules(area_id="front_door", zone_id="entry_mat")
    rule_ids = {r.rule_id for r in rules}
    # Should include zone + area + global; exclude the other area
    assert "r_zone" in rule_ids
    assert "r_area" in rule_ids
    assert "r_global" in rule_ids
    assert "r_other_area" not in rule_ids


async def test_retrieve_rules_excludes_soft_deleted(store: MemoryStore) -> None:
    async with store.session() as db:
        r = RuleRecord(rule_id="r_del", text="deleted", scope="global")
        r.deleted_at = datetime.utcnow()
        db.add(r)
        await db.commit()
    rules = await store.retrieve_rules()
    assert all(r.rule_id != "r_del" for r in rules)


async def test_retrieve_rules_excludes_suppressed(store: MemoryStore) -> None:
    from datetime import timedelta

    async with store.session() as db:
        r = RuleRecord(rule_id="r_supp", text="suppressed", scope="global")
        r.suppress_until = datetime.utcnow() + timedelta(hours=1)
        db.add(r)
        await db.commit()
    rules = await store.retrieve_rules()
    assert all(r.rule_id != "r_supp" for r in rules)


async def test_retrieve_rules_top_k_cap(store: MemoryStore) -> None:
    async with store.session() as db:
        for i in range(20):
            db.add(RuleRecord(rule_id=f"r_{i}", text=f"r {i}", scope="global"))
        await db.commit()
    rules = await store.retrieve_rules()
    assert len(rules) == 5  # config default


async def test_write_and_recall_episodic(store: MemoryStore) -> None:
    await store.open_session(
        session_id="sess_ep",
        subject_descriptor="visitor",
        opened_at=datetime.utcnow(),
        privacy_tier="cloud_eligible",
    )
    await store.write_episodic(
        session_id="sess_ep",
        summary="Visitor at door for 2 minutes",
        privacy_tier="cloud_eligible",
    )
    recalled = await store.recall_episodic()
    assert len(recalled) == 1
    assert recalled[0].summary.startswith("Visitor")


async def test_update_visit_ledger(store: MemoryStore) -> None:
    entry = await store.update_visit_ledger(
        actor_id="sarah",
        area_id="front_door",
        visited_at=datetime.utcnow(),
        duration_seconds=60.0,
    )
    assert entry.id is not None
    assert entry.duration_seconds == 60.0


async def test_sweep_expired_returns_zero_for_now(store: MemoryStore) -> None:
    from kukiihome_memory.retention import DataClass, RetentionPolicy

    count = await store.sweep_expired(policy=RetentionPolicy.default_for(DataClass.B_INTERIOR))
    assert count == 0  # placeholder until per-table sweep ships
