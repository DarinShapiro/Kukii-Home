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
    await cache.upsert(ActorEnrollmentEvent(actor_id="a", action="enrolled", name="Old"))
    await cache.upsert(ActorEnrollmentEvent(actor_id="a", action="updated", name="New"))
    fetched = await cache.get("a")
    assert fetched is not None
    assert fetched.name == "New"


@pytest.mark.asyncio
async def test_upsert_merges_face_then_body_retains_both():
    """Face and body are enrolled by independent publishers. Enrolling
    a body-only event must NOT wipe a previously-cached face embedding —
    one actor accumulates both modalities across separate events."""
    cache = ActorCache()
    face = tuple(0.1 * i for i in range(4))
    body = tuple(0.5 - 0.05 * i for i in range(6))
    await cache.upsert(
        ActorEnrollmentEvent(actor_id="darin", action="enrolled", name="Darin", face_embedding=face)
    )
    await cache.upsert(
        ActorEnrollmentEvent(actor_id="darin", action="updated", body_embedding=body)
    )
    fetched = await cache.get("darin")
    assert fetched is not None
    assert fetched.face_embedding == face  # preserved
    assert fetched.body_embedding == body  # added
    assert fetched.name == "Darin"  # preserved (incoming left it None)


@pytest.mark.asyncio
async def test_upsert_incoming_non_none_overlays_existing():
    """A new value for an already-set field wins (re-enrollment)."""
    cache = ActorCache()
    await cache.upsert(
        ActorEnrollmentEvent(actor_id="a", action="enrolled", face_embedding=(0.0, 0.0))
    )
    await cache.upsert(
        ActorEnrollmentEvent(actor_id="a", action="updated", face_embedding=(1.0, 1.0))
    )
    fetched = await cache.get("a")
    assert fetched is not None
    assert fetched.face_embedding == (1.0, 1.0)


@pytest.mark.asyncio
async def test_upsert_none_field_does_not_clear_cached_value():
    """A None field in an incoming event falls back to the cached
    value — it does not clear it (delta-merge semantics)."""
    cache = ActorCache()
    face = (0.2, 0.3, 0.4)
    await cache.upsert(
        ActorEnrollmentEvent(actor_id="a", action="enrolled", name="Al", face_embedding=face)
    )
    # Bare update carrying no embeddings / metadata.
    await cache.upsert(ActorEnrollmentEvent(actor_id="a", action="updated"))
    fetched = await cache.get("a")
    assert fetched is not None
    assert fetched.face_embedding == face
    assert fetched.name == "Al"


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
        await cache.upsert(ActorEnrollmentEvent(actor_id="a", action="deactivated"))


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
