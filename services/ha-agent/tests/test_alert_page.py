"""Tests for the per-alert page + dismiss/feedback routes.

End-to-end against the actual aiohttp app via aiohttp's TestClient.
Exercises the routes the notification tap UX hits — both the
happy-path HTML rendering and the structured-form submission paths.

The page renderers themselves are exercised here rather than as
isolated unit tests because the route handlers are thin and the
interesting behavior (404 vs render, form submission roundtrip,
dismiss reflected in the page) is at the request/response level.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from kukiihome_ha_agent.__main__ import BootState, _build_app
from kukiihome_ha_agent.event_store import EventStore
from kukiihome_ha_agent.http_api import AlertLog


def _alert(
    alert_id: str = "evt1",
    **extra,
) -> dict:
    base = {
        "alert_id": alert_id,
        "recorded_at": "2026-05-28T15:30:00+00:00",
        "camera_id": "front_porch",
        "camera_name": "Front Porch",
        "camera_entity": "camera.front_porch",
        "headline": "Person at Front Porch",
        "sensor_classification": "person",
        "identified_entities": [
            {
                "actor_name": "Alice",
                "identity_method": "face_arcface",
                "identity_confidence": 0.91,
            }
        ],
        "detections": [
            {"kind": "person", "confidence": 0.9},
            {"kind": "person", "confidence": 0.85},
        ],
    }
    base.update(extra)
    return base


@pytest.fixture
async def setup(tmp_path: Path):
    """Build an app instance backed by tmp_path's event store."""
    alert_log = AlertLog()
    event_store = EventStore(root=tmp_path / "events")
    alert_log.add_on_record(event_store.record_from_alert)
    boot = BootState()
    app = _build_app(boot=boot, alert_log=alert_log, event_store=event_store)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client, alert_log, event_store, tmp_path
    finally:
        await client.close()


# ─── in-panel deep-link reader (Epic 10.8.7) ────────────────────────


def test_status_page_has_deep_link_reader():
    """The status page (the ingress panel content) carries the JS that
    reads an #alert=<id> fragment and redirects the iframe to the
    per-alert detail page. This is the consumer half of the
    notification deep-link the notifier emits."""
    from kukiihome_ha_agent.__main__ import _STATUS_PAGE

    # Reads the alert id from a fragment...
    assert "alert=" in _STATUS_PAGE
    # ...checking the parent frame too (HA renders us in an iframe)...
    assert "window.top" in _STATUS_PAGE
    # ...and navigates to the detail page via a RELATIVE url so it
    # stays under the ingress prefix (no leading slash → no 401).
    assert 'replace("alert/"' in _STATUS_PAGE
    assert '"/alert/"' not in _STATUS_PAGE  # absolute would break ingress


# ─── GET /alert/<id> ────────────────────────────────────────────────


async def test_alert_page_404_when_unknown(setup):
    client, _, _, _ = setup
    resp = await client.get("/alert/nope")
    assert resp.status == 404
    text = await resp.text()
    assert "Alert not found" in text
    # 404 page links back to recent alerts.
    assert "recent alerts" in text.lower()


async def test_alert_page_renders_headline_and_camera(setup):
    client, alert_log, _, _ = setup
    alert_log.record(_alert())
    resp = await client.get("/alert/evt1")
    assert resp.status == 200
    text = await resp.text()
    assert "Person at Front Porch" in text
    assert "Front Porch" in text


async def test_alert_page_shows_identity_strip(setup):
    """When identified_entities is non-empty, the page shows the
    actor name + identity method + confidence."""
    client, alert_log, _, _ = setup
    alert_log.record(_alert())
    resp = await client.get("/alert/evt1")
    text = await resp.text()
    assert "Alice" in text
    assert "face_arcface" in text
    assert "0.91" in text


async def test_alert_page_shows_detection_summary(setup):
    """Detection list is collapsed by kind to keep it readable."""
    client, alert_log, _, _ = setup
    alert_log.record(_alert())
    resp = await client.get("/alert/evt1")
    text = await resp.text()
    # 2 person detections in the test fixture.
    assert "person" in text
    assert "x 2" in text


async def test_alert_page_shows_rule_that_fired(setup):
    """Epic 10.9 Part A: the page surfaces WHY it fired — the HA AI
    classification + the triggering binary_sensor entity."""
    client, alert_log, _, _ = setup
    alert_log.record(_alert(triggering_sensor="binary_sensor.front_porch_person"))
    resp = await client.get("/alert/evt1")
    text = await resp.text()
    assert "Triggered by" in text
    # Classification rendered title-cased.
    assert "Person" in text
    # Underlying sensor entity surfaced for the curious / debugging.
    assert "binary_sensor.front_porch_person" in text


