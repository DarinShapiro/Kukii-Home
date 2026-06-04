"""Cameras (Part II) — list + detail rendering + whitelist forms."""

from __future__ import annotations

import pytest
from kukiihome_ha_agent.action_store import PerceptionEntry, ProtectiveEntry
from kukiihome_ha_agent.web_ui.camera_data import (
    build_camera_detail,
    build_camera_summaries,
    infer_capability_matrix,
)
from kukiihome_ha_agent.web_ui.cameras import (
    CameraDetailViewModel,
    CameraSummary,
    CapabilityRow,
    parse_perception_form,
    parse_protective_form,
    render_camera_detail,
    render_cameras_list,
    render_perception_form,
    render_protective_form,
)

NOW = 1_700_000_000.0


# ─── view-model builders ─────────────────────────────────────────


class _FakeStatus:
    def __init__(self, cid, *, state="running", err="",
                 frames=0, motions=0):
        self.camera_id = cid
        self.state = state
        self.last_error = err
        self.frames_read = frames
        self.motion_events = motions


class _FakeHALoop:
    def __init__(self, cid, friendly):
        self.camera_id = cid
        self.friendly_name = friendly


def _alert(camera_id, *, kind="person", ts=NOW - 600):
    return {
        "camera_id": camera_id, "trigger_ts": ts,
        "sensor_classification": kind,
    }


def test_build_camera_summaries_merges_registry_and_ha_loops():
    statuses = [_FakeStatus("pool", state="running")]
    loops = [_FakeHALoop("pool", "Pool Camera Fluent"),
             _FakeHALoop("front", "Front Door Cam")]
    summaries = build_camera_summaries(
        registry_statuses=statuses, ha_loops=loops,
        alerts=[], now_ts=NOW,
    )
    by_id = {c.camera_id: c for c in summaries}
    assert "pool" in by_id and "front" in by_id
    # Friendly name + suffix-strip from Task 3
    assert by_id["pool"].name == "Pool Camera"
    assert by_id["pool"].state == "running"


def test_build_camera_summaries_events_24h_filter():
    summaries = build_camera_summaries(
        registry_statuses=[_FakeStatus("pool")],
        ha_loops=[],
        alerts=[
            _alert("pool", ts=NOW - 3600),       # within 24h
            _alert("pool", ts=NOW - 30 * 3600),  # outside 24h
            _alert("other", ts=NOW - 100),       # different camera
        ],
        now_ts=NOW,
    )
    assert summaries[0].events_24h == 1


def test_build_camera_summaries_last_motion_ts_is_most_recent():
    summaries = build_camera_summaries(
        registry_statuses=[_FakeStatus("pool")],
        ha_loops=[],
        alerts=[_alert("pool", ts=NOW - 100),
                _alert("pool", ts=NOW - 7000)],
        now_ts=NOW,
    )
    assert summaries[0].last_motion_ts == NOW - 100


# ─── capability matrix inference ────────────────────────────────


def test_capability_matrix_motion_always_present_when_events_seen():
    rows = infer_capability_matrix(
        [_alert("pool", kind="person")], camera_id="pool",
    )
    motion_row = next(r for r in rows if r.signal == "motion")
    assert motion_row.source == "NATIVE"
    assert not motion_row.needs_action


def test_capability_matrix_missing_motion_when_no_events_warns():
    rows = infer_capability_matrix([], camera_id="pool")
    motion_row = next(r for r in rows if r.signal == "motion")
    assert motion_row.source == "MISSING"
    assert motion_row.needs_action  # surface ⚠ on the page


def test_capability_matrix_person_inferred_from_classification():
    rows = infer_capability_matrix(
        [_alert("pool", kind="person")], camera_id="pool",
    )
    p = next(r for r in rows if r.signal == "person")
    assert p.source == "AUGMENTED"


