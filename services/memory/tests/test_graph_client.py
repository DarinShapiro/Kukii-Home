"""Contract tests for both GraphClient implementations.

The InMemoryGraphClient and Neo4jGraphClient must produce equivalent
behavior for the same operations. Each test is parametrized over both
backends; the Neo4j parameter skips cleanly when Docker is absent.

This dual-path setup is the foundation for differential testing: if
the two implementations ever disagree on outcomes for the same inputs,
that's an architectural alarm — one of them is wrong.
"""

from __future__ import annotations

import pytest
from kukiihome_memory.graph import (
    CitedEdge,
    Event,
    GraphClient,
    KnownActor,
    NodeKind,
)


@pytest.fixture(params=["in_memory", "neo4j"])
def graph_client(request, in_memory_client, neo4j_client) -> GraphClient:
    """Parametrize across both backends.

    Tests that take ``graph_client`` run once per backend. The neo4j
    backend skips cleanly when Docker isn't available (delegated to
    the neo4j_container fixture's own skip logic).
    """
    if request.param == "in_memory":
        return in_memory_client
    return neo4j_client


# ─── Lifecycle ────────────────────────────────────────────────────────


def test_initialize_schema_is_idempotent(graph_client: GraphClient):
    """Calling initialize_schema twice doesn't error."""
    graph_client.initialize_schema()
    graph_client.initialize_schema()  # must not raise


def test_clear_all_removes_everything(graph_client: GraphClient):
    graph_client.write_event(Event(id="evt_x", ts=1.0, camera_id="cam_a", tag_set=("person",)))
    graph_client.write_known_actor(KnownActor(id="actor_alice", name="Alice", role="resident"))

    graph_client.clear_all()

    assert graph_client.read_event("evt_x") is None
    assert graph_client.read_known_actor("actor_alice") is None


# ─── Event CRUD ───────────────────────────────────────────────────────


def test_write_event_then_read_returns_same_data(graph_client: GraphClient):
    event = Event(
        id="evt_001",
        ts=1735689600.0,
        camera_id="front_south_cam",
        tag_set=("person", "vehicle"),
        matched_actor_ids=("actor_alice",),
        metadata={"source": "ha_motion", "tier": "tier_0"},
    )
    graph_client.write_event(event)

    fetched = graph_client.read_event("evt_001")
    assert fetched is not None
    assert fetched.id == event.id
    assert fetched.ts == event.ts
    assert fetched.camera_id == event.camera_id
    # Tag set ordering is preserved by both backends.
    assert tuple(fetched.tag_set) == event.tag_set
    assert tuple(fetched.matched_actor_ids) == event.matched_actor_ids
    assert fetched.metadata == event.metadata


def test_read_event_returns_none_for_missing(graph_client: GraphClient):
    assert graph_client.read_event("evt_does_not_exist") is None


def test_write_event_is_idempotent(graph_client: GraphClient):
    """Same id, updated fields = update, not duplicate."""
    graph_client.write_event(Event(id="evt_dup", ts=1.0, camera_id="cam_a", tag_set=("person",)))
    graph_client.write_event(Event(id="evt_dup", ts=2.0, camera_id="cam_a", tag_set=("dog",)))
    fetched = graph_client.read_event("evt_dup")
    assert fetched is not None
    assert fetched.ts == 2.0
    assert tuple(fetched.tag_set) == ("dog",)


# ─── KnownActor CRUD ──────────────────────────────────────────────────


def test_write_known_actor_then_read(graph_client: GraphClient):
    actor = KnownActor(
        id="actor_alice",
        name="Alice",
        role="resident",
        face_embedding=tuple([0.1] * 128),
        access_profile="full",
    )
    graph_client.write_known_actor(actor)

    fetched = graph_client.read_known_actor("actor_alice")
    assert fetched is not None
    assert fetched.id == actor.id
    assert fetched.name == actor.name
    assert fetched.role == actor.role
    assert fetched.face_embedding is not None
    assert len(fetched.face_embedding) == 128
    assert fetched.access_profile == actor.access_profile


