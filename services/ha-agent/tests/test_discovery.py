"""Tests for the auto-discovery heuristics (no IO, pure logic)."""

from __future__ import annotations

from kukiihome_ha_agent.discovery import (
    DEFAULT_COOLDOWN_SECONDS,
    build_decisions,
    group_by_device,
    pick_motions,
    pick_stream,
)
from kukiihome_ha_agent.mcp_tools import HACameraEntity


def _cam(
    entity: str,
    *,
    state: str = "idle",
    motions: list[str] | None = None,
    name: str | None = None,
) -> HACameraEntity:
    return HACameraEntity(
        camera_entity=entity,
        friendly_name=name or entity.removeprefix("camera.").replace("_", " ").title(),
        state=state,
        motion_candidates=motions or [],
    )


# ─── group_by_device ─────────────────────────────────────────────────


def test_group_by_device_collapses_dahua_streams():
    """Dahua exposes main + sub + sub_2 + sub_3 — all one device."""
    cams = [
        _cam("camera.dahuapoolcam_main"),
        _cam("camera.dahuapoolcam_sub"),
        _cam("camera.dahuapoolcam_sub_2"),
        _cam("camera.dahuapoolcam_sub_3"),
    ]
    groups = group_by_device(cams)
    assert len(groups) == 1
    assert "dahuapoolcam" in groups
    assert len(groups["dahuapoolcam"]) == 4


def test_group_by_device_collapses_reolink_streams():
    """Reolink Fluent + ONVIF mainstream share the device-token set."""
    cams = [
        _cam("camera.front_south_camera_fluent"),
        _cam("camera.front_south_camera_profile000_mainstream"),
    ]
    groups = group_by_device(cams)
    assert len(groups) == 1
    # Tokens become 'front'+'south' (camera/stream/etc dropped).
    assert "front_south" in groups


def test_group_by_device_keeps_unrelated_cameras_separate():
    cams = [
        _cam("camera.dahuapoolcam_main"),
        _cam("camera.front_south_camera_fluent"),
    ]
    groups = group_by_device(cams)
    assert len(groups) == 2


# ─── pick_stream ─────────────────────────────────────────────────────


def test_pick_stream_prefers_fluent_over_mainstream_reolink():
    cams = [
        _cam("camera.front_south_camera_profile000_mainstream"),
        _cam("camera.front_south_camera_fluent"),
        _cam("camera.front_south_camera_clear"),
    ]
    pick = pick_stream(cams)
    assert pick is not None
    assert pick.camera_entity == "camera.front_south_camera_fluent"


def test_pick_stream_excludes_onvif_profile():
    """The _profile000_mainstream entity returns broken HTML — never pick."""
    cams = [_cam("camera.front_south_camera_profile000_mainstream")]
    assert pick_stream(cams) is None


def test_pick_stream_excludes_duplicate_dahua_substreams():
    cams = [
        _cam("camera.dahuapoolcam_sub_2"),
        _cam("camera.dahuapoolcam_sub_3"),
        _cam("camera.dahuapoolcam_main"),
        _cam("camera.dahuapoolcam_sub"),
    ]
    pick = pick_stream(cams)
    assert pick is not None
    # _sub wins over _main; _sub_2 and _sub_3 are excluded outright.
    assert pick.camera_entity == "camera.dahuapoolcam_sub"


def test_pick_stream_skips_unavailable():
    cams = [
        _cam("camera.dahuapoolcam_sub", state="unavailable"),
        _cam("camera.dahuapoolcam_main", state="idle"),
    ]
    pick = pick_stream(cams)
    assert pick is not None
    assert pick.camera_entity == "camera.dahuapoolcam_main"


def test_pick_stream_returns_none_when_all_unavailable():
    cams = [
        _cam("camera.dahuapoolcam_main", state="unavailable"),
        _cam("camera.dahuapoolcam_sub", state="unknown"),
    ]
    assert pick_stream(cams) is None


# ─── pick_motions ────────────────────────────────────────────────────