def test_capability_matrix_signals_missing_when_never_classified():
    rows = infer_capability_matrix(
        [_alert("pool", kind="person")], camera_id="pool",
    )
    v = next(r for r in rows if r.signal == "vehicle")
    assert v.source == "MISSING"


# ─── detail view model ──────────────────────────────────────────


def test_build_camera_detail_returns_none_for_unknown_camera():
    assert build_camera_detail(
        camera_id="ghost", registry_statuses=[],
        ha_loops=[], alerts=[], perception_entries=[], protective_entries=[],
        now_ts=NOW,
    ) is None


def test_build_camera_detail_includes_whitelist_entries():
    perc = [PerceptionEntry(camera_id="pool", target_kind="ha_service",
                             target="light.turn_on:light.pool")]
    prot = [ProtectiveEntry(
        camera_id="pool", action_class="siren", service="switch.turn_on",
        target="switch.siren_one", min_severity="critical", min_confidence=0.9,
    )]
    vm = build_camera_detail(
        camera_id="pool", registry_statuses=[_FakeStatus("pool")],
        ha_loops=[_FakeHALoop("pool", "Pool")], alerts=[],
        perception_entries=perc, protective_entries=prot, now_ts=NOW,
    )
    assert vm is not None
    assert len(vm.perception_whitelist) == 1
    assert vm.perception_whitelist[0].target == "light.turn_on:light.pool"
    assert vm.protective_whitelist[0].action_class == "siren"


# ─── list page rendering ────────────────────────────────────────


def test_render_cameras_list_empty_state_explains_discovery():
    html = render_cameras_list([])
    assert "<h1>Cameras</h1>" in html
    assert "No cameras configured" in html


def test_render_cameras_list_renders_tiles_with_state_chips():
    cams = [
        CameraSummary(camera_id="pool", name="Pool Camera",
                      state="running", events_24h=5),
        CameraSummary(camera_id="front", name="Front Camera",
                      state="error", last_error="rtsp timeout"),
    ]
    html = render_cameras_list(cams)
    assert "Pool Camera" in html
    assert "Front Camera" in html
    assert "5 events" in html
    assert "rtsp timeout" in html
    # state chips colored by class
    assert "cam-state ok" in html and "cam-state bad" in html
    # Click-through anchors
    assert "href='cameras/pool'" in html
    assert "href='cameras/front'" in html


def test_render_cameras_list_sorts_alphabetically():
    cams = [
        CameraSummary(camera_id="z", name="Zebra Cam", state="running"),
        CameraSummary(camera_id="a", name="Alpha Cam", state="running"),
    ]
    html = render_cameras_list(cams)
    assert html.index("Alpha Cam") < html.index("Zebra Cam")


def test_render_cameras_list_html_escapes_camera_names():
    cams = [CameraSummary(camera_id="evil", name="<script>",
                          state="running")]
    html = render_cameras_list(cams)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ─── detail page rendering ──────────────────────────────────────


def _make_vm(**kw):
    base: dict = dict(  # noqa: C408
        camera_id="pool", name="Pool Camera", state="running",
        events_24h=12, last_motion_ts=NOW - 600,
    )
    base.update(kw)
    return CameraDetailViewModel(**base)


def test_detail_page_includes_all_sections():
    vm = _make_vm(
        capabilities=[
            CapabilityRow(signal="motion", source="NATIVE",
                          detail="Dahua SMD"),
            CapabilityRow(signal="person", source="AUGMENTED",
                          detail="Dahua trigger + YOLO"),
        ],
        health={"frames_read": 1234, "motion_events": 8},
    )
    html = render_camera_detail(vm)
    # Each card heading is present
    for heading in ("Identity", "Detection capability",
                    "Authorized actions", "Health"):
        assert heading in html
    # Matrix entries surface
    assert "motion" in html and "NATIVE" in html
    assert "Dahua SMD" in html
    # Health fields visible
    assert "1234" in html and "8" in html
    # Activity link is present + cameras-filtered
    assert "activity?cam=pool" in html


