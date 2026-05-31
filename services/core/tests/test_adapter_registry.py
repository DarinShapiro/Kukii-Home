"""Tests for the adapter registry + bootstrap."""

from __future__ import annotations

import os

import pytest
from kukiihome_adapter_rtsp_direct import CameraConfig, RTSPDirectAdapter
from kukiihome_core.adapter_registry import AdapterRegistry, bootstrap_from_env
from kukiihome_shared.adapter import PreprocessingMode

pytestmark = pytest.mark.asyncio


async def test_register_and_discover_cameras() -> None:
    reg = AdapterRegistry()
    adapter = RTSPDirectAdapter(
        cameras=[
            CameraConfig(camera_id="front", rtsp_url="rtsp://x"),
            CameraConfig(camera_id="back", rtsp_url="rtsp://y"),
        ]
    )
    reg.register(adapter)
    await reg.discover_all()

    assert set(reg.cameras) == {"front", "back"}
    assert reg.adapter_for("front") is adapter
    assert reg.adapter_for("nonexistent") is None


async def test_capability_cached_after_discovery() -> None:
    reg = AdapterRegistry()
    reg.register(RTSPDirectAdapter(cameras=[CameraConfig(camera_id="c1", rtsp_url="rtsp://x")]))
    await reg.discover_all()
    cap = reg.capability_for("c1")
    assert cap is not None
    assert cap.preprocessing_mode == PreprocessingMode.DIRECT


async def test_mode_summary_counts_cameras_by_mode() -> None:
    reg = AdapterRegistry()
    reg.register(
        RTSPDirectAdapter(
            cameras=[
                CameraConfig(camera_id="c1", rtsp_url="rtsp://x"),
                CameraConfig(camera_id="c2", rtsp_url="rtsp://y"),
            ]
        )
    )
    await reg.discover_all()
    assert reg.mode_summary() == {"direct": 2}


async def test_camera_conflict_first_wins() -> None:
    reg = AdapterRegistry()
    a = RTSPDirectAdapter(cameras=[CameraConfig(camera_id="dup", rtsp_url="rtsp://a")])
    b = RTSPDirectAdapter(cameras=[CameraConfig(camera_id="dup", rtsp_url="rtsp://b")])
    reg.register(a)
    reg.register(b)
    await reg.discover_all()
    assert reg.adapter_for("dup") is a


def test_bootstrap_returns_empty_when_no_env_set(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Clear any env vars
    for key in list(os.environ):
        if key.startswith("KUKIIHOME_ADAPTER_"):
            monkeypatch.delenv(key, raising=False)
    reg = bootstrap_from_env()
    assert reg.adapters == []


def test_bootstrap_picks_up_rtsp_direct_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json

    config = json.dumps([{"camera_id": "envcam", "rtsp_url": "rtsp://env.example/main"}])
    monkeypatch.setenv("KUKIIHOME_ADAPTER_RTSP_DIRECT_CONFIG", config)
    reg = bootstrap_from_env()
    assert len(reg.adapters) == 1
    assert reg.adapters[0].name == "adapter-rtsp-direct"


def test_bootstrap_picks_up_agent_dvr_url(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KUKIIHOME_ADAPTER_AGENT_DVR_URL", "http://localhost:8090")
    reg = bootstrap_from_env()
    assert any(a.name == "adapter-agent-dvr" for a in reg.adapters)


def test_bootstrap_picks_up_frigate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KUKIIHOME_ADAPTER_FRIGATE_URL", "http://frigate.local:5000")
    reg = bootstrap_from_env()
    assert any(a.name == "adapter-frigate" for a in reg.adapters)
