"""Tests for the ha-agent client + tools + area resolver + HTTP API."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from sentihome_ha_agent import (
    AlertLog,
    AreaRegistry,
    HAAgentAPI,
    HAClient,
    HAClientSettings,
    HAState,
    HATools,
    make_ha_caller,
)

# ─────────────────────────────────────────────────────────────────────
# httpx mock helpers
# ─────────────────────────────────────────────────────────────────────


def _mock_transport(routes: dict[tuple[str, str], httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in routes:
            return routes[key]
        return httpx.Response(404, json={"error": f"no mock for {key}"})

    return httpx.MockTransport(handler)


def _client_with_mocks(routes: dict[tuple[str, str], httpx.Response]) -> HAClient:
    http = httpx.AsyncClient(
        transport=_mock_transport(routes),
        base_url="http://supervisor",
        headers={"Authorization": "Bearer tok"},
    )
    settings = HAClientSettings(ha_url="http://supervisor", ha_token="tok", websocket=False)
    # ws_connector unused because websocket=False.
    return HAClient(settings, http_client=http)


# ─────────────────────────────────────────────────────────────────────
# HAClient REST
# ─────────────────────────────────────────────────────────────────────


async def test_client_get_states_populates_cache():
    routes = {
        ("GET", "/api/states"): httpx.Response(
            200,
            json=[
                {"entity_id": "light.porch", "state": "off", "attributes": {}},
                {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
            ],
        )
    }
    client = _client_with_mocks(routes)
    states = await client.get_states()
    assert {s.entity_id for s in states} == {"light.porch", "light.kitchen"}
    # Second call uses cache (no extra REST hit, would 404 since route consumed once).
    cached = await client.get_states()
    assert cached == states


async def test_client_get_state_returns_none_on_404():
    routes = {("GET", "/api/states/nope.entity"): httpx.Response(404)}
    client = _client_with_mocks(routes)
    assert await client.get_state("nope.entity") is None


async def test_client_call_service_raises_on_4xx():
    routes = {("POST", "/api/services/light/turn_on"): httpx.Response(400, json={"message": "bad"})}
    client = _client_with_mocks(routes)
    from sentihome_ha_agent import HAClientError

    with pytest.raises(HAClientError):
        await client.call_service("light", "turn_on", entity_id="light.porch")


async def test_client_call_service_returns_body():
    routes = {("POST", "/api/services/light/turn_on"): httpx.Response(200, json={"ok": True})}
    client = _client_with_mocks(routes)
    result = await client.call_service("light", "turn_on", entity_id="light.porch")
    assert result == {"ok": True}


# ─────────────────────────────────────────────────────────────────────
# AreaRegistry
# ─────────────────────────────────────────────────────────────────────


def test_area_registry_explicit_override_wins():
    reg = AreaRegistry(explicit_overrides={"front_door": {"light": ["light.porch_explicit"]}})
    states = [
        HAState(
            entity_id="light.porch_explicit",
            state="off",
            attributes={"area_id": "front_door"},
        ),
        HAState(entity_id="light.kitchen", state="on", attributes={"area_id": "kitchen"}),
    ]
    res = reg.resolve("front_door", states)
    assert res.get("light") == ["light.porch_explicit"]


def test_area_registry_resolves_via_ha_area_id_attribute():
    reg = AreaRegistry()
    states = [
        HAState(entity_id="light.porch", state="off", attributes={"area_id": "front_door"}),
        HAState(entity_id="lock.front", state="locked", attributes={"area_id": "front_door"}),
        HAState(entity_id="light.kitchen", state="on", attributes={"area_id": "kitchen"}),
    ]
    res = reg.resolve("front_door", states)
    assert "light.porch" in res.get("light")
    assert "lock.front" in res.get("lock")
    assert "light.kitchen" not in res.get("light")


def test_area_registry_heuristic_substring_fallback():
    reg = AreaRegistry()
    # No area_id attribute; the resolver should fall back to entity-id matching.
    states = [
        HAState(entity_id="light.front_door_porch", state="off"),
        HAState(entity_id="light.kitchen_ceiling", state="on"),
    ]
    res = reg.resolve("front_door", states)
    assert "light.front_door_porch" in res.get("light")
    assert "light.kitchen_ceiling" not in res.get("light")


# ─────────────────────────────────────────────────────────────────────
# HATools
# ─────────────────────────────────────────────────────────────────────


async def test_tools_illuminate_area_calls_lights_in_area():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/states":
            return httpx.Response(
                200,
                json=[
                    {
                        "entity_id": "light.front_door_porch",
                        "state": "off",
                        "attributes": {"area_id": "front_door"},
                    },
                    {
                        "entity_id": "light.kitchen",
                        "state": "off",
                        "attributes": {"area_id": "kitchen"},
                    },
                ],
            )
        if request.method == "POST" and request.url.path == "/api/services/light/turn_on":
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x")
    settings = HAClientSettings(ha_url="http://x", ha_token="t", websocket=False)
    client = HAClient(settings, http_client=http)
    tools = HATools(client)

    result = await tools.illuminate_area("front_door", brightness=200)
    assert result["called"] == ["light.front_door_porch"]
    assert captured["payload"]["entity_id"] == ["light.front_door_porch"]
    assert captured["payload"]["brightness"] == 200


async def test_tools_illuminate_area_no_lights_is_skipped():
    routes = {("GET", "/api/states"): httpx.Response(200, json=[])}
    client = _client_with_mocks(routes)
    tools = HATools(client)
    result = await tools.illuminate_area("nowhere")
    assert result == {"called": [], "skipped_reason": "no_lights_in_area"}


async def test_tools_list_capabilities_buckets_by_domain():
    routes = {
        ("GET", "/api/states"): httpx.Response(
            200,
            json=[
                {"entity_id": "light.a", "state": "on"},
                {"entity_id": "light.b", "state": "off"},
                {"entity_id": "lock.front", "state": "locked"},
                {"entity_id": "sensor.cpu", "state": "30"},  # not in CAPABILITY_DOMAINS
            ],
        )
    }
    client = _client_with_mocks(routes)
    tools = HATools(client)
    caps = await tools.list_capabilities()
    by_domain = {c.domain: c for c in caps}
    assert by_domain["light"].entity_count == 2
    assert by_domain["lock"].entity_count == 1
    assert "sensor" not in by_domain


async def test_tools_list_ha_cameras_matches_motion_sensors_by_substring():
    """v0.3.0: HATools.list_ha_cameras pairs each camera.* with its
    binary_sensor.* siblings whose ids contain motion keywords."""
    routes = {
        ("GET", "/api/states"): httpx.Response(
            200,
            json=[
                {
                    "entity_id": "camera.pool_cam",
                    "state": "idle",
                    "attributes": {"friendly_name": "Dahua Pool Cam"},
                },
                {
                    "entity_id": "binary_sensor.pool_cam_motion",
                    "state": "off",
                    "attributes": {},
                },
                {
                    "entity_id": "binary_sensor.pool_cam_person",
                    "state": "off",
                    "attributes": {},
                },
                {
                    "entity_id": "binary_sensor.pool_cam_zone_1",  # NOT motion keyword
                    "state": "off",
                    "attributes": {},
                },
                {
                    "entity_id": "camera.front_yard_south",
                    "state": "idle",
                    "attributes": {"friendly_name": "Front Yard South"},
                },
                {
                    "entity_id": "binary_sensor.front_yard_south_motion",
                    "state": "off",
                    "attributes": {},
                },
            ],
        )
    }
    client = _client_with_mocks(routes)
    tools = HATools(client)
    cams = await tools.list_ha_cameras()
    by_entity = {c.camera_entity: c for c in cams}
    assert set(by_entity) == {"camera.pool_cam", "camera.front_yard_south"}
    assert by_entity["camera.pool_cam"].friendly_name == "Dahua Pool Cam"
    assert set(by_entity["camera.pool_cam"].motion_candidates) == {
        "binary_sensor.pool_cam_motion",
        "binary_sensor.pool_cam_person",
    }
    # Not matched: pool_cam_zone_1 lacks a motion keyword
    assert "binary_sensor.pool_cam_zone_1" not in by_entity["camera.pool_cam"].motion_candidates
    # Front Yard South should not have the Pool Cam sensors leaking in
    assert by_entity["camera.front_yard_south"].motion_candidates == [
        "binary_sensor.front_yard_south_motion"
    ]


async def test_tools_list_ha_cameras_empty_when_no_cameras():
    routes = {
        ("GET", "/api/states"): httpx.Response(
            200, json=[{"entity_id": "light.kitchen", "state": "on"}]
        )
    }
    client = _client_with_mocks(routes)
    tools = HATools(client)
    cams = await tools.list_ha_cameras()
    assert cams == []


def test_topology_accepts_ha_camera_adapter_kind():
    """v0.3.0: AdapterConfig now accepts kind='ha-camera' with
    camera_entity + motion_entities + snapshot_cooldown_seconds."""
    from sentihome_shared.topology import AdapterConfig, Topology

    cfg = AdapterConfig(
        name="pool-cam",
        kind="ha-camera",
        camera_entity="camera.pool_cam",
        motion_entities=[
            "binary_sensor.pool_cam_motion",
            "binary_sensor.pool_cam_person",
        ],
        snapshot_cooldown_seconds=15.0,
    )
    assert cfg.kind == "ha-camera"
    assert cfg.camera_entity == "camera.pool_cam"
    assert len(cfg.motion_entities) == 2
    assert cfg.snapshot_cooldown_seconds == 15.0
    # And confirm Topology.adapters accepts it
    t = Topology(adapters=[cfg])
    assert t.adapters[0].kind == "ha-camera"


async def test_tools_get_changes_filters_by_timestamp():
    routes = {
        ("GET", "/api/states"): httpx.Response(
            200,
            json=[
                {
                    "entity_id": "light.a",
                    "state": "on",
                    "last_changed": "2026-05-25T12:00:00+00:00",
                },
                {
                    "entity_id": "light.b",
                    "state": "off",
                    "last_changed": "2026-05-26T08:00:00+00:00",
                },
            ],
        )
    }
    client = _client_with_mocks(routes)
    tools = HATools(client)
    changed = await tools.get_changes(datetime.fromisoformat("2026-05-26T00:00:00+00:00"))
    assert [c.entity_id for c in changed] == ["light.b"]


async def test_tools_lock_unlock_invoke_correct_services():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.startswith("/api/services/lock/"):
            captured.append(request.url.path)
            return httpx.Response(200, json={})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x")
    settings = HAClientSettings(ha_url="http://x", ha_token="t", websocket=False)
    client = HAClient(settings, http_client=http)
    tools = HATools(client)
    await tools.lock("lock.front")
    await tools.unlock("lock.back")
    assert captured == ["/api/services/lock/lock", "/api/services/lock/unlock"]


# ─────────────────────────────────────────────────────────────────────
# HTTP API (in-process dispatch)
# ─────────────────────────────────────────────────────────────────────


async def test_api_healthz():
    api = HAAgentAPI(tools=None, alert_log=AlertLog())
    status, body = await api.dispatch(method="GET", path="/healthz")
    assert status == 200 and body == {"ok": True}


async def test_api_snapshot_returns_entities():
    routes = {
        ("GET", "/api/states"): httpx.Response(
            200, json=[{"entity_id": "light.a", "state": "on", "attributes": {}}]
        )
    }
    tools = HATools(_client_with_mocks(routes))
    api = HAAgentAPI(tools=tools, alert_log=AlertLog())
    status, body = await api.dispatch(method="GET", path="/snapshot")
    assert status == 200
    assert body["entities"][0]["entity_id"] == "light.a"


async def test_api_service_call_requires_domain_and_service():
    api = HAAgentAPI(tools=None, alert_log=AlertLog())
    status, body = await api.dispatch(method="POST", path="/service", body={})
    assert status == 400 and "error" in body


async def test_api_service_call_dispatches_to_tools():
    called: dict[str, Any] = {}

    class FakeTools:
        async def call_service(self, domain, service, *, entity_id=None, data=None):
            called["args"] = (domain, service, entity_id, data)
            return {"ok": True}

    api = HAAgentAPI(tools=FakeTools(), alert_log=AlertLog())
    status, body = await api.dispatch(
        method="POST",
        path="/service",
        body={
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.x",
            "data": {"brightness": 100},
        },
    )
    assert status == 200 and body["ok"] is True
    assert called["args"] == ("light", "turn_on", "light.x", {"brightness": 100})


async def test_api_acknowledge_alert_marks_record():
    log = AlertLog()
    log.record({"alert_id": "a1", "headline": "test"})
    api = HAAgentAPI(tools=None, alert_log=log)
    status, _ = await api.dispatch(
        method="POST", path="/acknowledge_alert", body={"alert_id": "a1", "feedback": "correct"}
    )
    assert status == 200
    assert log.recent(10)[0]["acknowledged"] is True
    assert log.recent(10)[0]["feedback"] == "correct"


async def test_api_unknown_route_404s():
    api = HAAgentAPI(tools=None, alert_log=AlertLog())
    status, body = await api.dispatch(method="GET", path="/nope")
    assert status == 404 and "error" in body


def test_alert_log_caps_entries():
    log = AlertLog(max_entries=3)
    for i in range(10):
        log.record({"alert_id": f"a{i}"})
    recent = log.recent(10)
    assert [e["alert_id"] for e in recent] == ["a7", "a8", "a9"]


# ─────────────────────────────────────────────────────────────────────
# make_ha_caller (notify-dispatcher integration seam)
# ─────────────────────────────────────────────────────────────────────


async def test_make_ha_caller_splits_domain_service():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content) if request.content else {}
        return httpx.Response(200, json={"ok": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x")
    settings = HAClientSettings(ha_url="http://x", ha_token="t", websocket=False)
    client = HAClient(settings, http_client=http)
    caller = make_ha_caller(client)
    result = await caller("notify.mobile_app_pixel", {"message": "hi"})
    assert result == {"ok": True}
    assert captured["path"] == "/api/services/notify/mobile_app_pixel"
    assert captured["body"]["message"] == "hi"


async def test_make_ha_caller_rejects_malformed_service():
    settings = HAClientSettings(ha_url="http://x", ha_token="t", websocket=False)
    client = HAClient(settings, http_client=httpx.AsyncClient(base_url="http://x"))
    caller = make_ha_caller(client)
    with pytest.raises(ValueError):
        await caller("notmobileapp", {})


# ─────────────────────────────────────────────────────────────────────
# Settings.from_topology
# ─────────────────────────────────────────────────────────────────────


def test_ha_agent_settings_requires_token():
    from sentihome_ha_agent import HAAgentSettings
    from sentihome_shared.topology import HAAgentConfig, Topology

    topo = Topology(ha_agent=HAAgentConfig(ha_url="http://x", ha_token=""))
    with pytest.raises(ValueError):
        HAAgentSettings.from_topology(topo)


def test_ha_agent_settings_from_topology_populates_fields():
    from sentihome_ha_agent import HAAgentSettings
    from sentihome_shared.topology import HAAgentConfig, Topology

    topo = Topology(ha_agent=HAAgentConfig(ha_url="http://h", ha_token="t", websocket=False))
    s = HAAgentSettings.from_topology(topo)
    assert s.ha_url == "http://h" and s.ha_token == "t" and s.websocket is False
