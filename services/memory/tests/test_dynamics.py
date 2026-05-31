"""Tests for the Hebbian edge-weight dynamics.

Verifies the mathematical properties the rest of the memory subsystem
relies on: decay floor never breached, habituation actually saturates,
pruning prefers nodes with strong recent edges, redundancy correctly
identifies functionally-equivalent memories.

These are pure-function tests — no graph state, no I/O. The synthetic-
data harness exercises the same functions in scenario-level tests.
"""

from __future__ import annotations

from kukiihome_memory.dynamics import (
    DecayParams,
    HabituationParams,
    RedundancyParams,
    decay,
    effective_age,
    habituation_boost,
    is_redundant,
    pruning_score,
    redundancy_score,
)

# ─── Decay ────────────────────────────────────────────────────────────


def test_decay_fresh_edge_returns_near_one():
    """At zero age, decay should be 1.0 exactly."""
    assert decay(0.0) == 1.0


def test_decay_never_below_floor():
    """No matter how old the edge, decay must stay >= d.
    This is the long-tail protection invariant."""
    p = DecayParams(d=0.05)
    # 10 years
    very_old = 10 * 365 * 24 * 3600
    assert decay(very_old, p) >= p.d
    # 100 years
    assert decay(100 * 365 * 24 * 3600, p) >= p.d


def test_decay_below_one_minus_floor_at_midpoint():
    """At the sigmoid midpoint, decay should be roughly (1-d)/2 + d
    (half the available range above the floor)."""
    p = DecayParams(a=86_400.0, b=3600.0, d=0.05, c=60.0)
    midpoint_decay = decay(p.a, p)
    # At midpoint: tau = d + (1-d)/2 = 0.5 + d/2 = 0.525
    assert abs(midpoint_decay - 0.525) < 0.01


def test_decay_monotonically_decreasing():
    """Decay must be monotonically non-increasing in age."""
    p = DecayParams()
    last = 1.0
    for age in (0, 100, 1000, 86400, 604800, 2_419_200, 10_000_000):
        d_val = decay(age, p)
        assert d_val <= last + 1e-9
        last = d_val


def test_decay_linear_segment_is_continuous_with_sigmoid():
    """At age = c, the linear segment must match the sigmoid value."""
    p = DecayParams(a=86_400.0, b=3600.0, d=0.05, c=3600.0)
    # Just below c (linear)
    just_below = decay(p.c - 0.01, p)
    # At c (linear endpoint = sigmoid start)
    at_c = decay(p.c, p)
    # Just above c (sigmoid)
    just_above = decay(p.c + 0.01, p)
    # All three should be within a few ppm
    assert abs(just_below - at_c) < 0.001
    assert abs(at_c - just_above) < 0.001


def test_decay_negative_age_returns_one():
    """Defensive: future-dated edges shouldn't decay."""
    assert decay(-100.0) == 1.0


# ─── Effective age ────────────────────────────────────────────────────


def test_effective_age_no_boost():
    """Without boost, effective age == raw age."""
    assert effective_age(now_ts=1000.0, edge_created_ts=200.0) == 800.0


def test_effective_age_with_boost():
    """Boost makes the edge appear younger."""
    eff = effective_age(now_ts=1000.0, edge_created_ts=200.0, cumulative_boost=300.0)
    assert eff == 500.0


def test_effective_age_boost_exceeds_age_clamps_to_zero():
    """An edge can't be 'younger than newly created' — clamp at zero."""
    eff = effective_age(now_ts=1000.0, edge_created_ts=900.0, cumulative_boost=500.0)
    assert eff == 0.0


# ─── Habituation ──────────────────────────────────────────────────────


def test_habituation_first_ever_boost_is_full():
    """A memory that has never been boosted (time_since_last_boost=None)
    receives the full delta_max — no habituation yet to apply."""
    p = HabituationParams(delta_max=0.2)
    assert habituation_boost(None, p) == p.delta_max


def test_habituation_just_after_boost_is_tiny():
    """Just after a previous boost (time_since_last_boost ≈ 0), the
    next citation gets almost no boost — that's the refractory period
    that prevents spam reinforcement."""
    p = HabituationParams(delta_max=0.2, t_crit=3600.0, scale=600.0)
    boost = habituation_boost(0.0, p)
    # At time_since=0, exponent = -(0 - 3600)/600 = 6. Sigmoid(6) ≈ 0.998.
    # boost = 0.2 / (1 + e^6) ≈ 0.0005 — very small.
    assert boost < 0.01


def test_habituation_at_t_crit_is_half():
    """At the sigmoid midpoint (time_since_last_boost == t_crit), the
    boost magnitude is half of delta_max."""
    p = HabituationParams(delta_max=0.2, t_crit=3600.0, scale=600.0)
    boost = habituation_boost(p.t_crit, p)
    assert abs(boost - p.delta_max / 2) < 0.01


def test_habituation_long_after_is_full():
    """A memory not cited for a long time gets the full delta_max
    when finally cited again."""
    p = HabituationParams(delta_max=0.2, t_crit=3600.0, scale=600.0)
    # 4x t_crit past last boost
    boost = habituation_boost(4 * p.t_crit, p)
    assert abs(boost - p.delta_max) < 0.01


def test_habituation_monotonically_increasing_in_time_since_last_boost():
    """The longer ago the last boost, the bigger the next allowed boost."""
    p = HabituationParams()
    boosts = [habituation_boost(t, p) for t in (0, 60, 600, 1800, 3600, 7200, 14400)]
    for i in range(len(boosts) - 1):
        assert boosts[i] <= boosts[i + 1] + 1e-9


