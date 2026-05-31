"""Graph snapshot + diff utility for cross-backend differential tests.

A :class:`GraphSnapshot` is the full set of nodes + edges in a graph
client at one moment in time, with deterministic ordering. Two
snapshots are equal iff every node and edge is byte-identical
(modulo float tolerance for edge weights / timestamps).

The differential test runs each canonical scenario on both
:class:`InMemoryGraphClient` and :class:`Neo4jGraphClient` and asserts
the resulting snapshots agree — proves the two implementations of the
GraphClient Protocol observe the same writes the same way.

This catches a class of bugs the parametrized scenario tests can't:
the assertion library only checks aggregates (counts, single-edge
weights). A backend could silently drop a recurring event and the
counts would still match — but the snapshot would differ.
"""

from __future__ import annotations

from dataclasses import dataclass

from kukiihome_memory.graph import (
    CitedEdge,
    Event,
    GraphClient,
    KnownActor,
    Policy,
    VLMDecision,
)

# Tolerance for float comparisons. Timestamps + weights round-trip
# through Neo4j as doubles, then back into Python; equality after the
# round-trip is at fp64 precision but conservative tolerance keeps the
# diff stable.
_FLOAT_TOL = 1e-9


@dataclass(frozen=True)
class GraphSnapshot:
    """Deterministically-ordered full graph state.

    Tuples (not lists) so the snapshot is hashable and immutable;
    callers can stash one and compare later.
    """

    events: tuple[Event, ...]
    actors: tuple[KnownActor, ...]
    decisions: tuple[VLMDecision, ...]
    policies: tuple[Policy, ...]
    cited_edges: tuple[CitedEdge, ...]

    @classmethod
    def from_client(cls, client: GraphClient) -> GraphSnapshot:
        """Read the full graph state, sorted for deterministic equality."""
        return cls(
            events=tuple(sorted(client.list_all_events(), key=lambda e: e.id)),
            actors=tuple(sorted(client.list_all_known_actors(), key=lambda a: a.id)),
            decisions=tuple(sorted(client.list_all_vlm_decisions(), key=lambda d: d.id)),
            policies=tuple(sorted(client.list_all_policies(), key=lambda p: p.id)),
            cited_edges=tuple(
                sorted(
                    client.list_all_cited_edges(),
                    key=lambda e: (e.decision_id, e.memory_id),
                )
            ),
        )


def diff_snapshots(a: GraphSnapshot, b: GraphSnapshot) -> list[str]:
    """Human-readable differences between two snapshots. Empty list
    means they agree.

    The diff is field-by-field on nodes that share an id; unmatched
    nodes (present in one snapshot, absent in the other) are
    reported as additions/removals.
    """
    diffs: list[str] = []
    _diff_events(a.events, b.events, diffs)
    _diff_actors(a.actors, b.actors, diffs)
    _diff_decisions(a.decisions, b.decisions, diffs)
    _diff_policies(a.policies, b.policies, diffs)
    _diff_cited_edges(a.cited_edges, b.cited_edges, diffs)
    return diffs


# ─── Per-collection diffing ──────────────────────────────────────────


def _diff_events(a: tuple[Event, ...], b: tuple[Event, ...], out: list[str]) -> None:
    by_id_a = {e.id: e for e in a}
    by_id_b = {e.id: e for e in b}
    only_in_a = set(by_id_a) - set(by_id_b)
    only_in_b = set(by_id_b) - set(by_id_a)
    for eid in sorted(only_in_a):
        out.append(f"Event {eid!r} present in A but missing in B")
    for eid in sorted(only_in_b):
        out.append(f"Event {eid!r} present in B but missing in A")
    for eid in sorted(set(by_id_a) & set(by_id_b)):
        ea, eb = by_id_a[eid], by_id_b[eid]
        if not _close(ea.ts, eb.ts):
            out.append(f"Event {eid!r} ts differs: A={ea.ts} vs B={eb.ts}")
        if ea.camera_id != eb.camera_id:
            out.append(f"Event {eid!r} camera_id differs: A={ea.camera_id!r} vs B={eb.camera_id!r}")
        if tuple(ea.tag_set) != tuple(eb.tag_set):
            out.append(
                f"Event {eid!r} tag_set differs: A={list(ea.tag_set)} vs B={list(eb.tag_set)}"
            )
        if tuple(ea.matched_actor_ids) != tuple(eb.matched_actor_ids):
            out.append(
                f"Event {eid!r} matched_actor_ids differs: "
                f"A={list(ea.matched_actor_ids)} vs B={list(eb.matched_actor_ids)}"
            )
        if ea.metadata != eb.metadata:
            out.append(f"Event {eid!r} metadata differs: A={ea.metadata} vs B={eb.metadata}")


