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
from sentihome_memory.graph import GraphClient
from synthesis.runner import run_scenario
from synthesis.scenarios.schema import load_scenario

CANONICAL_DIR = Path(__file__).parent / "synthesis" / "scenarios" / "canonical"


@pytest.fixture(params=["in_memory", "neo4j"])
def scenario_client(request, in_memory_client, neo4j_client) -> GraphClient:
    """Run each scenario test against both backends."""
    if request.param == "in_memory":
        return in_memory_client
    return neo4j_client


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