async def test_alert_page_omits_trigger_card_without_trigger_info(setup):
    """No classification + no sensor → no empty 'Triggered by' card."""
    client, alert_log, _, _ = setup
    alert_log.record(_alert(sensor_classification=None, triggering_sensor=None))
    resp = await client.get("/alert/evt1")
    text = await resp.text()
    assert "Triggered by" not in text


async def test_alert_page_shows_vlm_not_yet_analyzed_when_none(setup):
    """The VLM hook is reserved in the schema but not populated
    yet — page should say so explicitly so a debugging operator
    knows it's a Phase 11 gap, not a render bug."""
    client, alert_log, _, _ = setup
    alert_log.record(_alert())
    resp = await client.get("/alert/evt1")
    text = await resp.text()
    assert "Not yet analyzed" in text


async def test_alert_page_includes_fp_form_when_no_feedback(setup):
    """The FP capture form is inline at #fp, so the FP notification
    action button can deep-link straight to it."""
    client, alert_log, _, _ = setup
    alert_log.record(_alert())
    resp = await client.get("/alert/evt1")
    text = await resp.text()
    assert 'id="fp"' in text or "id='fp'" in text
    assert "empty_frame" in text
    assert "wrong_identity" in text
    assert "known_event" in text
    assert "camera_glitch" in text


async def test_alert_page_relative_urls_resolve_correctly(setup):
    """The alert page's own sub-resource URLs (hero img, dismiss + FP
    form actions) must RESOLVE to /alert/<id>/<resource> from the
    page at /alert/<id> — under whatever <base href> the shell emits.

    History — two bugs this guards against simultaneously:
      - v0.3.20 'alert/' DOUBLING: with base './', a prefixed
        'alert/evt1/x' resolved to /alert/alert/evt1/x.
      - depth-aware-base REGRESSION: once the shell emitted '../' for
        depth-2 pages (to keep nav links app-root-relative), a BARE
        'evt1/x' resolved to /evt1/x → 404, so no image/video appeared.

    The earlier version of this test only string-matched the bare form,
    so it passed straight through the second regression. This version
    instead extracts the actual <base href> and resolves each URL the
    way a browser does (RFC 3986 §5) — it stays correct no matter which
    base the shell chooses.
    """
    import re
    from urllib.parse import urljoin

    client, alert_log, _, _ = setup
    alert_log.record(_alert())
    page_url = "/alert/evt1"
    resp = await client.get(page_url)
    text = await resp.text()

    base_m = re.search(r"<base href='([^']+)'>", text)
    assert base_m, "shell must emit a <base href>"
    base = base_m.group(1)

    def resolve(link: str) -> str:
        return urljoin(urljoin(page_url, base), link)

    # The hero frame <img> (class='event-frame') must hit the real route.
    hero = re.search(r"class='event-frame' src='([^']+)'", text)
    assert hero, "expected the hero event-frame <img src>"
    assert resolve(hero.group(1)) == "/alert/evt1/annotated.jpg"

    # Both form actions (dismiss + FP feedback) must hit their routes.
    resolved_actions = {resolve(a) for a in re.findall(r"action='([^']+)'", text)}
    assert "/alert/evt1/dismiss" in resolved_actions
    assert "/alert/evt1/feedback" in resolved_actions
    # Never the doubled form (the v0.3.20 bug).
    assert "/alert/alert/" not in text


async def test_alert_page_hides_fp_form_after_feedback_submitted(setup):
    """Once the user has submitted feedback, the form's gone — we
    show the recorded reason instead. Prevents resubmissions
    cluttering the data."""
    client, alert_log, event_store, _ = setup
    alert_log.record(_alert())
    event_store.record_feedback(
        "evt1", feedback={"reason": "empty_frame", "kind": "false_positive"}
    )
    resp = await client.get("/alert/evt1")
    text = await resp.text()
    # No FP form.
    assert "Submit feedback" not in text
    # But the recorded reason is shown.
    assert "Feedback recorded" in text


# ─── POST /alert/<id>/feedback ──────────────────────────────────────


async def test_post_feedback_records_to_store(setup):
    client, alert_log, event_store, _ = setup
    alert_log.record(_alert())
    resp = await client.post(
        "/alert/evt1/feedback",
        data={
            "reason": "empty_frame",
            "notes": "just leaves in the wind",
        },
        allow_redirects=False,
    )
    # Redirects back to the alert page. ../{event_id}?fp=1 resolves
    # from /alert/evt1/feedback to /alert/evt1?fp=1 (the alert page).
    # Earlier versions used ../alert/evt1 which resolved to
    # /alert/alert/evt1 (a 404). Check the EXACT Location now.
    assert resp.status == 303
    assert resp.headers["Location"] == "../evt1?fp=1"
    # Feedback is durable on disk.
    meta = event_store.get("evt1")
    assert meta is not None
    assert meta["feedback"]["reason"] == "empty_frame"
    assert meta["feedback"]["notes"] == "just leaves in the wind"
    # And AlertLog reflects the acknowledgment.
    a = alert_log.get("evt1")
    assert a is not None
    assert a["feedback"] == "fp:empty_frame"


