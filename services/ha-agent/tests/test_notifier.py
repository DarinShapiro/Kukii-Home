"""Tests for the AlertNotifier (alerts → HA notify.* services)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from sentihome_ha_agent.http_api import AlertLog
from sentihome_ha_agent.notifier import AlertNotifier


def _alert(
    alert_id: str = "abc123",
    headline: str = "Person at Pool Cam",
    camera_id: str = "dahuapoolcam",
    sensor_classification: str = "person",
    recorded_at: str = "2026-05-27T14:23:01+00:00",
    evidence_ref: str | None = "/data/sentihome/snapshots/x.jpg",
    area: str = "",
) -> dict:
    return {
        "alert_id": alert_id,
        "headline": headline,
        "camera_id": camera_id,
        "sensor_classification": sensor_classification,
        "recorded_at": recorded_at,
        "evidence_ref": evidence_ref,
        "area": area,
    }


def _make_notifier(services: list[str], **kw) -> tuple[AlertNotifier, AsyncMock]:
    mock_client = AsyncMock()
    return AlertNotifier(client=mock_client, notify_services=services, **kw), mock_client


async def _drain(n: AlertNotifier) -> None:
    """Await all in-flight notify tasks.

    on_alert is fire-and-forget; tests need to wait for the spawned
    asyncio.Tasks to complete before asserting on the mock client.
    Using ``gather`` over a busy-wait keeps lint happy and is faster.
    """
    if n._pending_tasks:
        await asyncio.gather(*list(n._pending_tasks), return_exceptions=True)


# ─── render: payload shaping ─────────────────────────────────────────


def test_render_basic_alert():
    n, _ = _make_notifier([])
    title, message, data = n._render(_alert())
    assert title == "Person at Pool Cam"
    assert "Person" in message
    assert "dahuapoolcam" in message
    assert "14:23:01" in message
    assert data["url"] == "/"
    assert data["image"] == "/alerts/abc123/snapshot"


def test_render_uses_ingress_base_when_set():
    n, _ = _make_notifier(
        ["notify.mobile_app_x"],
        sentihome_ingress_base="/api/hassio_ingress/TOKEN123",
    )
    _, _, data = n._render(_alert())
    assert data["url"] == "/api/hassio_ingress/TOKEN123/"
    assert data["image"] == "/api/hassio_ingress/TOKEN123/alerts/abc123/snapshot"


def test_render_omits_image_when_no_snapshot():
    """No evidence_ref means we never captured a snapshot — don't
    pass a broken URL to the HA app."""
    n, _ = _make_notifier([])
    _, _, data = n._render(_alert(evidence_ref=None))
    assert "image" not in data
    assert "url" in data


def test_render_handles_missing_timestamp():
    n, _ = _make_notifier([])
    title, message, _data = n._render(_alert(recorded_at=""))
    assert title == "Person at Pool Cam"
    # Message still has classification + camera even without a time.
    assert "Person" in message
    assert "dahuapoolcam" in message


def test_render_falls_back_to_motion_when_no_classification():
    n, _ = _make_notifier([])
    _title, message, _data = n._render(_alert(headline="Motion at Pool", sensor_classification=""))
    assert "Motion" in message  # capital-M default


# ─── on_alert: fire-and-forget dispatch ──────────────────────────────


async def test_on_alert_calls_each_service():
    n, mock = _make_notifier(["notify.mobile_app_pixel_8", "notify.alexa_media_kitchen"])
    n.on_alert(_alert())
    # on_alert schedules a task; let the event loop run it.
    await _drain(n)
    assert mock.call_service.call_count == 2
    # Both services received the same title.
    calls = mock.call_service.call_args_list
    services_called = {(c.args[0], c.args[1]) for c in calls}
    assert services_called == {
        ("notify", "mobile_app_pixel_8"),
        ("notify", "alexa_media_kitchen"),
    }


async def test_on_alert_noop_when_no_services():
    """Notifier installed with empty services list = silent."""
    n, mock = _make_notifier([])
    n.on_alert(_alert())
    # No task created when there's nothing to dispatch.
    assert len(n._pending_tasks) == 0
    mock.call_service.assert_not_called()


async def test_on_alert_continues_when_one_service_fails():
    """If notify.mobile_app_X raises, notify.alexa_media should still go."""
    n, mock = _make_notifier(["notify.broken", "notify.working"])
    # First call raises, second succeeds.
    mock.call_service.side_effect = [Exception("boom"), None]
    n.on_alert(_alert())
    await _drain(n)
    # Both services attempted despite the first one raising.
    assert mock.call_service.call_count == 2


async def test_on_alert_rejects_malformed_service_string():
    n, mock = _make_notifier(["mobile_app_pixel_8"])  # missing dot
    n.on_alert(_alert())
    await _drain(n)
    # The service rejected with ValueError — mock was never called.
    mock.call_service.assert_not_called()


async def test_on_alert_payload_includes_image_when_snapshot_exists():
    n, mock = _make_notifier(["notify.mobile_app_x"])
    n.on_alert(_alert())
    await _drain(n)
    call = mock.call_service.call_args
    body = call.kwargs["data"]
    assert "data" in body
    assert "image" in body["data"]


# ─── AlertLog integration ────────────────────────────────────────────


async def test_alert_log_callback_fires_on_record():
    """AlertLog.add_on_record + AlertLog.record = notifier fires."""
    log = AlertLog()
    n, mock = _make_notifier(["notify.x"])
    log.add_on_record(n.on_alert)
    log.record(_alert(alert_id="zzz"))
    await _drain(n)
    assert mock.call_service.call_count == 1