def test_pick_motions_prefers_ai_classification():
    """smart_motion_human + smart_motion_vehicle > generic motion_alarm."""
    motions = [
        "binary_sensor.dahuapoolcam_motion_alarm",
        "binary_sensor.dahuapoolcam_smart_motion_human",
        "binary_sensor.dahuapoolcam_smart_motion_vehicle",
    ]
    chosen = pick_motions(motions)
    assert chosen == [
        "binary_sensor.dahuapoolcam_smart_motion_human",
        "binary_sensor.dahuapoolcam_smart_motion_vehicle",
    ]


def test_pick_motions_excludes_noisy_generics():
    """motion_alarm + cell_motion_detection + video_motion_info are noise."""
    motions = [
        "binary_sensor.front_south_motion_alarm",
        "binary_sensor.front_south_cell_motion_detection",
        "binary_sensor.front_south_video_motion_info",
        "binary_sensor.front_south_person_detection",
    ]
    chosen = pick_motions(motions)
    assert chosen == ["binary_sensor.front_south_person_detection"]


def test_pick_motions_falls_back_when_no_ai():
    """When no AI sensors, use _person / _vehicle / _animal."""
    motions = [
        "binary_sensor.front_south_motion_alarm",
        "binary_sensor.front_south_person",
        "binary_sensor.front_south_vehicle",
    ]
    chosen = pick_motions(motions)
    # motion_alarm excluded; _person + _vehicle kept.
    assert chosen == [
        "binary_sensor.front_south_person",
        "binary_sensor.front_south_vehicle",
    ]


def test_pick_motions_returns_intrusion_area():
    motions = [
        "binary_sensor.front_south_intrusion_area_1_person",
        "binary_sensor.front_south_intrusion_area_1_vehicle",
    ]
    chosen = pick_motions(motions)
    assert chosen == motions


def test_pick_motions_empty_when_only_excluded():
    motions = ["binary_sensor.dahua_motion_alarm"]
    assert pick_motions(motions) == []


# ─── build_decisions (top-level) ─────────────────────────────────────


def test_build_decisions_dahua_pool_cam_with_4_substreams():
    """The real Dahua case from the user's HA."""
    cams = [
        _cam(
            "camera.dahuapoolcam_main",
            motions=[
                "binary_sensor.dahuapoolcam_motion_alarm",
                "binary_sensor.dahuapoolcam_smart_motion_human",
                "binary_sensor.dahuapoolcam_smart_motion_vehicle",
            ],
        ),
        _cam("camera.dahuapoolcam_sub"),
        _cam("camera.dahuapoolcam_sub_2"),
        _cam("camera.dahuapoolcam_sub_3"),
    ]
    decisions = build_decisions(cams)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.enabled is True
    assert d.spec is not None
    # _sub wins over _main; _sub_2/_sub_3 excluded.
    assert d.spec.camera_entity == "camera.dahuapoolcam_sub"
    # AI motion sensors picked over the noisy motion_alarm.
    assert d.spec.motion_entities == (
        "binary_sensor.dahuapoolcam_smart_motion_human",
        "binary_sensor.dahuapoolcam_smart_motion_vehicle",
    )
    assert d.spec.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS
    assert d.spec.source == "auto"
    # All 4 streams surfaced as candidates for the UI.
    assert len(d.candidate_streams) == 4


def test_build_decisions_reolink_broken_onvif_present():
    """Mix of working Reolink Fluent + broken ONVIF mainstream."""
    cams = [
        _cam(
            "camera.front_south_camera_fluent",
            motions=[
                "binary_sensor.front_south_camera_person",
                "binary_sensor.front_south_camera_vehicle",
                "binary_sensor.front_south_camera_cell_motion_detection",
            ],
        ),
        _cam("camera.front_south_camera_profile000_mainstream"),
    ]
    decisions = build_decisions(cams)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.enabled is True
    assert d.spec is not None
    # ONVIF profile mainstream is excluded — _fluent picked.
    assert d.spec.camera_entity == "camera.front_south_camera_fluent"


def test_build_decisions_user_disabled_device():
    cams = [
        _cam("camera.dahuapoolcam_main", motions=["binary_sensor.dahuapoolcam_smart_motion_human"])
    ]
    decisions = build_decisions(cams, overrides={"dahuapoolcam": {"enabled": False}})
    assert len(decisions) == 1
    assert decisions[0].enabled is False
    assert decisions[0].spec is None


