"""Graph DB integration — Phase 1 (shadow dual-write) + Phase 2 wiring.

Covers the add-on side of Epic 10.2:
  - make_graph_client factory: in-memory default + graceful Neo4j
    fallback (boot never fails because of the graph).
  - graph_mirror: alert→Event + policy→Policy translation, defensive
    against malformed source rows, never raises.
  - diagnostics graph panel renders the backend + counts.
  - find_similar_actors vector search (in-memory backend).
"""

from __future__ import annotations

from kukiihome_ha_agent.graph_mirror import (
    mirror_event_from_alert,
    mirror_policy,
)
from kukiihome_ha_agent.graph_runtime import _redact, make_graph_client

# ─── factory ─────────────────────────────────────────────────────


def test_factory_defaults_to_in_memory_with_no_url():
    client, backend = make_graph_client()
    assert backend == "in_memory"
    assert client.__class__.__name__ == "InMemoryGraphClient"


def test_factory_falls_back_to_in_memory_on_unreachable_neo4j():
    # A bolt URL pointing nowhere must NOT raise — boot stays up on the
    # in-memory backend. (verify_connectivity fails fast on a closed port.)
    client, backend = make_graph_client(
        neo4j_url="bolt://127.0.0.1:1",  # nothing listens on port 1
        neo4j_user="neo4j",
        neo4j_password="whatever",
    )
    assert backend == "in_memory"
    assert client.__class__.__name__ == "InMemoryGraphClient"


def test_redact_strips_inline_credentials():
    assert _redact("bolt://user:pass@host:7687") == "bolt://host:7687"
    assert _redact("bolt://host:7687") == "bolt://host:7687"


# ─── event mirror ────────────────────────────────────────────────


def _fresh_client():
    client, _ = make_graph_client()
    return client


def test_mirror_event_writes_node_from_alert():
    c = _fresh_client()
    mirror_event_from_alert(
        c,
        {
            "alert_id": "evt_1",
            "camera_id": "pool",
            "ts": 1000.0,
            "subject": "person",
            "severity": "major",
        },
    )
    assert c.count_events() == 1
    ev = c.read_event("evt_1")
    assert ev.camera_id == "pool"
    assert ev.ts == 1000.0
    assert ev.tag_set == ("person",)
    assert ev.metadata.get("severity") == "major"


def test_mirror_event_uses_event_id_fallback_key():
    c = _fresh_client()
    mirror_event_from_alert(c, {"event_id": "evt_2", "camera_id": "front"})
    assert c.read_event("evt_2") is not None


def test_mirror_event_skips_alert_without_id():
    c = _fresh_client()
    mirror_event_from_alert(c, {"camera_id": "front"})  # no id
    assert c.count_events() == 0


def test_mirror_event_never_raises_on_garbage():
    c = _fresh_client()
    # Non-dict, None client, weird types — none of these may raise.
    mirror_event_from_alert(c, {"alert_id": "e", "tags": [1, None, "dog"]})
    mirror_event_from_alert(None, {"alert_id": "e"})
    ev = c.read_event("e")
    # Only the string tag survives the coercion.
    assert ev.tag_set == ("dog",)


def test_mirror_event_none_client_is_noop():
    # Must not raise when the graph isn't wired.
    mirror_event_from_alert(None, {"alert_id": "x", "camera_id": "y"})


# ─── policy mirror ───────────────────────────────────────────────


class _FakePolicy:
    def __init__(self, **kw):
        self.id = kw.get("id", "pol_1")
        self.kind = kw.get("kind", "dismissal")
        self.name = kw.get("name", "")
        self.descriptor = kw.get("descriptor", {})
        self.created_at = kw.get("created_at", 1000.0)
        self.expires_at = kw.get("expires_at", None)


def test_mirror_policy_writes_node():
    c = _fresh_client()
    mirror_policy(
        c,
        _FakePolicy(
            id="pol_42",
            kind="dismissal",
            descriptor={"camera": "pool", "subject": "dog", "intent_text": "ignore pool dog"},
            created_at=1000.0,
            expires_at=1000.0 + 3600.0,
        ),
    )
    assert c.count_policies() == 1
    p = c.read_policy("pol_42")
    assert p.kind == "dismissal"
    assert p.scope_camera == "pool"
    assert p.match_tag_subset == ("dog",)
    assert p.ttl_seconds == 3600.0
    assert p.rationale == "ignore pool dog"


