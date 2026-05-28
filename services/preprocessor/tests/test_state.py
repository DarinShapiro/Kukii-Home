"""Unit tests for the ActorCache.

Covers the three mutation paths (upsert, remove, snapshot) plus the
defensive guard that refuses upsert of deactivation events.
"""

from __future__ import annotations

import pytest
from sentihome_preprocessor.state import ActorCache
from sentihome_shared.preprocessor import ActorEnrollmentEvent


@pytest.mark.asyncio
async def test_upsert_then_get_returns_event():
    cache = ActorCache()
    ev = ActorEnrollmentEvent(
        actor_id="actor_alice",
        action="enrolled",
        name="Alice",
        face_embedding=tuple(0.1 * i for i in range(4)),
    )
    await cache.upsert(ev)
    fetched = await cache.get("actor_alice")
    assert fetched == ev


@pytest.mark.asyncio
async def test_upsert_overwrites_on_repeated_actor_id():
    cache = ActorCache()
    await cache.upsert(
        ActorEnrollmentEvent(actor_id="a", action="enrolled", name="Old")
    )
    await cache.upsert(
        ActorEnrollmentEvent(actor_id="a", action="updated", name="New")
    )
    fetched = await cache.get("a")
    assert fetched is not None
    assert fetched.name == "New"


@pytest.mark.asyncio
async def test_remove_returns_true_when_present_false_when_absent():
    cache = ActorCache()
    await cache.upsert(ActorEnrollmentEvent(actor_id="a", action="enrolled"))
    assert await cache.remove("a") is True
    assert await cache.remove("a") is False
    assert await cache.get("a") is None


@pytest.mark.asyncio
async def test_upsert_refuses_deactivated_event():
    """Defensive: deactivations must go through remove(), never
    upsert(). Surfacing the mis-route loudly."""
    cache = ActorCache()
    with pytest.raises(ValueError, match="deactivat"):
        await cache.upsert(
            ActorEnrollmentEvent(actor_id="a", action="deactivated")
        )


@pytest.mark.asyncio
async def test_snapshot_is_sorted_by_id():
    cache = ActorCache()
    for actor_id in ("zebra", "alice", "milkman"):
        await cache.upsert(ActorEnrollmentEvent(actor_id=actor_id, action="enrolled"))
    snap = await cache.snapshot()
    assert [e.actor_id for e in snap] == ["alice", "milkman", "zebra"]


@pytest.mark.asyncio
async def test_size_reflects_current_population():
    cache = ActorCache()
    assert await cache.size() == 0
    await cache.upsert(ActorEnrollmentEvent(actor_id="a", action="enrolled"))
    await cache.upsert(ActorEnrollmentEvent(actor_id="b", action="enrolled"))
    assert await cache.size() == 2
    await cache.remove("a")
    assert await cache.size() == 1