def test_detail_page_shows_whitelist_remove_buttons():
    from kukiihome_ha_agent.web_ui.cameras import (
        PerceptionEntryView,
        ProtectiveEntryView,
    )
    vm = _make_vm(
        perception_whitelist=[PerceptionEntryView(
            target_kind="ha_service",
            target="light.turn_on:light.pool", max_duration_s=60,
        )],
        protective_whitelist=[ProtectiveEntryView(
            action_class="lock", service="lock.lock",
            target="lock.back_door", min_severity="critical",
            min_confidence=0.8,
        )],
    )
    html = render_camera_detail(vm)
    assert "light.turn_on:light.pool" in html
    assert "lock.back_door" in html
    # Remove buttons + correct form actions
    assert "perception/delete" in html
    assert "protective/delete" in html


def test_detail_page_empty_whitelist_shows_onboarding_copy():
    vm = _make_vm()
    html = render_camera_detail(vm)
    assert "No perception actions authorized" in html
    assert "No protective actions authorized" in html


def test_detail_page_capability_warning_for_missing_critical():
    vm = _make_vm(capabilities=[
        CapabilityRow(signal="motion", source="MISSING",
                      detail="no events recorded yet",
                      critical_if_missing=True, needs_action=True),
    ])
    html = render_camera_detail(vm)
    assert "MISSING" in html
    assert "⚠" in html


# ─── whitelist forms ────────────────────────────────────────────


def test_perception_form_includes_both_kind_radios():
    html = render_perception_form("pool")
    assert "value='ha_service'" in html
    assert "value='camera_api'" in html
    assert "action='cameras/pool/whitelist/perception'" in html


def test_protective_form_includes_severity_radios():
    html = render_protective_form("pool")
    for sev in ("low", "normal", "critical"):
        assert f"value='{sev}'" in html
    assert "action='cameras/pool/whitelist/protective'" in html


# ─── form parsing ───────────────────────────────────────────────


def test_parse_perception_form_minimum_required_target():
    out = parse_perception_form({
        "target_kind": "ha_service",
        "target": "light.turn_on:light.pool",
        "max_duration_s": "60",
    })
    assert out["target_kind"] == "ha_service"
    assert out["target"] == "light.turn_on:light.pool"
    assert out["max_duration_s"] == 60


def test_parse_perception_form_caps_max_duration():
    assert parse_perception_form({"target": "x", "max_duration_s": "9999"})[
        "max_duration_s"] == 600
    assert parse_perception_form({"target": "x", "max_duration_s": "0"})[
        "max_duration_s"] == 1


def test_parse_perception_form_bad_kind_falls_back():
    out = parse_perception_form({"target_kind": "garbage", "target": "x"})
    assert out["target_kind"] == "ha_service"


def test_parse_perception_form_missing_target_raises():
    with pytest.raises(ValueError):
        parse_perception_form({"target": "  "})


def test_parse_protective_form_complete_path():
    out = parse_protective_form({
        "action_class": "lock", "service": "lock.lock",
        "target": "lock.x", "min_severity": "critical",
        "min_confidence": "0.85", "redundancy_required": "1",
    })
    assert out["min_confidence"] == pytest.approx(0.85)
    assert out["redundancy_required"] == 1


def test_parse_protective_form_caps_confidence_and_redundancy():
    out = parse_protective_form({
        "action_class": "a", "service": "s", "target": "t",
        "min_confidence": "5", "redundancy_required": "100",
    })
    assert out["min_confidence"] == 1.0
    assert out["redundancy_required"] == 5


def test_parse_protective_form_missing_required_raises():
    with pytest.raises(ValueError):
        parse_protective_form({"action_class": "lock"})  # missing service+target


def test_parse_protective_form_bad_severity_falls_back():
    out = parse_protective_form({
        "action_class": "a", "service": "s", "target": "t",
        "min_severity": "garbage",
    })
    assert out["min_severity"] == "critical"