def test_known_actor_without_embedding(graph_client: GraphClient):
    """Enrollment-pending actors have no embedding yet."""
    actor = KnownActor(
        id="actor_new",
        name="New Visitor",
        role="visitor_unknown",
        face_embedding=None,
    )
    graph_client.write_known_actor(actor)

    fetched = graph_client.read_known_actor("actor_new")
    assert fetched is not None
    assert fetched.face_embedding is None


# ─── Vector search (Epic 10.2 identity-resolution read path) ──────────


def _unit_embedding(axis: int, dims: int = 128) -> tuple[float, ...]:
    """A 128-d one-hot-ish unit vector along ``axis`` — matches the
    vector index's configured dimensionality so both backends accept it."""
    return tuple(1.0 if i == axis else 0.0 for i in range(dims))


def test_find_similar_actors_ranks_closest_first(graph_client: GraphClient):
    """Both backends rank enrolled actors by cosine similarity to the
    query embedding — the in-memory brute force must agree with Neo4j's
    native vector index."""
    graph_client.write_known_actor(
        KnownActor(
            id="actor_alice",
            name="Alice",
            role="resident",
            face_embedding=_unit_embedding(0),
        )
    )
    graph_client.write_known_actor(
        KnownActor(
            id="actor_bob",
            name="Bob",
            role="resident",
            face_embedding=_unit_embedding(7),
        )
    )
    # Query close to Alice's axis.
    query = tuple(0.95 if i == 0 else (0.05 if i == 1 else 0.0) for i in range(128))
    results = graph_client.find_similar_actors(query, k=2)
    assert len(results) >= 1
    assert results[0][0].id == "actor_alice"
    # Score descending.
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)


def test_find_similar_actors_skips_unenrolled(graph_client: GraphClient):
    """Actors without an embedding are never returned (Neo4j's vector
    index simply doesn't index them; in-memory skips them explicitly)."""
    graph_client.write_known_actor(
        KnownActor(
            id="actor_has_emb",
            name="Has",
            role="resident",
            face_embedding=_unit_embedding(3),
        )
    )
    graph_client.write_known_actor(
        KnownActor(
            id="actor_no_emb",
            name="None",
            role="visitor",
            face_embedding=None,
        )
    )
    results = graph_client.find_similar_actors(_unit_embedding(3), k=5)
    ids = {a.id for a, _ in results}
    assert "actor_no_emb" not in ids
    assert "actor_has_emb" in ids


def test_find_similar_actors_empty_graph_returns_empty(graph_client: GraphClient):
    assert graph_client.find_similar_actors(_unit_embedding(0), k=3) == []


# ─── Citation edges ───────────────────────────────────────────────────


def test_write_cited_edge_then_read(graph_client: GraphClient):
    # Both endpoints must exist first.
    graph_client.write_event(Event(id="evt_target", ts=1.0, camera_id="cam_a", tag_set=("person",)))
    # VLMDecision is a node label we haven't added a write-method for
    # yet; for now bootstrap one via the same id-uniqueness path the
    # Neo4j backend's MATCH expects. The in-memory backend doesn't
    # actually check, so it just works there.
    _bootstrap_decision_node(graph_client, "dec_xyz")

    edge = CitedEdge(
        decision_id="dec_xyz",
        memory_id="evt_target",
        weight=0.7,
        created_ts=1.0,
        last_reinforced_ts=None,
    )
    graph_client.write_cited_edge(edge)

    from_decision = graph_client.get_citations_from("dec_xyz")
    assert len(from_decision) == 1
    assert from_decision[0].memory_id == "evt_target"
    assert from_decision[0].weight == 0.7
    assert from_decision[0].last_reinforced_ts is None

    to_memory = graph_client.get_citations_to("evt_target")
    assert len(to_memory) == 1
    assert to_memory[0].decision_id == "dec_xyz"


