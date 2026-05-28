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
    camera_name: str = "DahuaPoolCam Main",
    camera_entity: str = "camera.dahuapoolcam_sub",
    sensor_classification: str = "person",
    recorded_at: str = "2026-05-27T14:23:01+00:00",
    evidence_ref: str | None = "/data/sentihome/snapshots/x.jpg",
    area: str = "",
    source: str = "ha_camera_event",
) -> dict:
    return {
        "alert_id": alert_id,
        "headline": headline,
        "camera_id": camera_id,
        "camera_name": camera_name,
        "camera_entity": camera_entity,
        "sensor_classification": sensor_classification,
        "recorded_at": recorded_at,
        "evidence_ref": evidence_ref,
        "area": area,
        "source": source,
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
    # Epic 10.8.6: tap URL = the /app/<slug> panel route (in-app,
    # authenticated). Needs panel_url_base set.
    n, _ = _make_notifier([], panel_url_base="/app/a58a7de9_sentihome")
    title, message, data = n._render(_alert())
    assert title == "Person at Pool Cam"
    # Message uses friendly camera name, not the slug.
    assert "DahuaPoolCam Main" in message or "Person detected" in message
    assert "14:23:01" in message
    # Image is HA's own camera_proxy — auth via mobile session.
    assert data["image"] == "/api/camera_proxy/camera.dahuapoolcam_sub"
    # Per-camera dedup tag is always present.
    assert data["tag"] == "sentihome_dahuapoolcam"
    # Epic 10.8.6: tap URL is the frontend panel route + alert hash.
    # No /api/ path (those open an external browser → 401), no
    # ingress token (browser-session-bound → 401).
    assert data["url"] == "/app/a58a7de9_sentihome#alert=abc123"
    assert data["clickAction"] == "/app/a58a7de9_sentihome#alert=abc123"
    assert "/api/" not in data["url"]


def test_render_tap_url_is_panel_route_not_ingress():
    """Epic 10.8.6: tap URL uses panel_url_base, never the ingress
    token prefix (the v0.3.15-27 failure mode)."""
    n, _ = _make_notifier(
        ["notify.mobile_app_x"],
        sentihome_ingress_base="/api/hassio_ingress/TOKEN123/",
        panel_url_base="/app/a58a7de9_sentihome",
    )
    _, _, data = n._render(_alert())
    assert data["url"].startswith("/app/a58a7de9_sentihome")
    assert "hassio_ingress" not in data["url"]


def test_render_omits_url_when_no_panel_base():
    """No Supervisor / dev: panel_url_base empty → omit the tap URL
    rather than emit a broken one. Notification still delivers."""
    n, _ = _make_notifier([])  # panel_url_base defaults to ""
    _, _, data = n._render(_alert())
    assert "url" not in data
    assert "clickAction" not in data


def test_render_tap_url_without_alert_id_is_bare_panel():
    """A synthetic ping with no alert_id still gets a tappable URL —
    just the panel root, no #alert hash."""
    n, _ = _make_notifier([], panel_url_base="/app/x_sentihome")
    alert = _alert()
    alert.pop("alert_id")
    _, _, data = n._render(alert)
    assert data["url"] == "/app/x_sentihome"
    assert "#alert" not in data["url"]


def test_render_omits_image_when_no_camera_entity():
    """Synthetic test alerts (camera_entity = '') get no image
    attachment — there's no HA entity to fetch a current frame from."""
    n, _ = _make_notifier([])
    _, _, data = n._render(_alert(camera_entity=""))
    assert "image" not in data
    # Tag is always present so the notification has data fields.
    assert "tag" in data


def test_render_marks_high_priority_for_fast_delivery():
    """v0.3.18 — alerts are time-sensitive. Flag them so APNs/FCM
    don't defer delivery in low-power / Focus mode."""
    n, _ = _make_notifier([])
    _, _, data = n._render(_alert())
    # Android / FCM
    assert data["priority"] == "high"
    # iOS APNs headers
    assert data["apns_headers"]["apns-priority"] == "10"
    assert data["apns_headers"]["apns-push-type"] == "alert"
    # iOS 15+ bypass Focus modes
    assert data["push"]["interruption-level"] == "time-sensitive"


def test_render_handles_missing_timestamp():
    n, _ = _make_notifier([])
    title, message, _data = n._render(_alert(recorded_at=""))
    assert title == "Person at Pool Cam"
    # Message still has classification + camera even without a time.
    assert "Person" in message
    assert "DahuaPoolCam Main" in message


def test_render_falls_back_to_motion_when_no_classification():
    n, _ = _make_notifier([])
    _title, message, _data = n._render(_alert(headline="Motion at Pool", sensor_classification=""))
    assert "Motion" in message  # capital-M default


def test_render_test_alerts_get_test_prefix():
    """Synthetic alerts from /notify/test or /discovery/test_alert
    should be visually distinct in the notification."""
    n, _ = _make_notifier([])
    title, _msg, _data = n._render(_alert(headline="Person at Pool Cam", source="notify_test"))
    assert title.startswith("[TEST]")


def test_render_message_includes_area_when_known():
    n, _ = _make_notifier([])
    _t, message, _d = n._render(_alert(area="Backyard"))
    assert "Backyard" in message


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


async def test_on_alert_payload_includes_image_when_camera_entity_present():
    """v0.3.17: when the alert carries a camera_entity, the image URL
    is HA's /api/camera_proxy/ path which the mobile Companion can
    fetch with its existing session auth."""
    n, mock = _make_notifier(["notify.mobile_app_x"])
    n.on_alert(_alert(camera_entity="camera.front_south_camera_fluent"))
    await _drain(n)
    call = mock.call_service.call_args
    body = call.kwargs["data"]
    assert "data" in body
    assert body["data"]["image"] == "/api/camera_proxy/camera.front_south_camera_fluent"


# ─── AlertLog integration ────────────────────────────────────────────


async def test_alert_log_callback_fires_on_record():
    """AlertLog.add_on_record + AlertLog.record = notifier fires."""
    log = AlertLog()
    n, mock = _make_notifier(["notify.x"])
    log.add_on_record(n.on_alert)
    log.record(_alert(alert_id="zzz"))
    await _drain(n)
    assert mock.call_service.call_count == 1


# ─── set_services (v0.3.13 live reconfiguration) ─────────────────────


async def test_set_services_swaps_services_at_runtime():
    """User unchecks 'mobile_app' in the UI → no more sends to it."""
    n, mock = _make_notifier(["notify.mobile_app_x"])
    n.set_services(["notify.alexa"])
    n.on_alert(_alert())
    await _drain(n)
    assert mock.call_service.call_count == 1
    domain, svc = mock.call_service.call_args.args[:2]
    assert (domain, svc) == ("notify", "alexa")


async def test_set_services_empty_disables_notifications():
    n, mock = _make_notifier(["notify.x"])
    n.set_services([])
    n.on_alert(_alert())
    await _drain(n)
    mock.call_service.assert_not_called()


# ─── test_send (v0.3.14 diagnostic) ──────────────────────────────────


async def test_test_send_returns_ok_for_each_success():
    n, _ = _make_notifier(["notify.a", "notify.b"])
    results = await n.test_send(_alert())
    assert len(results) == 2
    assert all(r["ok"] for r in results)
    assert all(r["error"] is None for r in results)
    assert {r["service"] for r in results} == {"notify.a", "notify.b"}


async def test_test_send_captures_per_service_failure():
    n, mock = _make_notifier(["notify.broken", "notify.ok"])
    mock.call_service.side_effect = [RuntimeError("Service not found"), None]
    results = await n.test_send(_alert())
    by_svc = {r["service"]: r for r in results}
    assert by_svc["notify.broken"]["ok"] is False
    assert "Service not found" in by_svc["notify.broken"]["error"]
    assert by_svc["notify.ok"]["ok"] is True
    assert by_svc["notify.ok"]["error"] is None


async def test_test_send_with_no_services_returns_empty():
    n, _ = _make_notifier([])
    results = await n.test_send(_alert())
    assert results == []


# ─── Epic 10.8.6: panel-route tap URL reaches HA ─────────────────────


async def test_dispatch_sends_panel_url_to_ha():
    """End-to-end: with panel_url_base set, the notify payload that
    reaches HA carries the /app/<slug> tap URL — no signing, no
    /api/ path, no async round-trip."""
    n, client = _make_notifier(
        ["notify.mobile_app_x"], panel_url_base="/app/a58a7de9_sentihome"
    )
    results = await n.test_send(_alert())
    assert results == [{"service": "notify.mobile_app_x", "ok": True, "error": None}]
    data = client.call_service.call_args.kwargs["data"]["data"]
    assert data["url"] == "/app/a58a7de9_sentihome#alert=abc123"
    assert "/api/" not in data["url"]