def test_build_decisions_stream_override_clobbers_ai_pick():
    cams = [
        _cam("camera.dahuapoolcam_main", motions=["binary_sensor.dahuapoolcam_smart_motion_human"]),
        _cam("camera.dahuapoolcam_sub"),
    ]
    decisions = build_decisions(
        cams,
        overrides={"dahuapoolcam": {"stream_override": "camera.dahuapoolcam_main"}},
    )
    assert decisions[0].spec is not None
    assert decisions[0].spec.camera_entity == "camera.dahuapoolcam_main"
    assert decisions[0].spec.source == "override"


def test_build_decisions_motion_override_clobbers_ai_pick():
    cams = [
        _cam(
            "camera.dahuapoolcam_main",
            motions=[
                "binary_sensor.dahuapoolcam_motion_alarm",
                "binary_sensor.dahuapoolcam_smart_motion_human",
            ],
        ),
    ]
    decisions = build_decisions(
        cams,
        overrides={
            "dahuapoolcam": {
                "motion_override": ["binary_sensor.dahuapoolcam_motion_alarm"],
            }
        },
    )
    assert decisions[0].spec is not None
    assert decisions[0].spec.motion_entities == ("binary_sensor.dahuapoolcam_motion_alarm",)
    assert decisions[0].spec.source == "override"


def test_build_decisions_cooldown_override():
    cams = [
        _cam("camera.dahuapoolcam_main", motions=["binary_sensor.dahuapoolcam_smart_motion_human"]),
    ]
    decisions = build_decisions(cams, overrides={"dahuapoolcam": {"cooldown_override": 30.0}})
    assert decisions[0].spec is not None
    assert decisions[0].spec.cooldown_seconds == 30.0
    assert decisions[0].spec.source == "override"


def test_build_decisions_auto_disables_when_no_usable_stream():
    """All streams unavailable → device shown but disabled with reason."""
    cams = [
        _cam("camera.broken_main", state="unavailable"),
        _cam("camera.broken_sub", state="unknown"),
    ]
    decisions = build_decisions(cams)
    assert len(decisions) == 1
    assert decisions[0].enabled is False
    assert decisions[0].spec is None
    assert decisions[0].auto_disabled_reason is not None
    assert "no usable stream" in decisions[0].auto_disabled_reason


# ─── v0.3.16: suggest_generic_motion heuristic ──────────────────────


def test_suggest_generic_motion_when_ai_picked_and_alarm_available():
    """The Dahua scenario: AI sensors picked but a generic motion_alarm
    is also discovered → UI should offer the fallback."""
    cams = [
        _cam(
            "camera.dahuapoolcam_main",
            motions=[
                "binary_sensor.dahuapoolcam_motion_alarm",
                "binary_sensor.dahuapoolcam_smart_motion_human",
                "binary_sensor.dahuapoolcam_smart_motion_vehicle",
            ],
        ),
    ]
    decisions = build_decisions(cams)
    assert decisions[0].suggest_generic_motion == ("binary_sensor.dahuapoolcam_motion_alarm")


def test_no_generic_motion_suggestion_when_user_already_overrode():
    """If the user explicitly overrode motion to non-AI sensors (e.g.
    already using motion_alarm), don't badger them with the banner."""
    cams = [
        _cam(
            "camera.dahuapoolcam_main",
            motions=[
                "binary_sensor.dahuapoolcam_motion_alarm",
                "binary_sensor.dahuapoolcam_smart_motion_human",
            ],
        ),
    ]
    decisions = build_decisions(
        cams,
        overrides={
            "dahuapoolcam": {
                "motion_override": ["binary_sensor.dahuapoolcam_motion_alarm"],
            }
        },
    )
    assert decisions[0].suggest_generic_motion is None


def test_no_generic_motion_suggestion_when_no_motion_alarm_exists():
    """If only AI sensors exist (no _motion_alarm), nothing to fall
    back to → no suggestion."""
    cams = [
        _cam(
            "camera.x_main",
            motions=["binary_sensor.x_smart_motion_human"],
        ),
    ]
    decisions = build_decisions(cams)
    assert decisions[0].suggest_generic_motion is None