def test_habituation_never_exceeds_delta_max():
    """No matter what time_since_last_boost, boost is bounded by delta_max."""
    p = HabituationParams(delta_max=0.2)
    for t in (0, 60, 3600, 1_000_000):
        assert habituation_boost(t, p) <= p.delta_max + 1e-9


# ─── Pruning ──────────────────────────────────────────────────────────


def test_pruning_score_orphaned_node():
    """A node with no edges has score 0."""
    assert pruning_score([]) == 0.0


def test_pruning_score_uses_strongest_edge():
    """Score is the max product, not the sum or average."""
    # Three edges with different (weight, decay) — strongest is the one
    # with the highest product.
    edges = [(0.5, 0.8), (0.9, 0.2), (0.3, 0.9)]
    # Products: 0.4, 0.18, 0.27
    assert pruning_score(edges) == 0.5 * 0.8  # = 0.4


def test_pruning_score_node_with_one_strong_recent_edge_survives():
    """The key property: a node with one good edge survives even if
    other edges have decayed almost to the floor."""
    p = DecayParams()
    very_old = decay(10 * 365 * 24 * 3600, p)  # near d
    fresh = decay(0.0, p)  # 1.0
    # Node has one fresh strong edge and ten ancient weak ones
    edges = [(1.0, fresh)] + [(0.5, very_old)] * 10
    score = pruning_score(edges)
    # Score == fresh edge product == 1.0 * 1.0 == 1.0
    assert score == 1.0


# ─── Redundancy / compression ─────────────────────────────────────────


def test_redundancy_score_zero_when_dissimilar():
    """Two completely different memories score zero."""
    assert redundancy_score(0.0, 0.0) == 0.0


def test_redundancy_score_one_when_identical():
    """Two identical memories score 1.0."""
    assert redundancy_score(1.0, 1.0) == 1.0


def test_redundancy_score_weighted_sum():
    """Score is the weighted average of embedding-sim and keyword-jaccard."""
    p = RedundancyParams(alpha_nmi=0.6)
    score = redundancy_score(embedding_similarity=1.0, keyword_jaccard=0.0, params=p)
    assert abs(score - 0.6) < 1e-9


def test_is_redundant_above_threshold():
    """Threshold check: redundant if score >= rs_min."""
    p = RedundancyParams(alpha_nmi=0.6, rs_min=0.25)
    # 0.6*0.5 + 0.4*0.0 = 0.3 ≥ 0.25
    assert is_redundant(0.5, 0.0, params=p) is True


def test_is_redundant_below_threshold():
    p = RedundancyParams(alpha_nmi=0.6, rs_min=0.25)
    # 0.6*0.3 + 0.4*0.0 = 0.18 < 0.25
    assert is_redundant(0.3, 0.0, params=p) is False


def test_is_redundant_default_params_use_mnemosyne_values():
    """Default RedundancyParams should match the Mnemosyne paper:
    alpha_nmi=0.6, rs_min=0.25."""
    p = RedundancyParams()
    assert p.alpha_nmi == 0.6
    assert p.rs_min == 0.25


# ─── Cross-cutting / integration of math primitives ───────────────────


def test_long_tail_protection_integration():
    """Critical scenario: a rare-but-important edge survives 6 months
    of neglect because it never decays below the floor.

    Concretely: an edge weight of 1.0 created 6 months ago, never
    reinforced. Decay drops it to (d * weight). Pruning score should
    still be above the floor for any neighbor that has this edge.
    """
    p = DecayParams(d=0.05)
    six_months = 180 * 24 * 3600.0
    decayed_value = decay(six_months, p)
    # Decay should approach but not equal the floor
    assert decayed_value > 0.045  # comfortably above
    assert decayed_value < 0.1  # well below 1.0


def test_spam_citation_pattern_yields_diminishing_boosts():
    """Critical scenario: VLM cites the same memory 20 times within
    a minute. Each subsequent citation should produce a tiny boost
    because the refractory window hasn't elapsed.

    Realistic model: a fresh memory gets full delta_max on first
    citation; immediate re-citations during the refractory window
    contribute almost nothing; only after t_crit does subsequent
    boost magnitude rise back toward delta_max.
    """
    p = HabituationParams(delta_max=0.2, t_crit=3600.0, scale=600.0)
    last_boost_ts = None  # never boosted
    boosts = []
    for i in range(20):
        now = i * 5.0  # 5 second intervals — well within refractory window
        time_since = (now - last_boost_ts) if last_boost_ts is not None else None
        boost = habituation_boost(time_since, p)
        boosts.append(boost)
        if boost > 0.001:
            last_boost_ts = now
    # First citation = full
    assert abs(boosts[0] - p.delta_max) < 1e-9
    # All subsequent citations within refractory window = nearly zero
    assert all(b < 0.01 for b in boosts[1:])


def test_decay_floor_is_non_zero_for_real_household_timescales():
    """Sanity: at typical household timescales (1 year of disuse), the
    decay value should still be meaningful for the long-tail-protection
    argument."""
    p = DecayParams()
    one_year = 365 * 24 * 3600.0
    val = decay(one_year, p)
    # Should be very close to the floor but not zero
    assert val >= p.d - 1e-9
    # The floor itself is the lower bound; this is the protection
    assert val > 0.0
    # Should not be misleadingly close to 1.0
    assert val < 0.1
