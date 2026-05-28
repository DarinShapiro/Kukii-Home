"""Decoupling guard: preprocessor must not import HA-side modules.

The recognition preprocessor runs on the inference box, a SEPARATE
machine from the HA add-on host. The only legitimate coupling is
via wire contracts in ``sentihome_shared``. This test walks every
Python source file under ``services/preprocessor/src/`` and asserts
that no import targets a forbidden HA-side package.

What this catches:

* A drive-by ``from sentihome_memory.graph import GraphClient`` that
  would silently work in dev (everything's installed editable) but
  break in production where the preprocessor's container doesn't
  include the memory service code.
* A maintainer pulling in ``sentihome_core``'s dispatch logic
  thinking it's reusable, accumulating coupling that locks the two
  deployments together.

What this DOESN'T catch (the wire contracts are correctly shared):

* ``sentihome_shared.preprocessor.contracts`` — that's the contract
* ``sentihome_shared.bus`` — generic NATS plumbing
* ``sentihome_shared.topology`` — read-only camera/area config
* Anything else under ``sentihome_shared``
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PREPROCESSOR_SRC = (
    Path(__file__).resolve().parent.parent / "src" / "sentihome_preprocessor"
)

# These are HA-side packages. The preprocessor must NEVER import any
# of them. ``sentihome_shared`` is the legitimate coupling point and
# is NOT on this list.
_FORBIDDEN_PACKAGES = frozenset(
    [
        "sentihome_ha_agent",
        "sentihome_memory",
        "sentihome_core",
        "sentihome_notify",
        "sentihome_vlm_router",
        "sentihome_detector",
    ]
)


def _all_python_files() -> list[Path]:
    return sorted(_PREPROCESSOR_SRC.rglob("*.py"))


def _imported_top_level_modules(source: str) -> set[str]:
    """Extract the top-level package name from every import in
    ``source``. ``from a.b.c import d`` → ``{"a"}``; ``import x.y``
    → ``{"x"}``."""
    tree = ast.parse(source)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level > 0:
                # Relative import; can't refer to a foreign package.
                continue
            out.add(node.module.split(".", 1)[0])
    return out


@pytest.mark.parametrize(
    "py_file",
    _all_python_files(),
    ids=lambda p: str(p.relative_to(_PREPROCESSOR_SRC)),
)
def test_preprocessor_file_has_no_forbidden_imports(py_file: Path):
    source = py_file.read_text(encoding="utf-8")
    imports = _imported_top_level_modules(source)
    forbidden_hits = imports & _FORBIDDEN_PACKAGES
    assert not forbidden_hits, (
        f"{py_file.relative_to(_PREPROCESSOR_SRC)} imports forbidden HA-side "
        f"package(s): {sorted(forbidden_hits)}.\n"
        f"The preprocessor lives on the inference box and must only "
        f"couple to HA-side code via wire contracts in sentihome_shared. "
        f"If you need a new contract, add it under "
        f"sentihome_shared.preprocessor and have both sides import from there."
    )


def test_guard_covers_a_realistic_file_set():
    """Sanity: make sure we're scanning something. If the package layout
    moves and the rglob finds nothing, the parametrized test silently
    passes with zero cases — this check fails loudly instead."""
    files = _all_python_files()
    assert len(files) >= 5, f"expected ≥5 source files, found {len(files)}"


def test_forbidden_list_does_not_include_shared_package():
    """The whole point is that sentihome_shared IS allowed."""
    assert "sentihome_shared" not in _FORBIDDEN_PACKAGES
