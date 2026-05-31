"""Edge-weight dynamics — Hebbian-style memory reinforcement and decay.

These are the **functional forms** the memory layer uses to reinforce, decay,
prune, and compress entries in the graph. They are deliberately pure
mathematics — no graph state, no I/O — so they can be unit-tested in
isolation and reused identically across the production retention loop and
the test harness's synthetic-data generators.

Adopted from the Mnemosyne paper (arXiv 2510.08601 — Jonelagadda et al., 2025)
with parameters retuned for the security/presence domain. See
``planning/research/2026-05-27-memory-architecture-papers.md`` for the
research synthesis and ``planning/epics/10-identity-recognition.md`` (Memory
substrate / Edge-weight dynamics section) for the architectural rationale.

The functions in this module are **normative**: the test harness verifies
the system's behavior assuming these forms. Parameter tuning (a, b, d, c,
delta_max, t_crit, alpha_nmi, rs_min) happens via the differential runner
once enough scenarios are in place.

Distinct from :mod:`.retention` — that module enforces §16 data-class TTLs
(soft-delete + hard-delete by data class). This module is about graph edge
weight evolution, which is a different concept (Hebbian dynamics vs data
governance retention).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ─── Decay ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecayParams:
    """Reverse-sigmoid decay parameters with a non-zero floor.

    Form: ``tau(e_eff) = (1 - d) / (1 + exp((e_eff - a) / b))`` for
    ``e_eff >= c``, with a linear-correction segment for ``e_eff in [0, c)``
    to avoid the sigmoid producing weights > (1 - d) near the origin.

    The non-zero floor ``d`` is the critical long-tail-protection
    property: no memory ever decays to zero, so rare-but-important
    memories from 6 months ago still survive pruning provided they
    have at least one strong recent edge.

    Default values are the Mnemosyne paper's choices for human recall;
    we'll retune these during the differential-runner phase to match
    household cycles (weekly patterns dominate; 4-week midpoint may be
    too long for the security domain).
    """

    a: float = 2_419_200.0
    """Sigmoid midpoint, in seconds. Mnemosyne default = 4 weeks."""

    b: float = 604_800.0
    """Sigmoid steepness, in seconds. Mnemosyne default ≈ 1 week."""

    d: float = 0.05
    """Non-zero floor. Memories decay TO this value, never below.
    Mnemosyne: 0.05. Directly addresses long-tail protection."""

    c: float = 86_400.0
    """Linear-correction transition point, in seconds. Below this age
    the linear segment runs from (0, 1) to (c, tau(c)). Above this age
    the sigmoid runs. Mnemosyne: ~1 day."""


def decay(e_eff: float, params: DecayParams | None = None) -> float:
    """Compute the temporal decay factor for an edge.

    Args:
        e_eff: Effective age in seconds. Effective age subtracts any
            cumulative refresh/rewind boost from the raw age — see
            :func:`effective_age`.
        params: Decay parameters. Defaults to Mnemosyne values.

    Returns:
        Decay factor in ``[d, 1.0]``. 1.0 at e_eff=0; asymptotes to ``d``.
    """
    p = params or DecayParams()
    if e_eff < 0:
        # Defensive: future-dated edges shouldn't decay (or should they
        # be impossible? Treating as fresh.)
        return 1.0
    if e_eff < p.c:
        # Linear correction: line from (0, 1.0) to (c, tau(c))
        tau_c = _sigmoid_decay(p.c, p)
        return 1.0 - (1.0 - tau_c) * (e_eff / p.c)
    return _sigmoid_decay(e_eff, p)


def _sigmoid_decay(e_eff: float, p: DecayParams) -> float:
    """Reverse-sigmoid decay segment (e_eff >= c)."""
    # Clamp exponent to avoid overflow in extreme cases.
    exponent = (e_eff - p.a) / p.b
    if exponent > 50.0:
        # exp() would explode; sigmoid value is ~0, decay ~= d
        return p.d
    if exponent < -50.0:
        return 1.0 - p.d  # Sigmoid ~= 1, decay ~= (1 - d)
    return p.d + (1.0 - p.d) / (1.0 + math.exp(exponent))


def effective_age(
    now_ts: float,
    edge_created_ts: float,
    cumulative_boost: float = 0.0,
) -> float:
    """Compute effective age accounting for refresh/rewind boost.

    Effective age = (current time - edge creation time) - cumulative boost
    from re-encounters / citation reinforcement / user feedback.

    Args:
        now_ts: Current simulated wall-clock time, in seconds.
        edge_created_ts: When the edge was first created, in seconds.
        cumulative_boost: Total boost applied to this edge over its
            lifetime, in seconds. Subtracts from raw age. Effectively
            "this edge was refreshed N seconds ago" rather than "this
            edge is M seconds old."

    Returns:
        Effective age in seconds. Always >= 0 (clamped).
    """
    raw_age = now_ts - edge_created_ts
    eff = raw_age - cumulative_boost
    return max(0.0, eff)


# ─── Reinforcement (habituation) ──────────────────────────────────────


@dataclass(frozen=True)
class HabituationParams:
    """Sigmoidal habituation on edge-weight reinforcement.

    Refractory-period model: each boost application has a saturation
    window after which a fresh boost can be applied. Two boosts within
    a single window contribute only partially (sigmoidal ramp).

    Form: ``delta = delta_max / (1 + exp(-(time_since_last_boost - t_crit) / scale))``

    Behaviour:
      - time_since_last_boost = 0 (just boosted): tiny boost
      - time_since_last_boost = t_crit: half boost (sigmoid midpoint)
      - time_since_last_boost >> t_crit: full delta_max
      - time_since_last_boost = None (never boosted): full delta_max

    This prevents the "spam the same memory 50 times in a minute"
    failure mode while still letting genuine repeated citations across
    distinct events accumulate boost over hours.
    """

    delta_max: float = 0.2
    """Maximum boost magnitude per citation event."""

    t_crit: float = 3600.0
    """Sigmoid midpoint, in seconds. After ``t_crit`` seconds since the
    last boost, the next citation gets half the maximum boost."""

    scale: float = 600.0
    """Sigmoid steepness, in seconds. Smaller = sharper transition
    around ``t_crit``; larger = gentler."""


def habituation_boost(
    time_since_last_boost: float | None,
    params: HabituationParams | None = None,
) -> float:
    """Compute the boost magnitude for a citation event.

    Args:
        time_since_last_boost: Seconds elapsed since the most recent
            boost was applied to this memory. ``None`` if the memory
            has never been boosted before (first-ever citation).
            Must be ``>= 0`` when not None.
        params: Habituation parameters.

    Returns:
        Boost magnitude in ``[0, delta_max]``. Caller applies this to
        the target edge by reducing its effective age by the boost,
        making the edge appear fresher than its raw age suggests.
    """
    p = params or HabituationParams()
    if time_since_last_boost is None:
        # First-ever citation — no habituation applies.
        return p.delta_max
    if time_since_last_boost < 0:
        # Defensive: clamp to "just boosted."
        return _habituation_at(0.0, p)
    return _habituation_at(time_since_last_boost, p)


def _habituation_at(time_since_last_boost: float, p: HabituationParams) -> float:
    """Sigmoid evaluation at a given non-negative time-since-last-boost."""
    exponent = -(time_since_last_boost - p.t_crit) / p.scale
    if exponent > 50.0:
        # exp() would explode; sigmoid is ~0 → boost ~0.
        return 0.0
    if exponent < -50.0:
        # exp() ~0; sigmoid ~1 → full boost.
        return p.delta_max
    return p.delta_max / (1.0 + math.exp(exponent))


# ─── Pruning ──────────────────────────────────────────────────────────


def pruning_score(neighbor_edges: list[tuple[float, float]]) -> float:
    """Compute a node's pruning score from its incident edges.

    A node's score is its **best-supported edge** — the max over all
    incident edges of ``edge_weight * decay(effective_age)``. The
    intuition: a node survives if it has at least one strong recent
    connection. Orphaned nodes (no edges) have score 0 and are pruned
    first.

    Args:
        neighbor_edges: List of ``(edge_weight, decay_factor)`` tuples
            for each incident edge. ``edge_weight`` is the persistent
            Hebbian weight (typically in [0, 1]); ``decay_factor`` is
            the result of :func:`decay` for that edge's effective age.

    Returns:
        Max of products. 0.0 for an orphaned node (no edges).
    """
    if not neighbor_edges:
        return 0.0
    return max(w * d for w, d in neighbor_edges)


# ─── Redundancy / compression ─────────────────────────────────────────


@dataclass(frozen=True)
class RedundancyParams:
    """Parameters for the redundancy-driven pair-and-keep-oldest
    compression mechanism.

    Form: ``RS(n, m) = alpha_nmi * MI(embedding_n, embedding_m) +
    (1 - alpha_nmi) * JS(keywords_n, keywords_m)``

    When RS exceeds ``rs_min`` between two episodic memories, they are
    considered functionally equivalent — the older one becomes the
    anchor (preserving provenance), the newer one is paired via a
    REDUNDANT_WITH edge, and the connecting edge is reinforced. This
    is how "100 milkman events → 1 anchored pattern" works without
    losing count or recency.
    """

    alpha_nmi: float = 0.6
    """Weight on the mutual-information / embedding-similarity term.
    The remaining ``1 - alpha_nmi`` goes to the keyword Jaccard term."""

    rs_min: float = 0.25
    """Threshold above which two memories are considered redundant.
    Below this they remain separate episodic entries."""


def redundancy_score(
    embedding_similarity: float,
    keyword_jaccard: float,
    params: RedundancyParams | None = None,
) -> float:
    """Compute the redundancy score between two memory entries.

    Args:
        embedding_similarity: Normalized mutual information or cosine
            similarity between the two memories' content embeddings,
            in ``[0, 1]``.
        keyword_jaccard: Jaccard similarity of the two memories' tag /
            keyword sets, in ``[0, 1]``.
        params: Redundancy parameters.

    Returns:
        Combined score in ``[0, 1]``. Compare against ``params.rs_min``
        to decide whether to pair the memories.
    """
    p = params or RedundancyParams()
    return p.alpha_nmi * embedding_similarity + (1.0 - p.alpha_nmi) * keyword_jaccard


def is_redundant(
    embedding_similarity: float,
    keyword_jaccard: float,
    params: RedundancyParams | None = None,
) -> bool:
    """True if the two memories should be paired into a compression cluster.

    Convenience wrapper over :func:`redundancy_score` + threshold check.
    """
    p = params or RedundancyParams()
    return redundancy_score(embedding_similarity, keyword_jaccard, p) >= p.rs_min
