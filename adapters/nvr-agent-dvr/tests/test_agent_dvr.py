"""Tests for the Agent DVR adapter (no real Agent DVR instance required)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from kukiihome_adapter_agent_dvr import AgentDVRConfig
from kukiihome_adapter_agent_dvr.adapter import AgentDVRAdapter, _to_capability
from kukiihome_adapter_agent_dvr.webhook import AgentDVRWebhookReceiver, _classify
from kukiihome_shared.adapter import PreprocessingMode

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────
# _to_capability mapping
# ─────────────────────────────────────────────────────────────────────


def test_to_capability_uses_id_when_present() -> None:
    cap = _to_capability({"id": 42, "name": "Front Door"})
    assert cap.camera_id == "42"
    assert cap.name == "Front Door"


def test_to_capability_falls_back_to_name() -> None:
    cap = _to_capability({"name": "Backyard"})
    assert cap.camera_id == "Backyard"


def test_to_capability_advertises_service_mode() -> None:
    cap = _to_capability({"id": 1})
    assert cap.preprocessing_mode == PreprocessingMode.SERVICE


def test_to_capability_reports_ptz() -> None:
    cap = _to_capability({"id": 1, "ptz": True})
    assert cap.ptz is True


def test_to_capability_defaults_no_ptz() -> None:
    cap = _to_capability({"id": 1})
    assert cap.ptz is False


# ─────────────────────────────────────────────────────────────────────
# Adapter identity / lifecycle
# ─────────────────────────────────────────────────────────────────────


def test_adapter_name_and_mode() -> None:
    adapter = AgentDVRAdapter(AgentDVRConfig())
    assert adapter.name == "adapter-agent-dvr"
    assert adapter.mode == PreprocessingMode.SERVICE


async def test_adapter_requires_start_before_use() -> None:
    adapter = AgentDVRAdapter(AgentDVRConfig())
    with pytest.raises(RuntimeError, match="not started"):
        await adapter.list_cameras()


async def test_get_frame_window_returns_metadata_stub() -> None:
    adapter = AgentDVRAdapter(AgentDVRConfig())
    window = await adapter.get_frame_window(
        "1",
        datetime(2026, 5, 25, tzinfo=UTC),
        datetime(2026, 5, 25, tzinfo=UTC),
    )
    assert window.metadata["preprocessing_mode"] == "service"
    assert "note" in window.metadata


# ─────────────────────────────────────────────────────────────────────
# Webhook receiver + classification
# ─────────────────────────────────────────────────────────────────────


def test_classify_person() -> None:
    assert _classify("Person detected at front door") == "person"


def test_classify_vehicle() -> None:
    assert _classify("Vehicle in driveway") == "vehicle"
    assert _classify("Car parked") == "vehicle"


def test_classify_animal() -> None:
    assert _classify("Dog in backyard") == "animal"


def test_classify_default_motion() -> None:
    assert _classify("Something moved") == "motion"


async def test_webhook_normalize_and_enqueue() -> None:
    receiver = AgentDVRWebhookReceiver()
    payload: dict[str, Any] = {
        "Type": "Alert",
        "ObjectId": 1,
        "Name": "Front Door",
        "Description": "Person detected at front door",
        "Time": "2026-05-25T14:30:22Z",
    }
    await receiver.handle_payload(payload)
    event = await receiver.queue.get()
    assert event.camera_id == "1"
    assert event.event_type == "person"
    assert event.timestamp.year == 2026


async def test_webhook_handles_missing_time() -> None:
    receiver = AgentDVRWebhookReceiver()
    await receiver.handle_payload({"ObjectId": 1, "Description": "Motion"})
    event = await receiver.queue.get()
    assert event.event_type == "motion"
    # Should use "now" — just verify it's set
    assert event.timestamp is not None
