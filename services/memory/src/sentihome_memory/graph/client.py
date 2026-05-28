"""Graph client protocol + two implementations.

The protocol is what the harness, dispatcher, and future memory
service all depend on. Two production implementations:

- :class:`InMemoryGraphClient` — pure Python dicts. No external deps.
  Used by the harness's default test path. Fast (microseconds per op).
- :class:`Neo4jGraphClient` — real Neo4j 5.x via the official driver.
  Production target. Integration-tested via testcontainers.

Both satisfy the same operations contract; differential tests assert
they agree on outcomes for the same operations on the same inputs.

This is the **Phase 1** minimal surface — only what's needed to write
events, enroll actors, record citations, and run pruning. The protocol
grows as Phase 2/3 scenarios demand more operations (multi-hop
traversal, DW-PageRank retrieval, KnownVehicle/Pet, etc.).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from sentihome_memory.graph.types import (
    CitedEdge,
    Event,
    KnownActor,
    NodeKind,
    Policy,
    PruneCandidate,
    VLMDecision,
)

if TYPE_CHECKING:
    from neo4j import Driver


class GraphClient(Protocol):
    """The operations contract every graph backend must satisfy.

    All operations are synchronous. Async lifts to the caller (the
    harness drives simulated time, not real I/O; the production
    memory service wraps these in its own async surface).
    """

    # ─── lifecycle ──────────────────────────────────────────────────

    def initialize_schema(self) -> None:
        """Apply schema migrations (constraints, indexes, vector
        indexes). Idempotent — safe to call multiple times."""
        ...

    def clear_all(self) -> None:
        """Delete every node + edge in the graph. Used between tests
        in the same backend session."""
        ...

    # ─── nodes ──────────────────────────────────────────────────────

    def write_event(self, event: Event) -> None:
        """Insert or update an Event node. Idempotent on ``event.id``."""
        ...

    def read_event(self, event_id: str) -> Event | None:
        """Fetch one event by id. ``None`` if it doesn't exist."""
        ...

    def write_known_actor(self, actor: KnownActor) -> None:
        """Insert or update a KnownActor. Idempotent on ``actor.id``."""
        ...

    def read_known_actor(self, actor_id: str) -> KnownActor | None:
        """Fetch one actor by id. ``None`` if not enrolled."""
        ...

    def write_vlm_decision(self, decision: VLMDecision) -> None:
        """Insert or update a VLMDecision node. Idempotent on
        ``decision.id``. Source endpoint for CITED edges."""
        ...

    def read_vlm_decision(self, decision_id: str) -> VLMDecision | None:
        """Fetch one decision by id. ``None`` if it doesn't exist."""
        ...

    # ─── edges ──────────────────────────────────────────────────────

    def write_cited_edge(self, edge: CitedEdge) -> None:
        """Record a citation from a VLMDecision to a Memory node.

        Creates the edge if absent; updates ``weight`` +
        ``last_reinforced_ts`` if present. The decision node and the
        cited memory node must both exist (caller's responsibility).
        """
        ...

    def get_citations_from(self, decision_id: str) -> list[CitedEdge]:
        """All CITED edges originating from ``decision_id``."""
        ...

    def get_citations_to(self, memory_id: str) -> list[CitedEdge]:
        """All CITED edges pointing at ``memory_id``. Used by retention
        scoring to find how much influence a memory has accumulated."""
        ...

    # ─── policies ───────────────────────────────────────────────────

    def write_policy(self, policy: Policy) -> None:
        """Insert or update a Policy node. Idempotent on ``policy.id``."""
        ...

    def read_policy(self, policy_id: str) -> Policy | None:
        """Fetch one policy by id."""
        ...

    def list_active_policies(self, *, now_ts: float) -> list[Policy]:
        """All policies whose TTL hasn't yet expired at ``now_ts``."""
        ...

    # ─── full enumeration (differential tests, debugging) ───────────

    def list_all_events(self) -> list[Event]:
        """Every Event in the graph. Order-undefined; callers that need
        determinism should sort by id. Used by the differential
        snapshot harness to compare backends."""
        ...

    def list_all_known_actors(self) -> list[KnownActor]:
        """Every KnownActor. See :meth:`list_all_events` notes."""
        ...

    def list_all_vlm_decisions(self) -> list[VLMDecision]:
        """Every VLMDecision."""
        ...

    def list_all_policies(self) -> list[Policy]:
        """Every Policy regardless of expiry. (For active-only, use
        :meth:`list_active_policies`.)"""
        ...

    def list_all_cited_edges(self) -> list[CitedEdge]:
        """Every CITED edge in the graph."""
        ...

    # ─── queries / counts ───────────────────────────────────────────

    def count_events(self, *, camera_id: str | None = None) -> int:
        """Total Event nodes; optionally filtered by camera."""
        ...

    def count_vlm_decisions(self) -> int:
        """Total VLMDecision nodes. Useful for asserting "VLM was/wasn't
        invoked N times in this scenario.\""""
        ...

    def count_policies(self, *, kind: str | None = None) -> int:
        """Total Policy nodes; optionally filtered by kind
        (``dismissal`` / ``transient_intent``)."""
        ...

    # ─── pruning ────────────────────────────────────────────────────

    def candidates_for_pruning(
        self,
        *,
        threshold: float,
        kind: NodeKind | None = None,
        now_ts: float | None = None,
    ) -> list[PruneCandidate]:
        """Return nodes whose pruning score is below ``threshold``.

        Pruning score = ``max(edge_weight * decay_factor)`` over all
        incident edges, where ``decay_factor`` is evaluated at
        ``now_ts``. Implementation is free to filter by node kind for
        efficiency; ``kind=None`` means all kinds.

        ``now_ts`` defaults to the latest edge ``created_ts`` in the
        graph — a useful fallback when callers don't carry a clock,
        but FRAGILE for scenarios where time passes without any new
        citations (the fallback freezes "now" at the last write).
        Production callers should always pass an explicit ``now_ts``
        from the TimeProvider.
        """
        ...


