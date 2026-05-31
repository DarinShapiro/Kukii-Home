"""Tests for ComponentHealth, overall_status rollup, and HealthRegistry."""

from __future__ import annotations

import pytest
from kukiihome_shared.health import (
    ComponentHealth,
    HealthRegistry,
    overall_status,
)


def _h(component: str, status: str, *, critical: bool = False) -> ComponentHealth:
    return ComponentHealth(component=component, status=status, critical=critical, updated_ts=1.0)


def test_overall_healthy_when_all_ok():
    comps = (_h("a", "ok"), _h("b", "ok"))
    assert overall_status(comps) == "healthy"


def test_overall_degraded_on_noncritical_offline_or_any_degraded():
    assert overall_status((_h("a", "ok"), _h("b", "offline"))) == "degraded"
    assert overall_status((_h("a", "degraded"),)) == "degraded"
    # A *critical* component merely degraded (not offline) is still degraded.
    assert overall_status((_h("bus", "degraded", critical=True),)) == "degraded"


def test_overall_critical_only_when_critical_component_offline():
    assert overall_status((_h("bus", "offline", critical=True),)) == "critical"
    # Non-critical offline alongside is still just... critical (worst wins).
    comps = (_h("cam", "offline"), _h("ha", "offline", critical=True))
    assert overall_status(comps) == "critical"


def test_overall_empty_is_healthy():
    assert overall_status(()) == "healthy"


@pytest.mark.asyncio
async def test_registry_report_get_and_last_write_wins():
    reg = HealthRegistry()
    await reg.report(_h("cam", "ok"))
    await reg.report(_h("cam", "offline"))  # newer wins
    got = await reg.get("cam")
    assert got is not None and got.status == "offline"
    assert await reg.get("missing") is None


@pytest.mark.asyncio
async def test_registry_snapshot_sorts_and_rolls_up():
    reg = HealthRegistry()
    await reg.report(_h("ha", "offline", critical=True))
    await reg.report(_h("cam", "ok"))
    snap = await reg.snapshot(now=42.0)
    assert snap.generated_ts == 42.0
    assert [c.component for c in snap.components] == ["cam", "ha"]  # sorted
    assert snap.overall == "critical"
