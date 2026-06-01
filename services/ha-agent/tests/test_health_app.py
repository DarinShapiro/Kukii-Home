"""Tests for the ha-agent health service wiring + /health, /diagnostics."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from kukiihome_ha_agent.health_app import (
    attach_health_routes,
    build_health_service,
    diagnostics_payload,
    health_payload,
)
from kukiihome_shared.health import (
    ComponentHealth,
    DiagnosticEntry,
    FailureMode,
    HealthSnapshot,
)


def test_build_registers_f4_ha_check():
    svc = build_health_service(is_connected=lambda: True, clock=lambda: 0.0)
    names = [c.name for c in svc._checks]
    assert "home_assistant" in names
    f4 = next(c for c in svc._checks if c.name == "home_assistant")
    assert f4.failure_mode == FailureMode.F4_HA_DOWN
    assert f4.critical is True


def test_extra_checks_registered():
    from kukiihome_shared.health import HealthCheck

    async def probe() -> ComponentHealth:
        return ComponentHealth(component="preprocessor", status="ok", updated_ts=0.0)

    svc = build_health_service(
        is_connected=lambda: True,
        extra_checks=[HealthCheck(name="preprocessor", probe=probe)],
        clock=lambda: 0.0,
    )
    assert "preprocessor" in [c.name for c in svc._checks]


@pytest.mark.asyncio
async def test_poll_once_reports_ok_when_connected():
    svc = build_health_service(is_connected=lambda: True, clock=lambda: 5.0)
    await svc.poll_once()
    snap = await svc.registry.snapshot(now=5.0)
    assert snap.overall == "healthy"
    ha = next(c for c in snap.components if c.component == "home_assistant")
    assert ha.status == "ok"


@pytest.mark.asyncio
async def test_poll_once_ha_down_activates_f4_and_rolls_critical():
    state = {"up": False}
    svc = build_health_service(is_connected=lambda: state["up"], clock=lambda: 1.0)
    await svc.poll_once()
    assert svc.degraded_state.is_active(FailureMode.F4_HA_DOWN)
    snap = await svc.registry.snapshot(now=1.0)
    assert snap.overall == "critical"  # HA is critical-class
    # recovery clears it
    state["up"] = True
    await svc.poll_once()
    assert not svc.degraded_state.is_active(FailureMode.F4_HA_DOWN)
    snap = await svc.registry.snapshot(now=2.0)
    assert snap.overall == "healthy"


# ─── payload shape ──────────────────────────────────────────────────


def test_health_payload_shape():
    snap = HealthSnapshot(
        overall="degraded",
        components=(
            ComponentHealth(
                component="home_assistant",
                status="offline",
                detail="ws down",
                critical=True,
                updated_ts=3.0,
            ),
        ),
        generated_ts=9.0,
    )
    p = health_payload(snap)
    assert p["overall"] == "degraded"
    assert p["generated_ts"] == 9.0
    assert p["components"][0]["component"] == "home_assistant"
    assert p["components"][0]["critical"] is True


def test_diagnostics_payload_shape():
    entries = [
        DiagnosticEntry(ts=1.0, level="warning", component="home_assistant", message="down"),
    ]
    p = diagnostics_payload(entries)
    assert p["count"] == 1
    assert p["entries"][0]["level"] == "warning"


# ─── aiohttp routes (no server; call handlers via the app router) ────


@pytest.mark.asyncio
async def test_routes_serve_health_and_diagnostics():
    svc = build_health_service(is_connected=lambda: False, clock=lambda: 7.0)
    await svc.poll_once()  # populate registry + diagnostics

    app = web.Application()
    attach_health_routes(app, svc)

    routes = {r.resource.canonical: r for r in app.router.routes()}
    assert "/health" in routes
    assert "/diagnostics" in routes

    class _Req:
        query: dict = {}  # noqa: RUF012 — test stub, single-use

    health_resp = await routes["/health"].handler(_Req())
    body = json.loads(health_resp.body)
    assert body["overall"] == "critical"
    assert body["components"][0]["component"] == "home_assistant"

    diag_resp = await routes["/diagnostics"].handler(_Req())
    diag = json.loads(diag_resp.body)
    assert diag["count"] >= 1
    assert diag["entries"][0]["component"] == "home_assistant"


@pytest.mark.asyncio
async def test_diagnostics_level_filter():
    svc = build_health_service(is_connected=lambda: False, clock=lambda: 0.0)
    await svc.poll_once()
    app = web.Application()
    attach_health_routes(app, svc)
    routes = {r.resource.canonical: r for r in app.router.routes()}

    class _Req:
        query = {"level": "critical"}  # noqa: RUF012 — test stub, single-use

    resp = await routes["/diagnostics"].handler(_Req())
    diag = json.loads(resp.body)
    # The F4 offline on a critical component logs at critical level.
    assert all(e["level"] == "critical" for e in diag["entries"])
