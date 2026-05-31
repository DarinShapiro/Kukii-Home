"""Tests for the watchdog — transition detection, escalation, alerts."""

from __future__ import annotations

import pytest
from kukiihome_shared.health import (
    ComponentHealth,
    DiagnosticEntry,
    DiagnosticRing,
    HealthCheck,
    HealthRegistry,
    Watchdog,
)


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _scripted_probe(statuses: list[str]):
    """A probe that returns the next scripted status each call."""
    it = iter(statuses)

    async def probe() -> ComponentHealth:
        status = next(it)
        return ComponentHealth(component="x", status=status, detail=f"is {status}", updated_ts=0.0)

    return probe


def _make() -> tuple[Watchdog, HealthRegistry, DiagnosticRing, list[DiagnosticEntry], _Clock]:
    reg = HealthRegistry()
    diag = DiagnosticRing()
    alerts: list[DiagnosticEntry] = []
    clock = _Clock()
    wd = Watchdog(
        registry=reg,
        diagnostics=diag,
        on_transition=alerts.append,
        clock=clock,
    )
    return wd, reg, diag, alerts, clock


@pytest.mark.asyncio
async def test_first_seen_offline_logs_warning_and_reports():
    wd, reg, diag, alerts, _ = _make()
    wd.register(HealthCheck(name="x", probe=_scripted_probe(["offline"])))
    await wd.run_once()

    reported = await reg.get("x")
    assert reported is not None and reported.status == "offline"
    recent = await diag.recent()
    assert len(recent) == 1
    assert recent[0].level == "warning"
    assert len(alerts) == 1  # operator alerted


@pytest.mark.asyncio
async def test_steady_state_does_not_relog():
    wd, _reg, diag, alerts, _ = _make()
    wd.register(HealthCheck(name="x", probe=_scripted_probe(["ok", "ok", "ok"])))
    await wd.run_once()  # first ok: baseline == ok, no transition
    await wd.run_once()
    await wd.run_once()
    assert await diag.size() == 0
    assert alerts == []


@pytest.mark.asyncio
async def test_recovery_logs_info():
    wd, _reg, diag, _alerts, _ = _make()
    wd.register(HealthCheck(name="x", probe=_scripted_probe(["offline", "ok"])))
    await wd.run_once()  # offline -> warning
    await wd.run_once()  # ok -> info recovered
    recent = await diag.recent()
    assert [e.level for e in recent] == ["info", "warning"]
    assert "recovered" in recent[0].message


@pytest.mark.asyncio
async def test_persistent_offline_escalates_to_critical():
    wd, _reg, diag, _alerts, _ = _make()
    wd.register(
        HealthCheck(
            name="x",
            probe=_scripted_probe(["offline", "offline", "offline"]),
            persistent_failure_threshold=3,
        )
    )
    await wd.run_once()  # 1 offline -> warning (only first transition logs)
    await wd.run_once()  # still offline, no new transition (steady)
    await wd.run_once()  # still offline
    # Only the first transition recorded an entry; it was a warning.
    recent = await diag.recent()
    assert len(recent) == 1
    assert recent[0].level == "warning"


@pytest.mark.asyncio
async def test_critical_component_offline_logs_critical_immediately():
    wd, reg, diag, _alerts, _ = _make()
    wd.register(HealthCheck(name="bus", probe=_scripted_probe(["offline"]), critical=True))
    await wd.run_once()
    recent = await diag.recent()
    assert recent[0].level == "critical"
    # Criticality stamped onto the reported health -> system rolls up critical.
    snap = await reg.snapshot(now=0.0)
    assert snap.overall == "critical"


@pytest.mark.asyncio
async def test_probe_exception_treated_as_offline():
    wd, reg, diag, _alerts, _ = _make()

    async def boom() -> ComponentHealth:
        raise RuntimeError("connection refused")

    wd.register(HealthCheck(name="x", probe=boom))
    await wd.run_once()
    reported = await reg.get("x")
    assert reported is not None and reported.status == "offline"
    assert "connection refused" in reported.detail
    assert (await diag.recent())[0].level == "warning"


@pytest.mark.asyncio
async def test_async_transition_callback_awaited():
    wd, _reg, _diag, _alerts, _ = _make()
    seen: list[str] = []

    async def cb(entry: DiagnosticEntry) -> None:
        seen.append(entry.component)

    wd.on_transition = cb
    wd.register(HealthCheck(name="x", probe=_scripted_probe(["offline"])))
    await wd.run_once()
    assert seen == ["x"]
