"""Activity page (Task 7 / Part IV) — depth + filters + pagination."""

from __future__ import annotations

from kukiihome_ha_agent.web_ui.activity import (
    DEFAULT_PAGE_SIZE,
    parse_filters,
    render_activity_page,
)

NOW = 1_700_000_000.0


def _alert(
    *,
    eid,
    kind="person",
    cam="pool",
    cam_friendly="Pool Camera Fluent",
    status="alerted",
    ts_delta=600,
    ack=False,
):
    return {
        "event_id": eid,
        "camera_id": cam,
        "camera_friendly_name": cam_friendly,
        "kind": kind,
        "trigger_ts": NOW - ts_delta,
        "triage_status": status,
        "acknowledged": ack,
    }


def _kw(**kw):
    """Defaults the renderer expects when called outside the route."""
    base = {
        "show_passive": True,
        "show_actions": True,
        "cameras": set(),
        "kinds": set(),
        "now_ts": NOW,
        "page": 0,
    }
    base.update(kw)
    return base


# ─── filter behavior ──────────────────────────────────────────────


def test_action_passive_lane_toggles():
    alerts = [
        _alert(eid="a1", status="alerted"),  # action
        _alert(eid="a2", status="alerted"),  # action
        _alert(eid="p1", status="dismissed"),  # passive
        _alert(eid="p2", status="dismissed"),  # passive
    ]
    actions_only = render_activity_page(
        alerts_all=alerts,
        **_kw(show_passive=False, show_actions=True),
    )
    passives_only = render_activity_page(
        alerts_all=alerts,
        **_kw(show_passive=True, show_actions=False),
    )
    assert "Showing 2 of 2" in actions_only and "0 passive" in actions_only
    assert "Showing 2 of 2" in passives_only and "0 action" in passives_only


def test_camera_filter_narrows_set():
    alerts = [
        _alert(eid="a1", cam="pool", cam_friendly="Pool Camera"),
        _alert(eid="a2", cam="pool", cam_friendly="Pool Camera"),
        _alert(eid="a3", cam="front_door", cam_friendly="Front Door Camera"),
    ]
    only_pool = render_activity_page(
        alerts_all=alerts,
        **_kw(cameras={"pool"}),
    )
    assert "Showing 2 of 2" in only_pool


def test_kind_filter_narrows_set():
    alerts = [
        _alert(eid="a1", kind="person"),
        _alert(eid="a2", kind="dog"),
        _alert(eid="a3", kind="dog"),
    ]
    only_dogs = render_activity_page(
        alerts_all=alerts,
        **_kw(kinds={"dog"}),
    )
    assert "Showing 2 of 2" in only_dogs


def test_filter_strip_includes_camera_friendly_names_stripped():
    """Camera dropdown uses the friendly name, stripped of stream-quality
    suffixes (Task 3) — *"Pool Camera"* not *"Pool Camera Fluent"*."""
    alerts = [_alert(eid="a1", cam="pool", cam_friendly="Pool Camera Fluent")]
    html = render_activity_page(alerts_all=alerts, **_kw())
    assert ">Pool Camera</option>" in html
    assert "Fluent" not in html


def test_filter_strip_lists_kinds_present():
    alerts = [
        _alert(eid="a1", kind="person"),
        _alert(eid="a2", kind="dog"),
    ]
    html = render_activity_page(alerts_all=alerts, **_kw())
    assert ">Person</option>" in html
    assert ">Dog</option>" in html


def test_empty_filtered_set_shows_helpful_copy():
    alerts = [_alert(eid="a1", kind="person")]
    html = render_activity_page(
        alerts_all=alerts,
        **_kw(kinds={"vehicle"}),
    )
    assert "No activity matches the current filters" in html


# ─── pagination ────────────────────────────────────────────────────


def test_pagination_load_earlier_link():
    # 50 alerts; default page_size = DEFAULT_PAGE_SIZE
    alerts = [_alert(eid=f"a{i}", ts_delta=600 + i) for i in range(50)]
    html_page0 = render_activity_page(alerts_all=alerts, **_kw())
    assert f"Showing {DEFAULT_PAGE_SIZE} of 50" in html_page0
    assert "Load earlier" in html_page0
    assert "page=1" in html_page0

    html_last = render_activity_page(alerts_all=alerts, **_kw(page=10))
    assert "Showing 50 of 50" in html_last
    assert "Load earlier" not in html_last


def test_pagination_preserves_filters_in_load_link():
    alerts = [
        _alert(eid=f"a{i}", kind="dog", cam="pool", cam_friendly="Pool Camera", ts_delta=600 + i)
        for i in range(50)
    ]
    html = render_activity_page(
        alerts_all=alerts,
        **_kw(kinds={"dog"}, cameras={"pool"}),
    )
    assert "page=1" in html and "kind=dog" in html and "cam=pool" in html


# ─── query-string parsing ─────────────────────────────────────────


def test_parse_filters_fresh_visit_defaults_both_on():
    out = parse_filters({})
    assert out["show_passive"] is True
    assert out["show_actions"] is True
    assert out["cameras"] == set()
    assert out["kinds"] == set()
    assert out["page"] == 0


def test_parse_filters_explicit_form_submission_honors_unticked():
    # passive=1 only → actions box is unticked, should be off
    out = parse_filters({"passive": "1"})
    assert out["show_passive"] is True
    assert out["show_actions"] is False


def test_parse_filters_camera_kind_lists():
    out = parse_filters({"cam": ["pool", "front_door"], "kind": ["dog"]})
    assert out["cameras"] == {"pool", "front_door"}
    assert out["kinds"] == {"dog"}


def test_parse_filters_bad_page_falls_back_to_zero():
    assert parse_filters({"page": "nope"})["page"] == 0
    assert parse_filters({"page": "-3"})["page"] == 0


# ─── shared row schema with home ──────────────────────────────────


def test_uses_same_row_schema_as_home():
    """Single _render_activity_row source — same markup wherever it appears."""
    alerts = [
        _alert(
            eid="e1",
            status="alerted",
            ack=True,
            kind="person",
            cam_friendly="Front South Camera Fluent",
        ),
    ]
    html = render_activity_page(alerts_all=alerts, **_kw())
    assert "activity-row" in html
    # Camera display normalization from Task 3 still applies
    assert "Front South Camera" in html
    assert "Fluent" not in html
    # Trace link still present
    assert "alert/e1" in html
