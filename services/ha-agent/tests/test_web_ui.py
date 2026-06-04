"""v2 product Web UI — shell + home + mocks (pure rendering, no HTTP)."""

from __future__ import annotations

from kukiihome_ha_agent.web_ui.home import render_home_page
from kukiihome_ha_agent.web_ui.mocks import render_diagnostics_page
from kukiihome_ha_agent.web_ui.shell import (
    NAV_ITEMS,
    camera_display_name,
    friendly_time,
    friendly_time_html,
    relative_time,
    render_shell,
)

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


def test_friendly_time_graduates():
    assert friendly_time(NOW - 10, now=NOW) == "Just now"
    # plural minutes
    assert friendly_time(NOW - 300, now=NOW) == "5 minutes ago"
    # singular minute boundary
    assert friendly_time(NOW - 60, now=NOW) == "1 minute ago"
    assert friendly_time(NOW - 4500, now=NOW) == "An hour ago"
    assert friendly_time(NOW - 7200 - 1, now=NOW).endswith("h ago")
    # Yesterday at <clock>: must include "Yesterday" and either AM or PM
    yesterday = friendly_time(NOW - 90000, now=NOW)
    assert yesterday.startswith("Yesterday")
    assert "AM" in yesterday or "PM" in yesterday
    # 6 days back → Last <Weekday> at <clock>
    last_week = friendly_time(NOW - 500000, now=NOW)
    assert last_week.startswith("Last ") and ("AM" in last_week or "PM" in last_week)
    # 30 days back → "Oct 15 at 4:51 PM" shape
    older = friendly_time(NOW - 30 * 86400, now=NOW)
    assert " at " in older and ("AM" in older or "PM" in older)


def test_friendly_time_html_wraps_in_title_span():
    out = friendly_time_html(NOW - 60, now=NOW)
    assert out.startswith("<span title=")
    assert ">1 minute ago</span>" in out
    # title carries an ISO-like timestamp; date parts always present
    assert "T" in out  # ISO date-time separator


def test_relative_time_alias_kept_for_compat():
    # the alias allows callers to migrate at their own pace
    assert relative_time(NOW - 10, now=NOW) == friendly_time(NOW - 10, now=NOW)


# ─── camera display-name normalization (Task 3) ──────────────────────


def test_camera_display_name_strips_stream_quality_suffixes():
    # Reolink Fluent suffix — the example that motivated this work
    assert camera_display_name("Front South Camera Fluent") == "Front South Camera"
    # other Reolink qualities
    assert camera_display_name("Backyard Cam Clear") == "Backyard Cam"
    assert camera_display_name("Driveway Balanced") == "Driveway Camera"
    # Dahua main/sub
    assert camera_display_name("Pool Camera Main") == "Pool Camera"
    assert camera_display_name("Pool Camera Sub") == "Pool Camera"
    # double suffixes (e.g. "Front Main Stream")
    assert camera_display_name("Front Main Stream") == "Front Camera"


def test_camera_display_name_appends_camera_when_missing():
    assert camera_display_name("Driveway") == "Driveway Camera"
    assert camera_display_name("Pool") == "Pool Camera"
    # already contains "Camera" or "Cam" — no append
    assert camera_display_name("Backyard Cam") == "Backyard Cam"
    assert camera_display_name("Front South Camera") == "Front South Camera"


def test_camera_display_name_handles_empty_and_intentional_names():
    assert camera_display_name("") == ""
    assert camera_display_name(None) == ""
    # legit name containing a suffix word as a non-suffix prefix — must not strip
    # ("Sub" appears mid-string, not at the end after whitespace)
    assert camera_display_name("Submarine Bay Camera") == "Submarine Bay Camera"


# ─── headline composition (Task 3) ───────────────────────────────────


def test_headline_uses_explicit_headline_when_present():
    # When the VLM lands, scene_description goes here verbatim
    from kukiihome_ha_agent.web_ui.home import _alert_headline
    assert _alert_headline({"headline": "Alice arrived at the front door"}) == \
        "Alice arrived at the front door"


def test_headline_composes_kind_at_friendly_camera():
    from kukiihome_ha_agent.web_ui.home import _alert_headline
    assert _alert_headline({
        "kind": "person",
        "camera_friendly_name": "Front South Camera Fluent",
    }) == "Person detected at Front South Camera"

    assert _alert_headline({
        "kind": "dog",
        "camera_friendly_name": "Backyard Cam Clear",
    }) == "Dog detected at Backyard Cam"


def test_headline_actor_name_when_resolved():
    from kukiihome_ha_agent.web_ui.home import _alert_headline
    assert _alert_headline({
        "actor_name": "Bob",
        "camera_friendly_name": "Front South Camera Fluent",
    }) == "Bob at Front South Camera"
    assert _alert_headline({"actor_name": "Bob"}) == "Bob seen"


def test_headline_motion_fallback():
    from kukiihome_ha_agent.web_ui.home import _alert_headline
    # No kind, no actor, no headline — just camera
    assert _alert_headline({
        "camera_friendly_name": "Front South Camera Fluent",
    }) == "Motion at Front South Camera"
    # Truly bare alert
    assert _alert_headline({}) == "Motion"


def test_activity_row_drops_redundant_slug_when_in_headline():
    from kukiihome_ha_agent.web_ui.home import _render_activity_row
    html = _render_activity_row({
        "event_id": "e1",
        "camera_id": "front_south",
        "camera_friendly_name": "Front South Camera Fluent",
        "kind": "person",
        "trigger_ts": NOW - 600,
        "triage_status": "alerted",
    }, now_ts=NOW)
    assert "Person detected at Front South Camera" in html
    # · front_south slug must NOT appear: it's already in the headline
    assert "· front_south" not in html

    # When the headline really doesn't mention the camera, the slug DOES help
    html = _render_activity_row({
        "event_id": "e2",
        "camera_id": "front_south",
        "headline": "Unknown delivery",     # nothing about the camera
        "trigger_ts": NOW - 600,
        "triage_status": "alerted",
    }, now_ts=NOW)
    assert "Unknown delivery" in html
    assert "· front_south" in html


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
    # Activity (Task 7) and Intent (Task 9) are no longer mocks — they're
    # served by their own renderers. The remaining mocks are still credible
    # "Coming soon" skeletons.
    pages = {
        "Diagnostics": render_diagnostics_page(),
    }
    for title, html in pages.items():
        assert f"<h1>{title}</h1>" in html
        assert "Coming soon" in html or "coming-soon" in html
        # each mock anchors to the design doc so future me knows where to look
        assert "web-ui-design.md" in html


def test_diagnostics_mock_links_back_to_legacy_status():
    html = render_diagnostics_page("/")
    assert "legacy status page" in html.lower()
    assert "href='/'" in html