# ─── In-memory implementation ────────────────────────────────────────


@dataclass
class InMemoryGraphClient:
    """Dict-backed graph. Fast, simple, no external deps.

    Used by the harness's default test path. Operations are O(N)
    over the relevant collection; at home scale this is < 1 ms even
    for 90-day scenarios.
    """

    _events: dict[str, Event] = field(default_factory=dict)
    _actors: dict[str, KnownActor] = field(default_factory=dict)
    _decisions: dict[str, VLMDecision] = field(default_factory=dict)
    _policies: dict[str, Policy] = field(default_factory=dict)
    _cited_edges: dict[tuple[str, str], CitedEdge] = field(default_factory=dict)
    """Keyed by (decision_id, memory_id)."""

    def initialize_schema(self) -> None:
        # No-op for in-memory — dicts are schemaless.
        pass

    def clear_all(self) -> None:
        self._events.clear()
        self._actors.clear()
        self._decisions.clear()
        self._policies.clear()
        self._cited_edges.clear()

    def write_event(self, event: Event) -> None:
        self._events[event.id] = event

    def read_event(self, event_id: str) -> Event | None:
        return self._events.get(event_id)

    def write_known_actor(self, actor: KnownActor) -> None:
        self._actors[actor.id] = actor

    def read_known_actor(self, actor_id: str) -> KnownActor | None:
        return self._actors.get(actor_id)

    def write_vlm_decision(self, decision: VLMDecision) -> None:
        self._decisions[decision.id] = decision

    def read_vlm_decision(self, decision_id: str) -> VLMDecision | None:
        return self._decisions.get(decision_id)

    def write_cited_edge(self, edge: CitedEdge) -> None:
        self._cited_edges[(edge.decision_id, edge.memory_id)] = edge

    def get_citations_from(self, decision_id: str) -> list[CitedEdge]:
        return [e for (d, _), e in self._cited_edges.items() if d == decision_id]

    def get_citations_to(self, memory_id: str) -> list[CitedEdge]:
        return [e for (_, m), e in self._cited_edges.items() if m == memory_id]

    def write_policy(self, policy: Policy) -> None:
        self._policies[policy.id] = policy

    def read_policy(self, policy_id: str) -> Policy | None:
        return self._policies.get(policy_id)

    def list_active_policies(self, *, now_ts: float) -> list[Policy]:
        return [p for p in self._policies.values() if p.is_active(now_ts)]

    def list_all_events(self) -> list[Event]:
        return list(self._events.values())

    def list_all_known_actors(self) -> list[KnownActor]:
        return list(self._actors.values())

    def list_all_vlm_decisions(self) -> list[VLMDecision]:
        return list(self._decisions.values())

    def list_all_policies(self) -> list[Policy]:
        return list(self._policies.values())

    def list_all_cited_edges(self) -> list[CitedEdge]:
        return list(self._cited_edges.values())

    def count_events(self, *, camera_id: str | None = None) -> int:
        if camera_id is None:
            return len(self._events)
        return sum(1 for e in self._events.values() if e.camera_id == camera_id)

    def count_vlm_decisions(self) -> int:
        return len(self._decisions)

    def count_policies(self, *, kind: str | None = None) -> int:
        if kind is None:
            return len(self._policies)
        return sum(1 for p in self._policies.values() if p.kind == kind)

    def candidates_for_pruning(
        self,
        *,
        threshold: float,
        kind: NodeKind | None = None,
        now_ts: float | None = None,
    ) -> list[PruneCandidate]:
        from sentihome_memory.dynamics import DecayParams, decay

        params = DecayParams()
        if now_ts is None:
            now = max(
                (e.created_ts for e in self._cited_edges.values()),
                default=0.0,
            )
        else:
            now = now_ts
        if not self._cited_edges and now_ts is None:
            return []

        candidates: list[PruneCandidate] = []

        def score_node(node_id: str, _node_kind: NodeKind) -> float:
            incoming = self.get_citations_to(node_id)
            if not incoming:
                return 0.0
            return max(edge.weight * decay(now - edge.created_ts, params) for edge in incoming)

        if kind in (None, NodeKind.EVENT):
            for event_id in self._events:
                score = score_node(event_id, NodeKind.EVENT)
                if score < threshold:
                    candidates.append(
                        PruneCandidate(
                            node_id=event_id,
                            node_kind=NodeKind.EVENT,
                            pruning_score=score,
                            reason=f"score {score:.3f} below threshold {threshold:.3f}",
                        )
                    )
        if kind in (None, NodeKind.KNOWN_ACTOR):
            for actor_id in self._actors:
                score = score_node(actor_id, NodeKind.KNOWN_ACTOR)
                if score < threshold:
                    candidates.append(
                        PruneCandidate(
                            node_id=actor_id,
                            node_kind=NodeKind.KNOWN_ACTOR,
                            pruning_score=score,
                            reason=f"score {score:.3f} below threshold {threshold:.3f}",
                        )
                    )

        return candidates


