"""Tests for the UI-driven notify-services store (v0.3.13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kukiihome_ha_agent.notify_overrides import (
    load_notify_services,
    resolve_initial_services,
    save_notify_services,
)


@pytest.fixture
def overrides_path(tmp_path: Path) -> Path:
    return tmp_path / "notify_overrides.json"


def test_load_returns_none_when_missing(overrides_path: Path):
    """None vs [] is meaningful: None = 'no UI choice', [] = 'unchecked all'."""
    assert load_notify_services(overrides_path) is None


def test_save_then_load_roundtrip(overrides_path: Path):
    save_notify_services(["notify.mobile_app_x", "notify.alexa"], overrides_path)
    assert load_notify_services(overrides_path) == [
        "notify.alexa",
        "notify.mobile_app_x",
    ]


def test_save_deduplicates_and_sorts(overrides_path: Path):
    save_notify_services(
        ["notify.b", "notify.a", "notify.b"],
        overrides_path,
    )
    raw = json.loads(overrides_path.read_text(encoding="utf-8"))
    assert raw["services"] == ["notify.a", "notify.b"]
    assert raw["version"] == 1


def test_save_empty_list_is_a_valid_choice(overrides_path: Path):
    """User unchecks every checkbox → file exists with empty list,
    which load returns as [] (not None)."""
    save_notify_services([], overrides_path)
    assert load_notify_services(overrides_path) == []


def test_load_returns_none_on_corrupt_json(overrides_path: Path):
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    overrides_path.write_text("{nope", encoding="utf-8")
    assert load_notify_services(overrides_path) is None


def test_load_drops_non_string_entries(overrides_path: Path):
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    overrides_path.write_text(
        json.dumps({"version": 1, "services": ["notify.a", 42, "notify.b"]}),
        encoding="utf-8",
    )
    assert load_notify_services(overrides_path) == ["notify.a", "notify.b"]


def test_atomic_write_no_tmp_left_behind(overrides_path: Path):
    save_notify_services(["notify.x"], overrides_path)
    assert overrides_path.exists()
    assert not overrides_path.with_suffix(overrides_path.suffix + ".tmp").exists()


def test_save_creates_parent_dir(tmp_path: Path):
    deep = tmp_path / "a" / "b" / "c" / "n.json"
    save_notify_services(["notify.x"], deep)
    assert deep.exists()


# ─── resolve_initial_services ────────────────────────────────────────


def test_resolve_returns_yaml_when_no_file(tmp_path: Path):
    p = tmp_path / "n.json"
    result = resolve_initial_services(["notify.from_yaml"], p)
    assert result == ["notify.from_yaml"]


def test_resolve_ui_overrides_yaml_when_file_exists(tmp_path: Path):
    p = tmp_path / "n.json"
    save_notify_services(["notify.from_ui"], p)
    result = resolve_initial_services(["notify.from_yaml"], p)
    assert result == ["notify.from_ui"]


def test_resolve_empty_ui_wins_over_yaml(tmp_path: Path):
    """The user explicitly unchecking everything must beat YAML."""
    p = tmp_path / "n.json"
    save_notify_services([], p)
    result = resolve_initial_services(["notify.from_yaml"], p)
    assert result == []


def test_resolve_handles_empty_yaml_and_no_file(tmp_path: Path):
    p = tmp_path / "n.json"
    assert resolve_initial_services([], p) == []
