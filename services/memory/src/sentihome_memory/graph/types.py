"""Wire-level types for the graph layer.

Plain dataclasses that mirror node + edge shapes in the graph. Both
:class:`InMemoryGraphClient` and :class:`Neo4jGraphClient` produce and
consume these. Keeps callers (harness, dispatcher, future memory
service) agnostic to whether they're talking to in-memory dicts or
live Cypher.

This is the **Phase 1** minimal schema — only Event, KnownActor, and
the CITED edge that drives reinforcement. More node + edge types
land as we exercise scenarios that need them (KnownVehicle, KnownPet,
Policy, VLMDecision, Alert, etc. — all enumerated in Epic 10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NodeKind(StrEnum):
    """First-class node types in the memory graph (Phase 1 subset)."""

    EVENT = "Event"
    """A motion event or HA observation. Lightweight; written on every
    trigger. Carries timestamp, camera, tag set, optional identity
    matches from the preprocessor."""

    KNOWN_ACTOR = "KnownActor"
    """A recognized person. Carries face embedding(s), access profile,
    enrollment metadata."""

    VLM_DECISION = "VLMDecision"
    """One VLM invocation's structured output. CITED edges from this
    node point at the Memory nodes the VLM cited as pertinent."""


@dataclass
class Event:
    """A motion event written to memory."""

    id: str
    """Globally unique event id. Convention: ``evt_<camera>_<ts>_<rand>``."""

    ts: float
    """Unix timestamp of the event (simulated time in tests)."""

    camera_id: str

    tag_set: tuple[str, ...] = field(default_factory=tuple)
    """Preprocessor's tag output: ``("person",)``, ``("dog",)``,
    ``("person", "vehicle")``, etc. Sorted alphabetically for
    deterministic comparisons."""

    matched_actor_ids: tuple[str, ...] = field(default_factory=tuple)
    """KnownActor IDs the preprocessor matched (high-confidence)."""

    metadata: dict[str, str] = field(default_factory=dict)
    """Free-form metadata; ad-hoc fields used by individual scenarios.
    Don't rely on a specific key without checking the producer."""


@dataclass
class KnownActor:
    """An enrolled person known to the household."""

    id: str
    """Stable id, e.g. ``actor_alice``."""

    name: str
    """Display name (PII; only present in test fixtures)."""

    role: str
    """``resident``, ``resident_minor``, ``visitor_known``, etc."""

    face_embedding: tuple[float, ...] | None = None
    """ArcFace 512-d embedding. Tuple for deterministic hashing /
    comparison; ``None`` when not yet enrolled."""

    access_profile: str = "none"
    """Free-form profile tag (``full``, ``front_porch_morning``, etc.).
    Interpreted by the dispatcher's policy engine, not by the graph."""


@dataclass
class CitedEdge:
    """One memory citation from a VLM decision.

    Persistent edge in the graph: when the dispatcher walks a
    VLMDecision's citations, each cited memory node receives a
    weight delta computed from the dispatcher's policy (NOT a
    VLM-supplied weight). This struct is what the graph client
    returns when listing citations.
    """

    decision_id: str
    """The VLMDecision node id this citation originated from."""

    memory_id: str
    """The cited node id. Must exist in the graph; otherwise the
    citation is a hallucination (caught by the dispatcher's
    invalid-citation check)."""

    weight: float
    """Persistent edge weight, in ``[0, 1]``. Updated by the
    dispatcher via reverse-sigmoid decay + sigmoidal habituation
    boost (see :mod:`sentihome_memory.dynamics`)."""

    created_ts: float
    """When this edge was first written. Used by :func:`effective_age`
    to compute decay."""

    last_reinforced_ts: float | None = None
    """When this edge was most recently boosted by a citation event.
    None = never boosted since creation; the dispatcher uses None
    to mean "first ever citation" in habituation_boost()."""


@dataclass
class VLMDecision:
    """One VLM invocation's structured output (Phase 1B minimum).

    The full schema will grow to include findings, tier, recommendations,
    upstream_quality_issues, etc. — see Epic 10. For Phase 1B we only
    need enough to write the node + attach CITED edges so harness
    scenarios can drive reinforcement dynamics.
    """

    id: str
    ts: float
    triggered_by_event_id: str | None = None
    """Event that this VLM call was invoked for. None for synthetic
    bootstrap decisions."""

    findings_summary: str = ""
    """Human-readable summary; not used for assertions (the structured
    fields are). Useful for audit + the eventual Bloom visualization."""


@dataclass
class PruneCandidate:
    """A node up for potential pruning, with its score + reason.

    Returned by the graph client's pruning queries so the dispatcher
    can decide whether to actually delete (vs. preserve via the
    long-tail-protection list, recent user pin, life-safety flag,
    etc.).
    """

    node_id: str
    node_kind: NodeKind
    pruning_score: float
    """Result of :func:`sentihome_memory.dynamics.pruning_score`
    over the node's incident edges. Lower = more prunable."""

    reason: str
    """Human-readable reason string for audit logs."""
