"""Tests for the F4 (Home Assistant down) health probe."""

from __future__ import annotations

import pytest
from kukiihome_ha_agent.health import make_ha_health_check, probe_ha_health
from kukiihome_shared.health import FailureMode


def test_connected_is_ok():
    h = probe_ha_health(connected=True, now=0.0)
    assert h.status == "ok"
    assert h.component == "home_assistant"
    assert h.critical is False  # ok health isn't flagged critical


def test_disconnected_is_offline_and_critical_by_default():
    h = probe_ha_health(connected=False, now=0.0)
    assert h.status == "offline"
    assert h.critical is True


def test_disconnected_critical_can_be_softened():
    h = probe_ha_health(connected=False, critical=False, now=0.0)
    assert h.status == "offline"
    assert h.critical is False


def test_custom_detail():
    h = probe_ha_health(connected=False, detail="auth rejected", now=0.0)
    assert h.detail == "auth rejected"


@pytest.mark.asyncio
async def test_make_check_declares_f4_and_reads_connection():
    state = {"connected": False}
    check = make_ha_health_check(lambda: state["connected"], clock=lambda: 0.0)
    assert check.failure_mode == FailureMode.F4_HA_DOWN
    assert check.critical is True
    assert check.name == "home_assistant"

    health = await check.probe()
    assert health.status == "offline"

    state["connected"] = True  # probe reads freshly each poll
    health = await check.probe()
    assert health.status == "ok"
