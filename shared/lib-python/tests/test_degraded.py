"""Tests for DegradedState + the SafeActionGate."""

from __future__ import annotations

from kukiihome_shared.health import (
    DegradedState,
    FailureMode,
    SafeActionGate,
)


def test_activate_deactivate_and_query():
    d = DegradedState()
    assert d.active() == frozenset()
    d.activate(FailureMode.F4_HA_DOWN)
    assert d.is_active(FailureMode.F4_HA_DOWN)
    assert d.active() == frozenset({FailureMode.F4_HA_DOWN})
    d.deactivate(FailureMode.F4_HA_DOWN)
    assert not d.is_active(FailureMode.F4_HA_DOWN)


def test_set_active_toggles():
    d = DegradedState()
    d.set_active(FailureMode.F7_VLM_DOWN, True)
    assert d.is_active(FailureMode.F7_VLM_DOWN)
    d.set_active(FailureMode.F7_VLM_DOWN, False)
    assert not d.is_active(FailureMode.F7_VLM_DOWN)


def test_gate_blocks_per_active_modes():
    d = DegradedState()
    gate = SafeActionGate(d)
    # Nothing degraded -> everything allowed.
    assert gate.is_allowed("lock")
    assert gate.is_allowed("notifications")
    # HA down -> device control blocked, notifications still allowed.
    d.activate(FailureMode.F4_HA_DOWN)
    assert gate.permission("lights") == "block"
    assert gate.permission("lock") == "block"
    assert gate.is_allowed("notifications")


def test_gate_conditional_on_vlm_down():
    d = DegradedState()
    gate = SafeActionGate(d)
    d.activate(FailureMode.F7_VLM_DOWN)
    assert gate.permission("lock") == "conditional"
    assert gate.is_allowed("lock") is False  # conditional != auto-allowed
    assert gate.is_allowed("lights")


def test_gate_combines_modes_most_restrictive():
    d = DegradedState()
    gate = SafeActionGate(d)
    d.activate(FailureMode.F6_GPU_SATURATED)  # lock conditional
    d.activate(FailureMode.F4_HA_DOWN)  # lock block
    assert gate.permission("lock") == "block"
    assert set(gate.blocked_actions()) >= {"lights", "lock", "unlock", "siren", "speaker"}