def test_write_cited_edge_is_idempotent(graph_client: GraphClient):
    """Re-writing the same (decision, memory) pair updates weights, not
    duplicates."""
    graph_client.write_event(Event(id="evt_z", ts=1.0, camera_id="cam_a", tag_set=("person",)))
    _bootstrap_decision_node(graph_client, "dec_z")

    graph_client.write_cited_edge(
        CitedEdge(decision_id="dec_z", memory_id="evt_z", weight=0.5, created_ts=1.0)
    )
    graph_client.write_cited_edge(
        CitedEdge(
            decision_id="dec_z",
            memory_id="evt_z",
            weight=0.9,
            created_ts=1.0,
            last_reinforced_ts=2.0,
        )
    )

    edges = graph_client.get_citations_from("dec_z")
    assert len(edges) == 1
    assert edges[0].weight == 0.9
    assert edges[0].last_reinforced_ts == 2.0


def test_get_citations_returns_empty_when_none(graph_client: GraphClient):
    assert graph_client.get_citations_from("dec_unknown") == []
    assert graph_client.get_citations_to("evt_unknown") == []


# ─── Pruning ──────────────────────────────────────────────────────────


def test_candidates_for_pruning_returns_orphan_nodes(graph_client: GraphClient):
    """A node with no citations should score 0 and appear as a candidate."""
    graph_client.write_event(Event(id="evt_orphan", ts=1.0, camera_id="cam_a", tag_set=("person",)))
    _bootstrap_decision_node(graph_client, "dec_anchor")
    graph_client.write_event(
        Event(id="evt_anchored", ts=1.0, camera_id="cam_a", tag_set=("person",))
    )
    # Need at least one edge in the graph so the "now" calculation
    # has something to anchor against. Both backends use max-edge-ts
    # as "now" in this Phase 1 implementation.
    graph_client.write_cited_edge(
        CitedEdge(
            decision_id="dec_anchor",
            memory_id="evt_anchored",
            weight=1.0,
            created_ts=1.0,
        )
    )

    # Threshold 0.1 — the anchored event has a fresh strong edge so
    # its score > threshold; the orphan has no edges (score = 0).
    candidates = graph_client.candidates_for_pruning(threshold=0.1, kind=NodeKind.EVENT)
    candidate_ids = {c.node_id for c in candidates}
    assert "evt_orphan" in candidate_ids
    assert "evt_anchored" not in candidate_ids


def test_candidates_for_pruning_respects_kind_filter(graph_client: GraphClient):
    graph_client.write_event(Event(id="evt_filter", ts=1.0, camera_id="cam_a", tag_set=("person",)))
    graph_client.write_known_actor(
        KnownActor(id="actor_filter", name="Test", role="visitor_unknown")
    )
    # Add at least one edge so candidates_for_pruning's "now"
    # calculation has data.
    _bootstrap_decision_node(graph_client, "dec_filter")
    graph_client.write_cited_edge(
        CitedEdge(
            decision_id="dec_filter",
            memory_id="evt_filter",
            weight=1.0,
            created_ts=1.0,
        )
    )

    events_only = graph_client.candidates_for_pruning(threshold=0.1, kind=NodeKind.EVENT)
    actors_only = graph_client.candidates_for_pruning(threshold=0.1, kind=NodeKind.KNOWN_ACTOR)

    event_kinds = {c.node_kind for c in events_only}
    actor_kinds = {c.node_kind for c in actors_only}
    assert event_kinds <= {NodeKind.EVENT}
    assert actor_kinds <= {NodeKind.KNOWN_ACTOR}


# ─── Helpers ──────────────────────────────────────────────────────────


def _bootstrap_decision_node(client: GraphClient, decision_id: str) -> None:
    """Create a minimal VLMDecision node so CITED edges have a source.

    Phase 1 hack: we don't have a public write_vlm_decision yet, so
    we either no-op (in-memory accepts any node id since it doesn't
    enforce node existence on edge write) or run a one-off MERGE in
    Cypher. Both backends support this via the same pathway in this
    Phase 1 minimum.
    """
    from kukiihome_memory.graph import InMemoryGraphClient, Neo4jGraphClient

    if isinstance(client, InMemoryGraphClient):
        # In-memory doesn't validate node existence on edge writes;
        # the decision_id is just a string key in the cited_edges dict.
        return

    if isinstance(client, Neo4jGraphClient):
        with client.driver.session() as session:
            session.run("MERGE (d:VLMDecision {id: $id})", id=decision_id)
