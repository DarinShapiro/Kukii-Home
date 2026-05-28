#!/usr/bin/env python
"""Run a memory-harness scenario against the persistent dev Neo4j.

Usage:
    python scripts/dev/seed_memory_scenario.py <scenario_name> [--clear]
    python scripts/dev/seed_memory_scenario.py --list

Examples:
    # Run milkman against dev Neo4j, append to whatever's already there.
    python scripts/dev/seed_memory_scenario.py milkman

    # Wipe everything first, then run pool_dog.
    python scripts/dev/seed_memory_scenario.py pool_dog --clear

    # Show available scenarios.
    python scripts/dev/seed_memory_scenario.py --list

After the script returns, open http://localhost:7474 in your browser:
    user:     neo4j
    password: sentihome   (or whatever NEO4J_PASSWORD is set to)

Then explore with Cypher, e.g.:
    MATCH (n) RETURN n LIMIT 50
    MATCH (d:VLMDecision)-[r:CITED]->(m) RETURN d, r, m
    MATCH (p:Policy) RETURN p

This script is intentionally separate from pytest's testcontainers
fixture: testcontainers tears down its ephemeral Neo4j between
sessions, which is right for tests but wrong for hand-inspection.
The dev compose stack (infrastructure/docker/dev.yml) provides a
long-lived Neo4j at bolt://localhost:7687 that survives across
runs; this script writes into THAT one.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCENARIOS_DIR = REPO_ROOT / "services/memory/tests/synthesis/scenarios/canonical"

# Make the test-only synthesis package importable.
sys.path.insert(0, str(REPO_ROOT / "services/memory/tests"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        help="Scenario name (without .yaml). E.g. 'milkman'. Use --list to see options.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Wipe the graph before seeding. Safer for repeatable inspection.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available canonical scenarios and exit.",
    )
    parser.add_argument(
        "--uri",
        default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        help="Bolt URI (default: bolt://localhost:7687 or $NEO4J_URI)",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("NEO4J_USER", "neo4j"),
        help="Neo4j username (default: neo4j or $NEO4J_USER)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("NEO4J_PASSWORD", "sentihome"),
        help="Neo4j password (default: sentihome or $NEO4J_PASSWORD)",
    )
    args = parser.parse_args()

    if args.list:
        return _print_available_scenarios()

    if not args.scenario:
        parser.error("Pass a scenario name (or --list to see options)")
        return 2  # unreachable; parser.error exits

    scenario_path = SCENARIOS_DIR / f"{args.scenario}.yaml"
    if not scenario_path.exists():
        print(f"ERROR: scenario {args.scenario!r} not found at {scenario_path}")  # noqa: T201
        print("Available scenarios:")  # noqa: T201
        _print_available_scenarios()
        return 1

    # Defer heavy imports until we actually need them (keeps --list snappy).
    from neo4j import GraphDatabase

    from sentihome_memory.graph import Neo4jGraphClient
    from synthesis.runner import run_scenario
    from synthesis.scenarios.schema import load_scenario

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    try:
        client = Neo4jGraphClient(driver=driver)
        client.initialize_schema()

        if args.clear:
            print("Clearing all nodes and edges from the dev Neo4j...")  # noqa: T201
            client.clear_all()

        scenario = load_scenario(scenario_path)
        print(  # noqa: T201
            f"Running scenario {scenario.name!r} "
            f"({scenario.duration_days} days, noise_profile={scenario.noise_profile!r}, "
            f"seed={scenario.seed})..."
        )
        result = run_scenario(scenario, client)

        print(_format_result(result))  # noqa: T201

        if not result.passed:
            print("\nAssertion FAILURES:")  # noqa: T201
            for f in result.assertion_failures:
                print(f"  - {f}")  # noqa: T201
            return 1

        print(  # noqa: T201
            f"\nOpen Neo4j Browser at http://localhost:7474 "
            f"(user: {args.user}, password: {args.password})\n"
            f"Try:  MATCH (n) RETURN n LIMIT 50"
        )
        return 0
    finally:
        driver.close()


def _print_available_scenarios() -> int:
    print("Available canonical scenarios:")  # noqa: T201
    for yaml_path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        print(f"  {yaml_path.stem}")  # noqa: T201
    return 0


def _format_result(result) -> str:
    return (
        f"\nScenario result: {result.scenario_name!r}\n"
        f"  events_written            = {result.events_written}\n"
        f"  vlm_decisions_written     = {result.vlm_decisions_written}\n"
        f"  citations_written         = {result.citations_written}\n"
        f"  policies_written          = {result.policies_written}\n"
        f"  events_dismissed_by_policy= {result.events_dismissed_by_policy}\n"
        f"  noise_events_generated    = {result.noise_events_generated}\n"
        f"  elapsed_simulated_days    = {result.elapsed_simulated_seconds / 86_400:.1f}\n"
        f"  passed                    = {result.passed}"
    )


if __name__ == "__main__":
    sys.exit(main())