# ─── Neo4j implementation ────────────────────────────────────────────


# Schema migration Cypher — applied by initialize_schema(). Idempotent
# via ``IF NOT EXISTS``. Embeddings sized to 128 (placeholder; real
# ArcFace is 512 — bump when face pipeline lands).
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT actor_id IF NOT EXISTS FOR (a:KnownActor) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (d:VLMDecision) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT policy_id IF NOT EXISTS FOR (p:Policy) REQUIRE p.id IS UNIQUE",
    "CREATE INDEX event_ts IF NOT EXISTS FOR (e:Event) ON (e.ts)",
    "CREATE INDEX event_camera IF NOT EXISTS FOR (e:Event) ON (e.camera_id)",
    "CREATE INDEX policy_scope_camera IF NOT EXISTS FOR (p:Policy) ON (p.scope_camera)",
    "CREATE INDEX policy_expires_at IF NOT EXISTS FOR (p:Policy) ON (p.expires_at)",
    """CREATE VECTOR INDEX actor_face_embedding IF NOT EXISTS
       FOR (a:KnownActor) ON (a.face_embedding)
       OPTIONS { indexConfig: {
         `vector.dimensions`: 128,
         `vector.similarity_function`: 'cosine'
       }}""",
)


@dataclass
class Neo4jGraphClient:
    """Real Neo4j 5.x graph via the official driver.

    Production target. Tests use testcontainers to spin up an
    ephemeral container; production deployment hosts Neo4j as a
    sidecar to the preprocessor on the inference box.

    All Cypher is parameterized — no string concatenation of values
    into queries.
    """

    driver: Driver

    def initialize_schema(self) -> None:
        with self.driver.session() as session:
            for stmt in _SCHEMA_STATEMENTS:
                session.run(stmt)

    def clear_all(self) -> None:
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def write_event(self, event: Event) -> None:
        with self.driver.session() as session:
            session.run(
                """
                MERGE (e:Event {id: $id})
                SET e.ts             = $ts,
                    e.camera_id      = $camera_id,
                    e.tag_set        = $tag_set,
                    e.matched_actor_ids = $matched_actor_ids,
                    e.metadata       = $metadata
                """,
                id=event.id,
                ts=event.ts,
                camera_id=event.camera_id,
                tag_set=list(event.tag_set),
                matched_actor_ids=list(event.matched_actor_ids),
                # Neo4j doesn't take arbitrary dicts as a property —
                # flatten metadata into a JSON-encoded string for now.
                # When metadata needs querying we'll model it as a
                # separate node.
                metadata=_encode_dict(event.metadata),
            )

    def read_event(self, event_id: str) -> Event | None:
        with self.driver.session() as session:
            record = session.run("MATCH (e:Event {id: $id}) RETURN e", id=event_id).single()
        if record is None:
            return None
        node = record["e"]
        return Event(
            id=node["id"],
            ts=node["ts"],
            camera_id=node["camera_id"],
            tag_set=tuple(node.get("tag_set") or ()),
            matched_actor_ids=tuple(node.get("matched_actor_ids") or ()),
            metadata=_decode_dict(node.get("metadata")),
        )

    def write_known_actor(self, actor: KnownActor) -> None:
        with self.driver.session() as session:
            session.run(
                """
                MERGE (a:KnownActor {id: $id})
                SET a.name            = $name,
                    a.role            = $role,
                    a.face_embedding  = $face_embedding,
                    a.access_profile  = $access_profile
                """,
                id=actor.id,
                name=actor.name,
                role=actor.role,
                face_embedding=list(actor.face_embedding) if actor.face_embedding else None,
                access_profile=actor.access_profile,
            )

    def read_known_actor(self, actor_id: str) -> KnownActor | None:
        with self.driver.session() as session:
            record = session.run("MATCH (a:KnownActor {id: $id}) RETURN a", id=actor_id).single()
        if record is None:
            return None
        node = record["a"]
        emb = node.get("face_embedding")
        return KnownActor(
            id=node["id"],
            name=node["name"],
            role=node["role"],
            face_embedding=tuple(emb) if emb else None,
            access_profile=node.get("access_profile", "none"),
        )

    def write_vlm_decision(self, decision: VLMDecision) -> None:
        with self.driver.session() as session:
            session.run(
                """
                MERGE (d:VLMDecision {id: $id})
                SET d.ts                     = $ts,
                    d.triggered_by_event_id  = $triggered_by_event_id,
                    d.findings_summary       = $findings_summary
                """,
                id=decision.id,
                ts=decision.ts,
                triggered_by_event_id=decision.triggered_by_event_id,
                findings_summary=decision.findings_summary,
            )

    def read_vlm_decision(self, decision_id: str) -> VLMDecision | None:
        with self.driver.session() as session:
            record = session.run(
                "MATCH (d:VLMDecision {id: $id}) RETURN d", id=decision_id
            ).single()
        if record is None:
            return None
        node = record["d"]
        return VLMDecision(
            id=node["id"],
            ts=node["ts"],
            triggered_by_event_id=node.get("triggered_by_event_id"),
            findings_summary=node.get("findings_summary", ""),
        )

    def write_policy(self, policy: Policy) -> None:
        with self.driver.session() as session:
            session.run(
                """
                MERGE (p:Policy {id: $id})
                SET p.kind              = $kind,
                    p.scope_camera      = $scope_camera,
                    p.match_tag_subset  = $match_tag_subset,
                    p.ttl_seconds       = $ttl_seconds,
                    p.created_ts        = $created_ts,
                    p.expires_at        = $expires_at,
                    p.rationale         = $rationale
                """,
                id=policy.id,
                kind=policy.kind,
                scope_camera=policy.scope_camera,
                match_tag_subset=list(policy.match_tag_subset),
                ttl_seconds=policy.ttl_seconds,
                created_ts=policy.created_ts,
                expires_at=policy.created_ts + policy.ttl_seconds,
                rationale=policy.rationale,
            )

    def read_policy(self, policy_id: str) -> Policy | None:
        with self.driver.session() as session:
            record = session.run("MATCH (p:Policy {id: $id}) RETURN p", id=policy_id).single()
        if record is None:
            return None
        return _node_to_policy(record["p"])

    def list_active_policies(self, *, now_ts: float) -> list[Policy]:
        with self.driver.session() as session:
            records = session.run(
                "MATCH (p:Policy) WHERE $now < p.expires_at RETURN p",
                now=now_ts,
            )
            return [_node_to_policy(r["p"]) for r in records]

    def list_all_events(self) -> list[Event]:
        with self.driver.session() as session:
            records = session.run("MATCH (e:Event) RETURN e")
            return [
                Event(
                    id=r["e"]["id"],
                    ts=r["e"]["ts"],
                    camera_id=r["e"]["camera_id"],
                    tag_set=tuple(r["e"].get("tag_set") or ()),
                    matched_actor_ids=tuple(r["e"].get("matched_actor_ids") or ()),
                    metadata=_decode_dict(r["e"].get("metadata")),
                )
                for r in records
            ]

    def list_all_known_actors(self) -> list[KnownActor]:
        with self.driver.session() as session:
            records = session.run("MATCH (a:KnownActor) RETURN a")
            out: list[KnownActor] = []
            for r in records:
                node = r["a"]
                emb = node.get("face_embedding")
                out.append(
                    KnownActor(
                        id=node["id"],
                        name=node["name"],
                        role=node["role"],
                        face_embedding=tuple(emb) if emb else None,
                        access_profile=node.get("access_profile", "none"),
                    )
                )
            return out

    def list_all_vlm_decisions(self) -> list[VLMDecision]:
        with self.driver.session() as session:
            records = session.run("MATCH (d:VLMDecision) RETURN d")
            return [
                VLMDecision(
                    id=r["d"]["id"],
                    ts=r["d"]["ts"],
                    triggered_by_event_id=r["d"].get("triggered_by_event_id"),
                    findings_summary=r["d"].get("findings_summary", ""),
                )
                for r in records
            ]

    def list_all_policies(self) -> list[Policy]:
        with self.driver.session() as session:
            records = session.run("MATCH (p:Policy) RETURN p")
            return [_node_to_policy(r["p"]) for r in records]

    def list_all_cited_edges(self) -> list[CitedEdge]:
        with self.driver.session() as session:
            records = session.run(
                """
                MATCH (d)-[r:CITED]->(m)
                RETURN d.id AS d_id, m.id AS m_id, r AS edge
                """
            )
            return [_record_to_edge(r) for r in records]

    def count_events(self, *, camera_id: str | None = None) -> int:
        with self.driver.session() as session:
            if camera_id is None:
                record = session.run("MATCH (e:Event) RETURN count(e) AS n").single()
            else:
                record = session.run(
                    "MATCH (e:Event {camera_id: $cam}) RETURN count(e) AS n",
                    cam=camera_id,
                ).single()
        return int(record["n"]) if record else 0

    def count_vlm_decisions(self) -> int:
        with self.driver.session() as session:
            record = session.run("MATCH (d:VLMDecision) RETURN count(d) AS n").single()
        return int(record["n"]) if record else 0

    def count_policies(self, *, kind: str | None = None) -> int:
        with self.driver.session() as session:
            if kind is None:
                record = session.run("MATCH (p:Policy) RETURN count(p) AS n").single()
            else:
                record = session.run(
                    "MATCH (p:Policy {kind: $kind}) RETURN count(p) AS n",
                    kind=kind,
                ).single()
        return int(record["n"]) if record else 0

    def write_cited_edge(self, edge: CitedEdge) -> None:
        with self.driver.session() as session:
            # The decision + memory nodes must exist; we don't auto-
            # create them here (callers do, in the right order). MERGE
            # on the edge alone preserves any prior write.
            session.run(
                """
                MATCH (d {id: $decision_id})
                MATCH (m {id: $memory_id})
                MERGE (d)-[r:CITED]->(m)
                SET r.weight             = $weight,
                    r.created_ts         = $created_ts,
                    r.last_reinforced_ts = $last_reinforced_ts
                """,
                decision_id=edge.decision_id,
                memory_id=edge.memory_id,
                weight=edge.weight,
                created_ts=edge.created_ts,
                last_reinforced_ts=edge.last_reinforced_ts,
            )

    def get_citations_from(self, decision_id: str) -> list[CitedEdge]:
        with self.driver.session() as session:
            records = session.run(
                """
                MATCH (d {id: $decision_id})-[r:CITED]->(m)
                RETURN d.id AS d_id, m.id AS m_id, r AS edge
                """,
                decision_id=decision_id,
            )
            return [_record_to_edge(r) for r in records]

    def get_citations_to(self, memory_id: str) -> list[CitedEdge]:
        with self.driver.session() as session:
            records = session.run(
                """
                MATCH (d)-[r:CITED]->(m {id: $memory_id})
                RETURN d.id AS d_id, m.id AS m_id, r AS edge
                """,
                memory_id=memory_id,
            )
            return [_record_to_edge(r) for r in records]

    def candidates_for_pruning(
        self,
        *,
        threshold: float,
        kind: NodeKind | None = None,
        now_ts: float | None = None,
    ) -> list[PruneCandidate]:
        # Mirror the in-memory implementation's semantics. For Neo4j
        # we still pull the citation edges back and score in Python —
        # the Mnemosyne decay function is a custom non-Cypher curve.
        # When we want sub-millisecond pruning queries we'll port the
        # formula to a stored Cypher function or precompute the
        # decay table.
        from sentihome_memory.dynamics import DecayParams, decay

        params = DecayParams()
        # Use ``now_ts`` if caller supplied it; otherwise fall back to
        # the most recent edge — same fragile fallback as
        # InMemoryGraphClient, useful when callers don't carry a clock.
        with self.driver.session() as session:
            if now_ts is None:
                now_record = session.run(
                    "MATCH ()-[r:CITED]->() RETURN max(r.created_ts) AS now"
                ).single()
                now_ts = (now_record["now"] if now_record else None) or 0.0

            label_filter = ""
            if kind is NodeKind.EVENT:
                label_filter = ":Event"
            elif kind is NodeKind.KNOWN_ACTOR:
                label_filter = ":KnownActor"

            records = session.run(
                f"""
                MATCH (n{label_filter})
                OPTIONAL MATCH (n)<-[r:CITED]-()
                WITH n, collect(r) AS edges
                RETURN n.id AS node_id, labels(n) AS labels, edges
                """
            )
            results = list(records)

        candidates: list[PruneCandidate] = []
        for record in results:
            edges = record["edges"]
            if not edges:
                score = 0.0
            else:
                score = max(
                    (edge["weight"] or 0.0) * decay(now_ts - (edge["created_ts"] or 0.0), params)
                    for edge in edges
                )
            if score < threshold:
                labels = record["labels"] or []
                node_kind = NodeKind.EVENT if "Event" in labels else NodeKind.KNOWN_ACTOR
                candidates.append(
                    PruneCandidate(
                        node_id=record["node_id"],
                        node_kind=node_kind,
                        pruning_score=score,
                        reason=f"score {score:.3f} below threshold {threshold:.3f}",
                    )
                )
        return candidates


