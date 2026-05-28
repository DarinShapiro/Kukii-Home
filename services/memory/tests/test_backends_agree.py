"""Cross-backend differential test.

For each canonical scenario: run the runner against both
``InMemoryGraphClient`` and ``Neo4jGraphClient``, then assert the
resulting graph snapshots are byte-identical (modulo a tiny float
tolerance on timestamps/weights).

Why this exists separately from the parametrized scenario tests:
those check aggregates (counts, single-edge weights). A backend bug
that silently drops a recurring event would still satisfy aggregate
assertions if the count happened to match — but the snapshot diff
catches it because the dropped event's id is now absent.

Skips cleanly when Docker isn't running; the in-memory side still
exercises the snapshot+diff utilities through the unit-level diff
tests in ``test_snapshot_diff.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from synthesis.runner import run_scenario
from synthesis.scenarios.schema import load_scenario
from synthesis.snapshot import GraphSnapshot, diff_snapshots

CANONICAL_DIR = Path(__file__).parent / "synthesis" / "scenarios" / "canonical"

# Every canonical scenario we expect the two backends to agree on.
# Phase 1B+ list — add new scenarios here as they land.
CANONICAL_SCENARIOS = [
    "basic_reinforcement",
    "milkman",
    "pool_dog",
    "rare_event",
    "noise_floor",
    "policy_expiry",
    "camera_scoped_policy",
]


@pytest.mark.parametrize("scenario_name", CANONICAL_SCENARIOS)
def test_backends_produce_identical_graph_state(scenario_name: str, in_memory_client, neo4j_client):
    """Run ``scenario_name`` against both backends; snapshots must match.

    Eagerly requests both clients (not via the lazy ``scenario_client``
    indirection) because this test fundamentally needs both — there's
    no in-memory-only variant of a cross-backend check. The neo4j
    fixture skips cleanly if Docker is unreachable, which is the
    right behavior here.
    """
    scenario_path = CANONICAL_DIR / f"{scenario_name}.yaml"
    scenario = load_scenario(scenario_path)

    result_a = run_scenario(scenario, in_memory_client)
    result_b = run_scenario(scenario, neo4j_client)

    # Sanity: the runner's own bookkeeping should agree across backends.
    # If this fails, something is wrong with the runner itself, not the
    # backends — surface that distinctly.
    assert result_a.events_written == result_b.events_written, (
        f"runner-level events_written diverged: "
        f"in_memory={result_a.events_written}, neo4j={result_b.events_written}"
    )
    assert result_a.vlm_decisions_written == result_b.vlm_decisions_written
    assert result_a.policies_written == result_b.policies_written
    assert result_a.events_dismissed_by_policy == result_b.events_dismissed_by_policy

    snap_a = GraphSnapshot.from_client(in_memory_client)
    snap_b = GraphSnapshot.from_client(neo4j_client)

    diffs = diff_snapshots(snap_a, snap_b)

    assert not diffs, (
        f"backends produced different graph state for {scenario_name!r}:\n  " + "\n  ".join(diffs)
    )
