"""Pytest configuration for sentihome-memory tests.

Adds the ``tests/`` directory to sys.path so test modules can import
each other via package paths (e.g. ``from synthesis.households.schema
import ...``). The ``synthesis`` subsystem is test-only — not shipped
in the production wheel — so we don't want it on the production
sentihome_memory package path.

Also re-exports the Neo4j fixtures from ``synthesis.fixtures.neo4j_fixture``
so tests can request them without long import paths.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Surface the Neo4j + in-memory client fixtures at the tests/ level
# so any test module can just ``def test_x(neo4j_client): ...`` or
# ``def test_x(in_memory_client): ...``. pytest discovers fixtures by
# import; importing them here makes them available everywhere.
from synthesis.fixtures.neo4j_fixture import (  # noqa: F401
    in_memory_client,
    neo4j_client,
    neo4j_container,
    neo4j_driver,
)
