"""Pytest configuration for sentihome-memory tests.

Adds the ``tests/`` directory to sys.path so test modules can import
each other via package paths (e.g. ``from synthesis.households.schema
import ...``). The ``synthesis`` subsystem is test-only — not shipped
in the production wheel — so we don't want it on the production
sentihome_memory package path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
