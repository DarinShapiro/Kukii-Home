"""Tests for FrigateAdapter (MQTT payload normalization, contract conformance)."""

from __future__ import annotations

import json

import pytest
from sentihome_adapter_frigate import FrigateAdapter, FrigateConfig
from sentihome_shared.adapter import PreprocessingMode


def test_frigate_adapter_identity() -> None:
    adapter = FrigateAdapter(FrigateConfig())
    assert adapter.name == "adapter-frigate"
    assert adapter.mode == PreprocessingMode.BUILT_IN


def test_frigate_mqtt_payload_to_motion_event() -> None:
    adapter = FrigateAdapter(FrigateConfig())
    payload = json.dumps(
        {
            "type": "new",
            "before": None,
            "after": {
                "camera": "front_door",
                "label": "person",
                "top_score": 0.87,
                "start_time": 1716657600,  # 2024-05-25 16:00:00 UTC
                "box": [100, 200, 300, 400],
            },
        }
    ).encode()
    event = adapter._handle_mqtt_payload(payload)
    assert event is not None
    assert event.camera_id == "front_door"
    assert event.event_type == "person"
    assert event.confidence == 0.87
    assert event.bbox == (100, 200, 300, 400)


def test_frigate_mqtt_ignores_update_type() -> None:
    adapter = FrigateAdapter(FrigateConfig())
    payload = json.dumps({"type": "update", "after": {"camera": "x"}}).encode()
    assert adapter._handle_mqtt_payload(payload) is None


def test_frigate_mqtt_handles_invalid_json() -> None:
    adapter = FrigateAdapter(FrigateConfig())
    assert adapter._handle_mqtt_payload(b"not json") is None


@pytest.mark.asyncio
async def test_frigate_requires_start_before_use() -> None:
    adapter = FrigateAdapter(FrigateConfig())
    with pytest.raises(RuntimeError, match="not started"):
        await adapter.list_cameras()
