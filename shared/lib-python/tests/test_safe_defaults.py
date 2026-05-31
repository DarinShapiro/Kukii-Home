"""Tests for the §19 safe-defaults matrix."""

from __future__ import annotations

from kukiihome_shared.health import FailureMode, SafeDefaultsMatrix


def test_no_active_modes_allows_everything():
    m = SafeDefaultsMatrix()
    for action in ("lights", "notifications", "lock", "unlock", "siren", "speaker"):
        assert m.permission(action, []) == "allow"
        assert m.is_allowed(action, []) is True


def test_ha_down_blocks_device_control_keeps_notifications():
    m = SafeDefaultsMatrix()
    active = [FailureMode.F4_HA_DOWN]
    assert m.permission("lights", active) == "block"
    assert m.permission("lock", active) == "block"
    assert m.permission("speaker", active) == "block"
    assert m.permission("notifications", active) == "allow"


def test_bus_down_blocks_everything_including_notifications():
    m = SafeDefaultsMatrix()
    active = [FailureMode.F5_BUS_DOWN]
    assert set(m.blocked_actions(active)) == {
        "lights",
        "notifications",
        "lock",
        "unlock",
        "siren",
        "speaker",
    }


def test_camera_offline_blocks_locks_allows_lights_speaker():
    m = SafeDefaultsMatrix()
    active = [FailureMode.F1_CAMERA_OFFLINE]
    assert m.is_allowed("lights", active)
    assert m.is_allowed("speaker", active)
    assert m.permission("lock", active) == "block"
    assert m.permission("unlock", active) == "block"


def test_vlm_down_makes_lock_conditional_not_allowed():
    m = SafeDefaultsMatrix()
    active = [FailureMode.F7_VLM_DOWN]
    assert m.permission("lock", active) == "conditional"
    # conditional is NOT auto-allowed.
    assert m.is_allowed("lock", active) is False


def test_internet_down_imposes_no_restriction():
    m = SafeDefaultsMatrix()
    assert m.blocked_actions([FailureMode.F8_INTERNET_DOWN]) == ()


def test_modes_absent_from_table_impose_no_restriction():
    m = SafeDefaultsMatrix()
    for mode in (
        FailureMode.F2_RTSP_STUTTER,
        FailureMode.F9_MEMORY_PRESSURE,
        FailureMode.F10_POWER_LOSS,
    ):
        assert m.blocked_actions([mode]) == ()


def test_combining_modes_takes_most_restrictive():
    m = SafeDefaultsMatrix()
    # GPU saturated alone: lock is conditional. Add HA down: lock blocks.
    active = [FailureMode.F6_GPU_SATURATED, FailureMode.F4_HA_DOWN]
    assert m.permission("lock", active) == "block"
    # speaker: gpu allows, ha blocks -> block wins.
    assert m.permission("speaker", active) == "block"
    # notifications: both allow.
    assert m.permission("notifications", active) == "allow"


def test_verdicts_returns_full_map():
    m = SafeDefaultsMatrix()
    v = m.verdicts([FailureMode.F1_CAMERA_OFFLINE])
    assert v == {
        "lights": "allow",
        "notifications": "allow",
        "lock": "block",
        "unlock": "block",
        "siren": "block",
        "speaker": "allow",
    }


def test_failure_mode_code_and_label():
    assert FailureMode.F4_HA_DOWN.code == "F4"
    assert FailureMode.F4_HA_DOWN.label == "Home Assistant down"