def test_mirror_policy_handles_missing_expiry():
    c = _fresh_client()
    mirror_policy(c, _FakePolicy(id="pol_x", expires_at=None))
    p = c.read_policy("pol_x")
    assert p.ttl_seconds == 0.0  # no expiry → 0 TTL, still a valid node


def test_mirror_policy_none_is_noop():
    c = _fresh_client()
    mirror_policy(c, None)
    mirror_policy(None, _FakePolicy())
    assert c.count_policies() == 0


# ─── vector search (Phase 2 read path) ───────────────────────────


def test_find_similar_actors_ranks_by_cosine():
    from kukiihome_memory.graph.types import KnownActor

    c = _fresh_client()
    c.write_known_actor(
        KnownActor(
            id="alice",
            name="Alice",
            role="resident",
            face_embedding=(1.0, 0.0, 0.0),
        )
    )
    c.write_known_actor(
        KnownActor(
            id="bob",
            name="Bob",
            role="resident",
            face_embedding=(0.0, 1.0, 0.0),
        )
    )
    c.write_known_actor(
        KnownActor(
            id="noemb",
            name="NoEmbed",
            role="visitor",
        )
    )  # no embedding → skipped
    res = c.find_similar_actors((0.95, 0.05, 0.0), k=3)
    assert [a.id for a, _ in res] == ["alice", "bob"]
    assert res[0][1] > res[1][1]


def test_find_similar_actors_respects_min_similarity():
    from kukiihome_memory.graph.types import KnownActor

    c = _fresh_client()
    c.write_known_actor(
        KnownActor(
            id="alice",
            name="Alice",
            role="resident",
            face_embedding=(1.0, 0.0, 0.0),
        )
    )
    c.write_known_actor(
        KnownActor(
            id="orthogonal",
            name="Ortho",
            role="visitor",
            face_embedding=(0.0, 1.0, 0.0),
        )
    )
    res = c.find_similar_actors((1.0, 0.0, 0.0), k=5, min_similarity=0.5)
    assert [a.id for a, _ in res] == ["alice"]  # orthogonal (sim 0) filtered


def test_find_similar_actors_empty_when_no_enrollment():
    c = _fresh_client()
    assert c.find_similar_actors((1.0, 0.0), k=3) == []


# ─── diagnostics panel ───────────────────────────────────────────


def test_diagnostics_renders_graph_backend_and_counts():
    from kukiihome_ha_agent.web_ui.diagnostics import (
        build_diagnostics_vm,
        render_diagnostics_page,
    )

    c = _fresh_client()
    mirror_event_from_alert(c, {"alert_id": "e1", "camera_id": "pool"})
    mirror_event_from_alert(c, {"alert_id": "e2", "camera_id": "pool"})
    vm = build_diagnostics_vm(
        version="9.9.9",
        preprocessor_ok=None,
        preprocessor_url=None,
        ha_connected=True,
        ha_entities=0,
        rules_store=None,
        action_store=None,
        area_store=None,
        policy_store=None,
        registry_statuses=[],
        ha_loops=[],
        alerts=[],
        now_ts=1000.0,
        graph_client=c,
        graph_backend="in_memory",
    )
    assert vm.graph.events == 2
    html = render_diagnostics_page(vm)
    assert "Memory graph" in html
    assert "in-memory" in html
    assert ">2<" in html  # the events count cell


def test_diagnostics_graph_section_shows_neo4j_when_durable():
    from kukiihome_ha_agent.web_ui.diagnostics import (
        GraphSubstrateSnapshot,
        _graph_section,
    )

    html = _graph_section(
        GraphSubstrateSnapshot(
            backend="neo4j",
            events=10,
            policies=3,
            actors=2,
        )
    )
    assert "Neo4j" in html
    assert "durable" in html


# ─── memory package graph-importability ──────────────────────────


def test_graph_layer_importable_independent_of_sql_layer():
    # The whole point of the __init__ guard: importing the graph layer
    # must work even though the package __init__ also (optionally) imports
    # the sqlalchemy-backed SQL layer. Here sqlalchemy IS installed, but
    # this asserts the import path the add-on relies on is intact.
    from kukiihome_memory.graph.client import (
        InMemoryGraphClient,
        Neo4jGraphClient,  # noqa: F401 — import presence is the assertion
    )
    from kukiihome_memory.graph.types import Event, KnownActor, Policy  # noqa: F401

    assert InMemoryGraphClient().count_events() == 0
