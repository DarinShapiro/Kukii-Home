"""v2 product Web UI — shell + home + mocks (pure rendering, no HTTP)."""

from __future__ import annotations

from kukiihome_ha_agent.web_ui.home import render_home_page
from kukiihome_ha_agent.web_ui.mocks import (
    render_activity_page,
    render_areas_page,
    render_cameras_page,
    render_diagnostics_page,
    render_intent_page,
    render_policies_page,
)
from kukiihome_ha_agent.web_ui.shell import NAV_ITEMS, relative_time, render_shell

NOW = 1_700_000_000.0  # fixed reference time for deterministic relative timestamps


# ─── shell ─────────────────────────────────────────────────────────


def test_shell_renders_nav_with_active_highlight():
    html = render_shell("areas", "<p>hi</p>", version="0.6.0")
    assert "<base href='./'>" in html                  # ingress-safe
    assert "Kukii-Home" in html
    assert "<p>hi</p>" in html
    assert "0.6.0" in html
    for path, label in NAV_ITEMS:
        assert f"href='{path}'" in html
        assert label in html
    # active tab carries .active; others don't
    assert "class='active' href='areas'" in html
    assert "class='' href='home'" in html


def test_shell_flash_is_rendered_and_escaped():
    html = render_shell("home", "x", flash="<script>alert(1)</script>")
    assert "<script>" not in html
    assert "alert(1)" in html


def test_relative_time_graduates():
    assert relative_time(NOW - 10, now=NOW) == "Just now"
    assert relative_time(NOW - 300, now=NOW) == "5m ago"
    assert relative_time(NOW - 4500, now=NOW) == "An hour ago"
    assert relative_time(NOW - 7200 - 1, now=NOW).endswith("h ago")
    assert relative_time(NOW - 90000, now=NOW) == "Yesterday"
    # 7-day boundary returns weekday or "Earlier" — both acceptable strings
    week = relative_time(NOW - 500000, now=NOW)
    assert isinstance(week, str) and len(week) > 0


# ─── home page — empty state (win-state) ───────────────────────────


def test_home_empty_is_winstate():
    html = render_home_page(
        alerts_recent=[], unresolved_tracks=0,
        cameras_total=0, cameras_active=0,
        preprocessor_ok=None, ha_connected=False, ha_entities=0,
        now_ts=NOW,
    )
    assert "All quiet" in html or "Nothing yet" in html
    # Empty Needs Attention → win-state copy, not a sad blank
    assert "Nothing needs you" in html
    # Empty Activity → reassurance copy, not a sad blank
    assert "system is watching" in html.lower()


# ─── home page — with real-shaped alert data ───────────────────────


def _alert(headline, status="alerted", cam="pool", ts=NOW - 600,
           acknowledged=False, event_id="e1"):
    return {
        "headline": headline, "camera_id": cam,
        # trigger_ts is a plain unix ts — _alert_when_ts will use it when
        # recorded_at is absent (we leave it absent here for simplicity).
        "trigger_ts": ts,
        "triage_status": status,
        "acknowledged": acknowledged, "event_id": event_id,
    }


def test_home_status_line_and_activity():
    alerts = [
        _alert("Alice arrived", status="alerted", cam="front_door",
               ts=NOW - 600, acknowledged=True, event_id="e1"),
        _alert("Rex in backyard", status="dismissed", cam="backyard",
               ts=NOW - 3600, event_id="e2"),
        _alert("Unknown delivery", status="alerted", cam="front_door",
               ts=NOW - 7200, acknowledged=False, event_id="e3"),
    ]
    html = render_home_page(
        alerts_recent=alerts, unresolved_tracks=5,
        cameras_total=4, cameras_active=4,
        preprocessor_ok=True, ha_connected=True, ha_entities=18,
        now_ts=NOW,
    )
    # Status line reports today's counts including unhandled
    assert "1 unhandled" in html or "unhandled" in html
    # Identity inbox row shows the count + Review CTA
    assert "5</b> unnamed" in html and "Review" in html
    # All three alerts surface as activity rows
    assert "Alice arrived" in html
    assert "Rex in backyard" in html
    assert "Unknown delivery" in html
    # Passive row gets the muted class; action rows don't
    assert "activity-row passive" in html
    # Trust contract line shows action/passive split
    assert "passive" in html and "system is reasoning" in html
    # System stripe shows preprocessor + HA reachability
    assert "Preprocessor reachable" in html
    assert "HA connected" in html and "18 entities" in html


def test_home_html_escapes_alert_content():
    alerts = [_alert(headline="<img src=x>", event_id="<evil>")]
    html = render_home_page(
        alerts_recent=alerts, unresolved_tracks=0,
        cameras_total=1, cameras_active=1,
        preprocessor_ok=True, ha_connected=True, ha_entities=0,
        now_ts=NOW,
    )
    assert "<img src=x>" not in html
    assert "&lt;img" in html
    assert "<evil>" not in html


def test_home_unresolved_tracks_disappears_when_zero():
    html = render_home_page(
        alerts_recent=[_alert("ok")], unresolved_tracks=0,
        cameras_total=1, cameras_active=1,
        preprocessor_ok=True, ha_connected=True, ha_entities=0,
        now_ts=NOW,
    )
    assert "unnamed track" not in html  # no row when none unresolved


# ─── mock pages: nav targets render with explanatory content ───────


def test_each_mock_renders_explainer():
    pages = {
        "Activity": render_activity_page(),
        "Areas": render_areas_page(),
        "Intent": render_intent_page(),
        "Policies": render_policies_page(),
        "Cameras": render_cameras_page(),
        "Diagnostics": render_diagnostics_page(),
    }
    for title, html in pages.items():
        assert f"<h1>{title}</h1>" in html
        assert "Coming soon" in html or "coming-soon" in html
        # each mock anchors to the design doc so future me knows where to look
        assert "web-ui-design.md" in html


def test_intent_mock_calls_out_both_preferences_and_rules():
    html = render_intent_page()
    assert "Preferences" in html
    assert "Rules" in html
    assert "VLM" in html       # mentions the reasoner


def test_diagnostics_mock_links_back_to_legacy_status():
    html = render_diagnostics_page("/")
    assert "legacy status page" in html.lower()
    assert "href='/'" in html
