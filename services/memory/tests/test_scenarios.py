"""End-to-end scenario tests — runs hand-curated scenarios through the
harness against both graph backends.

If a scenario asserts a behavior the harness produces correctly on
both InMemoryGraphClient and Neo4jGraphClient, we have a solid
end-to-end signal that the math + graph layer + scenario DSL all
agree. Phase 1B milestone is: this file passes against both backends
with at least one non-trivial canonical scenario.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kukiihome_memory.graph import GraphClient
from synthesis.runner import run_scenario
from synthesis.scenarios.schema import load_scenario

CANONICAL_DIR = Path(__file__).parent / "synthesis" / "scenarios" / "canonical"


@pytest.fixture(params=["in_memory", "neo4j"])
def scenario_client(request) -> GraphClient:
    """Run each scenario test against both backends.

    Lazy-resolves the underlying client fixture so a missing Docker
    daemon only skips the neo4j parametrization — not the in_memory
    one. (Listing both clients as direct parameters made the
    neo4j_container skip cascade onto every test, including pure
    in-memory runs.)
    """
    if request.param == "in_memory":
        return request.getfixturevalue("in_memory_client")
    return request.getfixturevalue("neo4j_client")


def test_basic_reinforcement_scenario(scenario_client: GraphClient):
    """Full end-to-end: load → run → assert.

    Failures from the runner's assertion evaluator are surfaced as a
    pytest failure with the full list of which assertions broke.
    """
    scenario_path = CANONICAL_DIR / "basic_reinforcement.yaml"
    scenario = load_scenario(scenario_path)

    result = run_scenario(scenario, scenario_client)

    # Diagnostic visibility on failure — show what actually happened.
    print(  # noqa: T201
        f"\nScenario {result.scenario_name!r}: "
        f"events={result.events_written}, "
        f"decisions={result.vlm_decisions_written}, "
        f"citations={result.citations_written}, "
        f"simulated_seconds={result.elapsed_simulated_seconds:,.0f}"
    )

    assert result.passed, "Scenario assertions failed:\n  " + "\n  ".join(result.assertion_failures)


def test_scenario_runner_produces_expected_counts(scenario_client: GraphClient):
    """Sanity-check the runner's own bookkeeping matches what's in the graph."""
    scenario_path = CANONICAL_DIR / "basic_reinforcement.yaml"
    scenario = load_scenario(scenario_path)

    result = run_scenario(scenario, scenario_client)

    # The runner tracks how many of each thing it wrote.
    assert result.events_written == 5
    assert result.vlm_decisions_written == 5
    assert result.citations_written == 5

    # And the graph should reflect that.
    assert scenario_client.count_events(camera_id="front_south_cam") == 5
    assert scenario_client.count_vlm_decisions() == 5

    # Edge weights all carry the template default.
    edges = scenario_client.get_citations_from("dec_d3")
    assert len(edges) == 1
    assert edges[0].memory_id == "actor_alice"
    assert edges[0].weight == 0.7


# ─── Milkman: dismissal-policy short-circuit ──────────────────────────


def test_milkman_scenario(scenario_client: GraphClient):
    """30-day recurring pattern; VLM authors a dismissal on day 1;
    subsequent matching events are silenced.

    This is the central efficiency property of the dismissal-policy
    design: one VLM call buys ~12 silenced subsequent events.
    """
    scenario_path = CANONICAL_DIR / "milkman.yaml"
    scenario = load_scenario(scenario_path)

    result = run_scenario(scenario, scenario_client)

    print(  # noqa: T201
        f"\nMilkman: events={result.events_written}, "
        f"vlm_calls={result.vlm_decisions_written}, "
        f"policies={result.policies_written}, "
        f"dismissed={result.events_dismissed_by_policy}, "
        f"simulated_days={result.elapsed_simulated_seconds / 86400:.1f}"
    )

    assert result.passed, "Milkman scenario assertions failed:\n  " + "\n  ".join(
        result.assertion_failures
    )


def test_milkman_dismissal_silences_subsequent_events(scenario_client: GraphClient):
    """Concrete check: VLM invoked exactly once (day 1), all
    subsequent events recorded but dismissed by policy."""
    scenario_path = CANONICAL_DIR / "milkman.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    assert result.vlm_decisions_written == 1, (
        f"expected 1 VLM call (day 1 only), got {result.vlm_decisions_written}"
    )
    assert result.policies_written == 1
    assert scenario_client.count_policies(kind="dismissal") == 1

    total_events = scenario_client.count_events(camera_id="front_porch")
    assert total_events >= 10, f"only {total_events} events written"

    # All events past day 1 should have been dismissed by the policy.
    assert result.events_dismissed_by_policy == total_events - 1, (
        f"expected {total_events - 1} dismissals, got {result.events_dismissed_by_policy}"
    )


# ─── PoolDog: novelty breaks dismissal subset match ───────────────────


def test_pool_dog_scenario(scenario_client: GraphClient):
    """7-day scenario where a {dog} dismissal silences routine dog events
    but a day-5 {dog, person} event breaks the subset rule and re-invokes
    the VLM. This is the novelty-detection counterpart to Milkman.
    """
    scenario_path = CANONICAL_DIR / "pool_dog.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    print(  # noqa: T201
        f"\nPoolDog: events={result.events_written}, "
        f"vlm_calls={result.vlm_decisions_written}, "
        f"policies={result.policies_written}, "
        f"dismissed={result.events_dismissed_by_policy}"
    )

    assert result.passed, "PoolDog scenario assertions failed:\n  " + "\n  ".join(
        result.assertion_failures
    )


def test_pool_dog_novel_tag_breaks_dismissal(scenario_client: GraphClient):
    """The critical architectural check: when an event's tag_set is NOT
    a subset of the policy's allowed set, the policy must not match —
    VLM is re-invoked.
    """
    scenario_path = CANONICAL_DIR / "pool_dog.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    # 2 VLM calls: day 1 + day 5. The recurring template fires on
    # every day from 2 to 7 (including day 5 alongside the declared
    # event), all dismissed by policy → 6 dismissals.
    assert result.vlm_decisions_written == 2
    assert result.events_dismissed_by_policy == 6
    assert result.policies_written == 1

    # The day-5 escalation decision exists and cites Rex.
    edges = scenario_client.get_citations_from("dec_d5_intruder")
    assert len(edges) == 1
    assert edges[0].memory_id == "actor_rex"


# ─── RareEvent: long-tail protection ──────────────────────────────────


def test_rare_event_scenario(scenario_client: GraphClient):
    """90-day scenario where a critical edge from day 1 must survive
    89 days of disuse. The Mnemosyne reverse-sigmoid decay floor
    (d=0.05) is what makes this work — a pure exponential would let
    this edge decay to ~zero.
    """
    scenario_path = CANONICAL_DIR / "rare_event.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    print(  # noqa: T201
        f"\nRareEvent: events={result.events_written}, "
        f"vlm_calls={result.vlm_decisions_written}, "
        f"simulated_days={result.elapsed_simulated_seconds / 86400:.1f}"
    )

    assert result.passed, "RareEvent scenario assertions failed:\n  " + "\n  ".join(
        result.assertion_failures
    )


def test_rare_event_critical_edge_survives_90_days(scenario_client: GraphClient):
    """Concrete proof of long-tail protection: by day 90, the critical
    actor's incoming CITED edge from day 1 still exists in the graph
    with its original weight (decay is applied at query time during
    pruning, not by mutating stored edges).
    """
    scenario_path = CANONICAL_DIR / "rare_event.yaml"
    scenario = load_scenario(scenario_path)
    run_scenario(scenario, scenario_client)

    # The actor_threat node still has its single high-weight edge from
    # day 1. The persistent edge weight is 1.0 (we don't decay stored
    # weights — decay applies during pruning queries).
    edges_to_threat = scenario_client.get_citations_to("actor_threat")
    assert len(edges_to_threat) == 1
    assert edges_to_threat[0].weight == 1.0
    assert edges_to_threat[0].decision_id == "dec_d1_rare_critical"

    # At a high pruning threshold (0.9), the threat actor SHOULD show
    # up as a candidate — even its strongest edge has decayed below 0.9
    # by day 90. This proves the decay function is firing.
    high_threshold_candidates = scenario_client.candidates_for_pruning(threshold=0.9)
    threat_ids = {c.node_id for c in high_threshold_candidates}
    assert "actor_threat" in threat_ids, (
        "actor_threat should be a pruning candidate at threshold 0.9 "
        "after 90 days of disuse (decay is working)"
    )

    # But at a low threshold near the floor (0.04, just below
    # Mnemosyne's d=0.05), it MUST NOT be — that's the long-tail
    # protection.
    low_threshold_candidates = scenario_client.candidates_for_pruning(threshold=0.04)
    safe_ids = {c.node_id for c in low_threshold_candidates}
    assert "actor_threat" not in safe_ids, (
        "actor_threat must survive pruning at threshold 0.04 because "
        "Mnemosyne's decay floor (d=0.05) keeps weight x decay above 0.04 "
        "even after 90 days of disuse. Long-tail protection."
    )


# ─── NoiseFloor: ambient noise produces no citations ──────────────────


def test_noise_floor_scenario(scenario_client: GraphClient):
    """7 days of ambient noise only — zero declared events. Asserts
    the negative property: ambient motion does NOT invoke the VLM,
    does NOT spawn citations, does NOT author policies."""
    scenario_path = CANONICAL_DIR / "noise_floor.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    print(  # noqa: T201
        f"\nNoiseFloor: events={result.events_written}, "
        f"vlm_calls={result.vlm_decisions_written}, "
        f"citations={result.citations_written}, "
        f"policies={result.policies_written}, "
        f"noise_events={result.noise_events_generated}"
    )

    assert result.passed, "NoiseFloor scenario assertions failed:\n  " + "\n  ".join(
        result.assertion_failures
    )


def test_noise_floor_concrete_invariants(scenario_client: GraphClient):
    """Concrete graph-state checks complementing the YAML assertions:
    the in-graph Event count matches noise_events_generated (every
    noise event lands as a real graph node), and there are no
    KnownActor citations anywhere."""
    scenario_path = CANONICAL_DIR / "noise_floor.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    # Every noise event becomes a graph Event node.
    assert result.events_written == result.noise_events_generated, (
        f"every noise event should be recorded as a graph Event "
        f"(events_written={result.events_written}, "
        f"noise_events_generated={result.noise_events_generated})"
    )
    assert scenario_client.count_events() == result.noise_events_generated

    # Zero VLM decisions, zero citations.
    assert result.vlm_decisions_written == 0
    assert result.citations_written == 0
    assert scenario_client.count_vlm_decisions() == 0

    # Zero policies.
    assert result.policies_written == 0
    assert scenario_client.count_policies() == 0


# ─── PolicyExpiry: TTL boundary ───────────────────────────────────────


def test_policy_expiry_scenario(scenario_client: GraphClient):
    """Day 1 authors a 1-day dismissal; day 2 fires inside TTL
    (dismissed); day 4 fires past TTL (VLM re-invoked)."""
    scenario_path = CANONICAL_DIR / "policy_expiry.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    print(  # noqa: T201
        f"\nPolicyExpiry: events={result.events_written}, "
        f"vlm_calls={result.vlm_decisions_written}, "
        f"policies={result.policies_written}, "
        f"dismissed={result.events_dismissed_by_policy}"
    )

    assert result.passed, "PolicyExpiry scenario assertions failed:\n  " + "\n  ".join(
        result.assertion_failures
    )


def test_policy_expiry_boundary_behaviour(scenario_client: GraphClient):
    """Concrete checks for the TTL boundary:
    * exactly 1 event dismissed (day 2),
    * day-4 decision exists with a fresh citation to Rex."""
    scenario_path = CANONICAL_DIR / "policy_expiry.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    assert result.events_dismissed_by_policy == 1, (
        f"expected 1 dismissed event (day 2), got {result.events_dismissed_by_policy}"
    )

    # The day-4 re-engagement decision must exist and cite Rex.
    edges = scenario_client.get_citations_from("dec_d4_rex_reengagement")
    assert len(edges) == 1
    assert edges[0].memory_id == "actor_rex"

    # Three Event nodes total (one for each declared event), even the
    # dismissed one is recorded.
    assert scenario_client.count_events(camera_id="backyard_cam") == 3


# ─── CameraScopedPolicy: scope isolation ──────────────────────────────


def test_camera_scoped_policy_scenario(scenario_client: GraphClient):
    """Backyard dismissal does not silence driveway events."""
    scenario_path = CANONICAL_DIR / "camera_scoped_policy.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    print(  # noqa: T201
        f"\nCameraScopedPolicy: events={result.events_written}, "
        f"vlm_calls={result.vlm_decisions_written}, "
        f"policies={result.policies_written}, "
        f"dismissed={result.events_dismissed_by_policy}"
    )

    assert result.passed, "CameraScopedPolicy scenario assertions failed:\n  " + "\n  ".join(
        result.assertion_failures
    )


def test_camera_scoped_policy_does_not_bleed(scenario_client: GraphClient):
    """Concrete proof of scope isolation: the day-2 backyard event is
    dismissed but the day-2 driveway event is NOT."""
    scenario_path = CANONICAL_DIR / "camera_scoped_policy.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    assert result.events_dismissed_by_policy == 1, (
        "exactly the day-2 backyard event should be dismissed; "
        f"got {result.events_dismissed_by_policy}"
    )

    # Two events on backyard_cam (day1 + day2; day2 dismissed).
    assert scenario_client.count_events(camera_id="backyard_cam") == 2
    # One event on driveway_cam (day2 escalation).
    assert scenario_client.count_events(camera_id="driveway_cam") == 1

    # The driveway escalation decision exists with weight 0.8.
    edges = scenario_client.get_citations_from("dec_d2_driveway_dog")
    assert len(edges) == 1
    assert edges[0].memory_id == "actor_rex"
    assert edges[0].weight == 0.8


# ─── SoakOneYear: 365-day decay-floor verification (slow) ─────────────


@pytest.mark.slow
def test_soak_one_year(scenario_client: GraphClient):
    """365-day soak: max-weight day-1 citation must survive a full
    calendar year of disuse because the Mnemosyne decay floor keeps
    weight*decay above the 0.04 pruning threshold.

    Marked slow so it skips on PR runs; nightly soak job picks it up.
    Pass ``-m slow`` (or remove the default exclusion) to run.
    """
    scenario_path = CANONICAL_DIR / "soak_one_year.yaml"
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, scenario_client)

    print(  # noqa: T201
        f"\nSoakOneYear: events={result.events_written}, "
        f"vlm_calls={result.vlm_decisions_written}, "
        f"noise={result.noise_events_generated}, "
        f"simulated_days={result.elapsed_simulated_seconds / 86400:.0f}"
    )

    assert result.passed, "SoakOneYear scenario assertions failed:\n  " + "\n  ".join(
        result.assertion_failures
    )

    # Concrete decay-floor checks evaluated at end-of-scenario (day 365):
    # * actor_threat is NOT a pruning candidate at 0.04 (floor protects).
    # * actor_threat IS a candidate at any threshold strictly above
    #   the floor (0.06), proving decay actually fired.
    end_ts = result.final_simulated_ts
    low_threshold_candidates = scenario_client.candidates_for_pruning(threshold=0.04, now_ts=end_ts)
    low_ids = {c.node_id for c in low_threshold_candidates}
    assert "actor_threat" not in low_ids, (
        "after 365 days of disuse, actor_threat's weight (1.0) x "
        "decay-floor (0.05) must remain above threshold 0.04 — "
        "long-tail protection at year scale"
    )

    above_floor_candidates = scenario_client.candidates_for_pruning(threshold=0.06, now_ts=end_ts)
    above_floor_ids = {c.node_id for c in above_floor_candidates}
    assert "actor_threat" in above_floor_ids, (
        "at threshold 0.06 (above the floor), actor_threat should be "
        "a candidate — proves decay fired across the year"
    )