async def test_post_feedback_400_for_invalid_reason(setup):
    client, alert_log, _, _ = setup
    alert_log.record(_alert())
    resp = await client.post(
        "/alert/evt1/feedback",
        data={"reason": "nonsense"},
        allow_redirects=False,
    )
    assert resp.status == 400


async def test_post_feedback_404_for_unknown_event(setup):
    client, _, _, _ = setup
    resp = await client.post(
        "/alert/ghost/feedback",
        data={"reason": "empty_frame"},
        allow_redirects=False,
    )
    assert resp.status == 404


async def test_post_feedback_captures_actual_actor_id_for_wrong_identity(setup):
    """Wrong-identity case: the user can specify who it actually
    was. Stored alongside the reason for the tuning loop."""
    client, alert_log, event_store, _ = setup
    alert_log.record(_alert())
    resp = await client.post(
        "/alert/evt1/feedback",
        data={
            "reason": "wrong_identity",
            "actual_actor_id": "charlie",
        },
        allow_redirects=False,
    )
    assert resp.status == 303
    meta = event_store.get("evt1")
    assert meta["feedback"]["actual_actor_id"] == "charlie"


# ─── POST /alert/<id>/dismiss ───────────────────────────────────────


async def test_post_dismiss_marks_event(setup):
    client, alert_log, event_store, _ = setup
    alert_log.record(_alert())
    resp = await client.post("/alert/evt1/dismiss", allow_redirects=False)
    assert resp.status == 303
    # ../evt1?dismissed=1 from /alert/evt1/dismiss resolves to
    # /alert/evt1?dismissed=1 — the alert page with a flash banner.
    assert resp.headers["Location"] == "../evt1?dismissed=1"
    meta = event_store.get("evt1")
    assert meta is not None
    assert meta["dismissed"] is True
    # AlertLog mirrors the dismissal.
    a = alert_log.get("evt1")
    assert a["feedback"] == "dismissed"


async def test_post_dismiss_json_response_for_api_clients(setup):
    """The iOS notification action button fires a programmatic
    POST. Accept: application/json gets a JSON response rather than
    a redirect so the Companion app can act on the result without
    parsing HTML."""
    client, alert_log, _, _ = setup
    alert_log.record(_alert())
    resp = await client.post(
        "/alert/evt1/dismiss",
        headers={"Accept": "application/json"},
        allow_redirects=False,
    )
    assert resp.status == 200
    body = await resp.json()
    assert body == {"ok": True}


async def test_post_dismiss_json_404_for_unknown(setup):
    client, _, _, _ = setup
    resp = await client.post(
        "/alert/ghost/dismiss",
        headers={"Accept": "application/json"},
        allow_redirects=False,
    )
    assert resp.status == 404


async def test_alert_page_shows_dismissed_state_after_dismissal(setup):
    """After dismiss, the page swaps the action button for a
    disabled 'Dismissed' marker so the user knows it took."""
    client, alert_log, _, _ = setup
    alert_log.record(_alert())
    await client.post("/alert/evt1/dismiss", allow_redirects=False)
    resp = await client.get("/alert/evt1")
    text = await resp.text()
    assert "Dismissed" in text


# ─── GET /alert/<id>/frame.jpg ──────────────────────────────────────


async def test_frame_serves_evidence_copy(setup, tmp_path: Path):
    """When the alert was recorded with an evidence_ref, the frame
    is served from the EventStore's copy (not the original path),
    so the alert's frame survives evidence-cleanup."""
    client, alert_log, _, _ = setup
    snap = tmp_path / "snap.jpg"
    snap.write_bytes(b"\xff\xd8\xff\xd9JPEG-BYTES")
    alert_log.record(_alert(evidence_ref=str(snap)))
    resp = await client.get("/alert/evt1/frame.jpg")
    assert resp.status == 200
    body = await resp.read()
    assert body == b"\xff\xd8\xff\xd9JPEG-BYTES"


async def test_frame_404_when_no_evidence(setup):
    client, alert_log, _, _ = setup
    alert_log.record(_alert())  # no evidence_ref
    resp = await client.get("/alert/evt1/frame.jpg")
    assert resp.status == 404


async def test_annotated_frame_falls_back_to_raw_when_no_annotation(setup, tmp_path):
    """Phase 10.3.3 markup is HA-agent-side TODO — for now, annotated
    falls back to raw so the page's <img> renders something."""
    client, alert_log, _, _ = setup
    snap = tmp_path / "x.jpg"
    snap.write_bytes(b"\xff\xd8\xff\xd9X")
    alert_log.record(_alert(evidence_ref=str(snap)))
    resp = await client.get("/alert/evt1/annotated.jpg")
    assert resp.status == 200
    assert await resp.read() == b"\xff\xd8\xff\xd9X"
