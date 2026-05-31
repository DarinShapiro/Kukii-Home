"""Unit tests for the GraphSnapshot + diff utility.

Exercises the diff logic in isolation against the InMemoryGraphClient
so the diff is testable without Docker. Tests every kind of
divergence the diff is supposed to catch.
"""

from __future__ import annotations

from kukiihome_memory.graph import (
    CitedEdge,
    Event,
    InMemoryGraphClient,
    KnownActor,
    Policy,
    VLMDecision,
)
from synthesis.snapshot import GraphSnapshot, diff_snapshots

# ─── No-op cases ─────────────────────────────────────────────────────


def test_empty_snapshots_agree():
    a = InMemoryGraphClient()
    b = InMemoryGraphClient()
    assert diff_snapshots(GraphSnapshot.from_client(a), GraphSnapshot.from_client(b)) == []


def test_identical_clients_snapshot_equal():
    """Same events + actors + decisions + policies + edges → no diff."""
    a = _populated_client()
    b = _populated_client()
    diffs = diff_snapshots(GraphSnapshot.from_client(a), GraphSnapshot.from_client(b))
    assert diffs == []


# ─── Divergences the diff must catch ─────────────────────────────────


def test_missing_event_surfaces_as_diff():
    a = _populated_client()
    b = _populated_client()
    # Drop an event from B.
    b._events.pop("evt_1")
    diffs = diff_snapshots(GraphSnapshot.from_client(a), GraphSnapshot.from_client(b))
    assert any("Event 'evt_1' present in A but missing in B" in d for d in diffs)


def test_extra_actor_surfaces_as_diff():
    a = _populated_client()
    b = _populated_client()
    b.write_known_actor(KnownActor(id="actor_extra", name="Surprise", role="visitor_unknown"))
    diffs = diff_snapshots(GraphSnapshot.from_client(a), GraphSnapshot.from_client(b))
    assert any("KnownActor 'actor_extra' present in B but missing in A" in d for d in diffs)


def test_diverging_event_camera_id_surfaces_as_diff():
    a = _populated_client()
    b = _populated_client()
    # Rewrite evt_1 with a different camera_id in B.
    original = b._events["evt_1"]
    b.write_event(
        Event(
            id=original.id,
            ts=original.ts,
            camera_id="other_cam",
            tag_set=original.tag_set,
            matched_actor_ids=original.matched_actor_ids,
        )
    )
    diffs = diff_snapshots(GraphSnapshot.from_client(a), GraphSnapshot.from_client(b))
    assert any("camera_id differs" in d for d in diffs)


def test_diverging_edge_weight_surfaces_as_diff():
    a = _populated_client()
    b = _populated_client()
    b.write_cited_edge(
        CitedEdge(
            decision_id="dec_1",
            memory_id="actor_1",
            weight=0.99,
            created_ts=100.0,
            last_reinforced_ts=100.0,
        )
    )
    diffs = diff_snapshots(GraphSnapshot.from_client(a), GraphSnapshot.from_client(b))
    assert any("weight differs" in d for d in diffs)


def test_extra_policy_surfaces_as_diff():
    a = _populated_client()
    b = _populated_client()
    b.write_policy(
        Policy(
            id="pol_extra",
            kind="dismissal",
            scope_camera="front_cam",
            match_tag_subset=("person",),
            ttl_seconds=3600,
            created_ts=100.0,
        )
    )
    diffs = diff_snapshots(GraphSnapshot.from_client(a), GraphSnapshot.from_client(b))
    assert any("Policy 'pol_extra' present in B but missing in A" in d for d in diffs)


def test_diverging_policy_match_subset_surfaces_as_diff():
    a = _populated_client()
    b = _populated_client()
    # Overwrite pol_1's match_tag_subset.
    original = b._policies["pol_1"]
    b.write_policy(
        Policy(
            id=original.id,
            kind=original.kind,
            scope_camera=original.scope_camera,
            match_tag_subset=("person", "vehicle"),  # was ("dog",)
            ttl_seconds=original.ttl_seconds,
            created_ts=original.created_ts,
            rationale=original.rationale,
        )
    )
    diffs = diff_snapshots(GraphSnapshot.from_client(a), GraphSnapshot.from_client(b))
    assert any("match_tag_subset differs" in d for d in diffs)


# ─── Float tolerance ─────────────────────────────────────────────────


def test_negligible_float_drift_is_ignored():
    """1e-12 drift in a timestamp shouldn't surface as a diff —
    Neo4j round-trips through doubles and can introduce sub-ULP noise."""
    a = _populated_client()
    b = _populated_client()
    original = b._events["evt_1"]
    b.write_event(
        Event(
            id=original.id,
            ts=original.ts + 1e-12,
            camera_id=original.camera_id,
            tag_set=original.tag_set,
            matched_actor_ids=original.matched_actor_ids,
        )
    )
    diffs = diff_snapshots(GraphSnapshot.from_client(a), GraphSnapshot.from_client(b))
    assert diffs == [], f"sub-tolerance drift surfaced as diff: {diffs}"


# ─── Helper ──────────────────────────────────────────────────────────


def _populated_client() -> InMemoryGraphClient:
    """A small, deterministic graph: 2 events, 1 actor, 1 decision,
    1 policy, 1 citation. Used as the 'identical' baseline both
    clients build from."""
    client = InMemoryGraphClient()

    client.write_event(
        Event(
            id="evt_1",
            ts=100.0,
            camera_id="front_cam",
            tag_set=("person",),
            matched_actor_ids=("actor_1",),
        )
    )
    client.write_event(
        Event(
            id="evt_2",
            ts=200.0,
            camera_id="back_cam",
            tag_set=("dog",),
            matched_actor_ids=(),
        )
    )

    client.write_known_actor(
        KnownActor(id="actor_1", name="Alice", role="resident", access_profile="full")
    )

    client.write_vlm_decision(
        VLMDecision(
            id="dec_1",
            ts=100.0,
            triggered_by_event_id="evt_1",
            findings_summary="alice arriving home",
        )
    )

    client.write_cited_edge(
        CitedEdge(
            decision_id="dec_1",
            memory_id="actor_1",
            weight=0.7,
            created_ts=100.0,
            last_reinforced_ts=100.0,
        )
    )

    client.write_policy(
        Policy(
            id="pol_1",
            kind="dismissal",
            scope_camera="back_cam",
            match_tag_subset=("dog",),
            ttl_seconds=86_400,
            created_ts=100.0,
        )
    )

    return client