# ─── helpers ─────────────────────────────────────────────────────────


def _encode_dict(d: dict[str, str]) -> str:
    """Encode a metadata dict as a sorted ``key=value`` semicolon string.

    Neo4j properties can't hold arbitrary dicts; we round-trip via a
    deterministic string encoding. Sufficient for the small free-form
    metadata current scenarios use. If we ever need queryable metadata
    we'll model it as a separate node.
    """
    if not d:
        return ""
    return ";".join(f"{k}={v}" for k, v in sorted(d.items()))


def _decode_dict(s: str | None) -> dict[str, str]:
    if not s:
        return {}
    out: dict[str, str] = {}
    for part in s.split(";"):
        if "=" in part:
            k, _, v = part.partition("=")
            out[k] = v
    return out


def _node_to_policy(node) -> Policy:
    """Turn a Neo4j Policy node into a :class:`Policy` dataclass."""
    return Policy(
        id=node["id"],
        kind=node["kind"],
        scope_camera=node.get("scope_camera"),
        match_tag_subset=tuple(node.get("match_tag_subset") or ()),
        ttl_seconds=node["ttl_seconds"],
        created_ts=node["created_ts"],
        rationale=node.get("rationale", ""),
    )


def _record_to_edge(record) -> CitedEdge:
    """Turn a Cypher record (d_id, m_id, edge) into a :class:`CitedEdge`."""
    edge = record["edge"]
    last = edge.get("last_reinforced_ts")
    # Neo4j may return None vs NaN-as-None; normalise.
    if last is not None and isinstance(last, float) and math.isnan(last):
        last = None
    return CitedEdge(
        decision_id=record["d_id"],
        memory_id=record["m_id"],
        weight=edge["weight"],
        created_ts=edge["created_ts"],
        last_reinforced_ts=last,
    )