def _diff_actors(a: tuple[KnownActor, ...], b: tuple[KnownActor, ...], out: list[str]) -> None:
    by_id_a = {x.id: x for x in a}
    by_id_b = {x.id: x for x in b}
    only_in_a = set(by_id_a) - set(by_id_b)
    only_in_b = set(by_id_b) - set(by_id_a)
    for aid in sorted(only_in_a):
        out.append(f"KnownActor {aid!r} present in A but missing in B")
    for aid in sorted(only_in_b):
        out.append(f"KnownActor {aid!r} present in B but missing in A")
    for aid in sorted(set(by_id_a) & set(by_id_b)):
        xa, xb = by_id_a[aid], by_id_b[aid]
        if xa.name != xb.name:
            out.append(f"KnownActor {aid!r} name differs: A={xa.name!r} vs B={xb.name!r}")
        if xa.role != xb.role:
            out.append(f"KnownActor {aid!r} role differs: A={xa.role!r} vs B={xb.role!r}")
        if xa.access_profile != xb.access_profile:
            out.append(
                f"KnownActor {aid!r} access_profile differs: "
                f"A={xa.access_profile!r} vs B={xb.access_profile!r}"
            )


def _diff_decisions(a: tuple[VLMDecision, ...], b: tuple[VLMDecision, ...], out: list[str]) -> None:
    by_id_a = {d.id: d for d in a}
    by_id_b = {d.id: d for d in b}
    only_in_a = set(by_id_a) - set(by_id_b)
    only_in_b = set(by_id_b) - set(by_id_a)
    for did in sorted(only_in_a):
        out.append(f"VLMDecision {did!r} present in A but missing in B")
    for did in sorted(only_in_b):
        out.append(f"VLMDecision {did!r} present in B but missing in A")
    for did in sorted(set(by_id_a) & set(by_id_b)):
        da, db = by_id_a[did], by_id_b[did]
        if not _close(da.ts, db.ts):
            out.append(f"VLMDecision {did!r} ts differs: A={da.ts} vs B={db.ts}")
        if da.triggered_by_event_id != db.triggered_by_event_id:
            out.append(
                f"VLMDecision {did!r} triggered_by_event_id differs: "
                f"A={da.triggered_by_event_id!r} vs B={db.triggered_by_event_id!r}"
            )
        if da.findings_summary != db.findings_summary:
            # Findings is multi-line; just flag, don't dump.
            out.append(f"VLMDecision {did!r} findings_summary differs")


def _diff_policies(a: tuple[Policy, ...], b: tuple[Policy, ...], out: list[str]) -> None:
    by_id_a = {p.id: p for p in a}
    by_id_b = {p.id: p for p in b}
    only_in_a = set(by_id_a) - set(by_id_b)
    only_in_b = set(by_id_b) - set(by_id_a)
    for pid in sorted(only_in_a):
        out.append(f"Policy {pid!r} present in A but missing in B")
    for pid in sorted(only_in_b):
        out.append(f"Policy {pid!r} present in B but missing in A")
    for pid in sorted(set(by_id_a) & set(by_id_b)):
        pa, pb = by_id_a[pid], by_id_b[pid]
        if pa.kind != pb.kind:
            out.append(f"Policy {pid!r} kind differs: A={pa.kind!r} vs B={pb.kind!r}")
        if pa.scope_camera != pb.scope_camera:
            out.append(
                f"Policy {pid!r} scope_camera differs: "
                f"A={pa.scope_camera!r} vs B={pb.scope_camera!r}"
            )
        if tuple(pa.match_tag_subset) != tuple(pb.match_tag_subset):
            out.append(
                f"Policy {pid!r} match_tag_subset differs: "
                f"A={list(pa.match_tag_subset)} vs B={list(pb.match_tag_subset)}"
            )
        if not _close(pa.ttl_seconds, pb.ttl_seconds):
            out.append(
                f"Policy {pid!r} ttl_seconds differs: A={pa.ttl_seconds} vs B={pb.ttl_seconds}"
            )
        if not _close(pa.created_ts, pb.created_ts):
            out.append(f"Policy {pid!r} created_ts differs: A={pa.created_ts} vs B={pb.created_ts}")


def _diff_cited_edges(a: tuple[CitedEdge, ...], b: tuple[CitedEdge, ...], out: list[str]) -> None:
    by_key_a = {(e.decision_id, e.memory_id): e for e in a}
    by_key_b = {(e.decision_id, e.memory_id): e for e in b}
    only_in_a = set(by_key_a) - set(by_key_b)
    only_in_b = set(by_key_b) - set(by_key_a)
    for k in sorted(only_in_a):
        out.append(f"CITED edge {k[0]}→{k[1]} present in A but missing in B")
    for k in sorted(only_in_b):
        out.append(f"CITED edge {k[0]}→{k[1]} present in B but missing in A")
    for k in sorted(set(by_key_a) & set(by_key_b)):
        ea, eb = by_key_a[k], by_key_b[k]
        if not _close(ea.weight, eb.weight):
            out.append(f"CITED edge {k[0]}→{k[1]} weight differs: A={ea.weight} vs B={eb.weight}")
        if not _close(ea.created_ts, eb.created_ts):
            out.append(
                f"CITED edge {k[0]}→{k[1]} created_ts differs: "
                f"A={ea.created_ts} vs B={eb.created_ts}"
            )
        if not _close(ea.last_reinforced_ts, eb.last_reinforced_ts):
            out.append(
                f"CITED edge {k[0]}→{k[1]} last_reinforced_ts differs: "
                f"A={ea.last_reinforced_ts} vs B={eb.last_reinforced_ts}"
            )


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _FLOAT_TOL
