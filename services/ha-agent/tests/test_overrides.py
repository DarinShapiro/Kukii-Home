"""Tests for the persistent overrides store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sentihome_ha_agent.overrides import (
    load_overrides,
    reset_device,
    save_overrides,
    set_device_override,
)


@pytest.fixture
def overrides_path(tmp_path: Path) -> Path:
    return tmp_path / "adapter_overrides.json"


def test_load_returns_empty_when_missing(overrides_path: Path):
    assert load_overrides(overrides_path) == {}


def test_save_then_load_roundtrip(overrides_path: Path):
    data = {"dahuapoolcam": {"enabled": True, "cooldown_override": 30.0}}
    save_overrides(data, overrides_path)
    loaded = load_overrides(overrides_path)
    assert loaded == data


def test_save_is_atomic_uses_tmpfile(overrides_path: Path):
    """save should leave no .tmp behind after success."""
    save_overrides({"front_south": {"enabled": False}}, overrides_path)
    assert overrides_path.exists()
    assert not overrides_path.with_suffix(overrides_path.suffix + ".tmp").exists()


def test_save_creates_parent_dir(tmp_path: Path):
    nested = tmp_path / "deep" / "nested" / "overrides.json"
    save_overrides({"x": {"enabled": True}}, nested)
    assert nested.exists()


def test_save_writes_versioned_payload(overrides_path: Path):
    save_overrides({"x": {"enabled": True}}, overrides_path)
    raw = json.loads(overrides_path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["devices"] == {"x": {"enabled": True}}


def test_load_returns_empty_on_corrupt_json(overrides_path: Path):
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    overrides_path.write_text("{not json", encoding="utf-8")
    # Must not raise — the add-on should boot even with a corrupt file.
    assert load_overrides(overrides_path) == {}


def test_load_returns_empty_on_wrong_shape(overrides_path: Path):
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    overrides_path.write_text('["not", "an", "object"]', encoding="utf-8")
    assert load_overrides(overrides_path) == {}


def test_load_drops_non_dict_entries(overrides_path: Path):
    """Defensive: a hand-edited bad entry shouldn't crash the loader."""
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    overrides_path.write_text(
        json.dumps({"version": 1, "devices": {"good": {"enabled": True}, "bad": "string"}}),
        encoding="utf-8",
    )
    loaded = load_overrides(overrides_path)
    assert loaded == {"good": {"enabled": True}}


# ─── set_device_override ─────────────────────────────────────────────


def test_set_device_override_creates_entry():
    overrides: dict[str, dict] = {}
    set_device_override(overrides, "dahuapoolcam", enabled=False)
    assert overrides == {"dahuapoolcam": {"enabled": False}}


def test_set_device_override_partial_update_preserves_other_fields():
    overrides: dict[str, dict] = {"front_south": {"enabled": True, "cooldown_override": 30.0}}
    set_device_override(overrides, "front_south", stream_override="camera.front_south_fluent")
    assert overrides["front_south"] == {
        "enabled": True,
        "cooldown_override": 30.0,
        "stream_override": "camera.front_south_fluent",
    }


def test_set_device_override_sorts_motion_list():
    overrides: dict[str, dict] = {}
    set_device_override(
        overrides,
        "x",
        motion_override=["binary_sensor.z", "binary_sensor.a"],
    )
    assert overrides["x"]["motion_override"] == ["binary_sensor.a", "binary_sensor.z"]


def test_set_device_override_clear_flags_remove_fields():
    overrides: dict[str, dict] = {
        "x": {"enabled": True, "stream_override": "camera.x_main", "cooldown_override": 30.0}
    }
    set_device_override(overrides, "x", clear_stream=True, clear_cooldown=True)
    assert overrides["x"] == {"enabled": True}


def test_set_device_override_prunes_empty_entries():
    """A device whose only override was its enable flag, then we clear
    everything, should disappear so the next AI pick isn't accidentally
    pinned by a stale empty entry."""
    overrides: dict[str, dict] = {"x": {"stream_override": "camera.x_main"}}
    set_device_override(overrides, "x", clear_stream=True)
    assert "x" not in overrides


def test_reset_device_drops_entry():
    overrides: dict[str, dict] = {"x": {"enabled": True}, "y": {"enabled": False}}
    reset_device(overrides, "x")
    assert overrides == {"y": {"enabled": False}}


def test_reset_device_noop_when_missing():
    overrides: dict[str, dict] = {"x": {"enabled": True}}
    reset_device(overrides, "missing")  # Must not raise.
    assert overrides == {"x": {"enabled": True}}
