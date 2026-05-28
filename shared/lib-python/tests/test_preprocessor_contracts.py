"""Roundtrip + invariants for preprocessor wire contracts.

Pydantic does the heavy lifting; these tests pin the deliberately-
chosen behaviors:

* Strict ``extra="forbid"`` — surfaces typos + dropped fields.
* ``schema_version`` defaults to ``"v1"`` so producers don't have
  to remember to set it.
* :class:`FrameWindow` roundtrips through JSON without loss.
* Subject strings are stable — they're an interface, not an
  implementation detail.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sentihome_shared.preprocessor import (
    ALL_ACTOR_SUBJECTS,
    SUBJECT_ACTOR_DEACTIVATED,
    SUBJECT_ACTOR_ENROLLED,
    SUBJECT_ACTOR_UPDATED,
    ActorEnrollmentEvent,
    ActorMatch,
    DetectionTag,
    FrameRef,
    FrameWindow,
    KnobAdjustment,
    PreprocessorStatus,
)

# ─── FrameWindow ─────────────────────────────────────────────────────


def test_frame_window_roundtrips_through_json():
    original = FrameWindow(
        camera_id="front_porch",
        ts_start=1_700_000_000.0,
        ts_end=1_700_000_010.0,
        preprocessor_node_id="node-A",
        frames=(
            FrameRef(
                ts=1_700_000_000.0,
                uri="s3://frames/front_porch/0.jpg",
                width=1920,
                height=1080,
                quality_score=0.82,
            ),
            FrameRef(
                ts=1_700_000_000.5,
                uri="s3://frames/front_porch/1.jpg",
            ),
        ),
        detections=(
            DetectionTag(
                kind="person",
                confidence=0.92,
                bbox=(0.1, 0.2, 0.4, 0.8),
                frame_ts=1_700_000_000.0,
                track_id="t-7",
            ),
        ),
        actor_matches=(
            ActorMatch(
                actor_id="actor_alice",
                confidence=0.88,
                match_method="face_arcface",
                frame_ts=1_700_000_000.0,
                track_id="t-7",
            ),
        ),
        enrichment_mode="enriched",
        enrichment_latency_ms=47,
    )
    raw = json.dumps(original.model_dump(mode="json"))
    rebuilt = FrameWindow.model_validate_json(raw)
    assert rebuilt == original


def test_frame_window_defaults_schema_version_v1():
    fw = FrameWindow(camera_id="cam", ts_start=0.0, ts_end=1.0)
    assert fw.schema_version == "v1"


def test_frame_window_rejects_unknown_field():
    with pytest.raises(ValidationError):
        FrameWindow.model_validate(
            {
                "camera_id": "cam",
                "ts_start": 0.0,
                "ts_end": 1.0,
                "framez": [],  # typo — must blow up
            }
        )


def test_frame_window_enrichment_mode_is_constrained():
    """Only the two literal values; anything else rejected."""
    with pytest.raises(ValidationError):
        FrameWindow.model_validate(
            {
                "camera_id": "cam",
                "ts_start": 0.0,
                "ts_end": 1.0,
                "enrichment_mode": "deep_enriched",
            }
        )


# ─── DetectionTag ────────────────────────────────────────────────────


def test_detection_tag_clamps_confidence_to_unit_interval():
    with pytest.raises(ValidationError):
        DetectionTag(
            kind="person", confidence=1.5, bbox=(0, 0, 1, 1), frame_ts=0.0
        )
    with pytest.raises(ValidationError):
        DetectionTag(
            kind="person", confidence=-0.1, bbox=(0, 0, 1, 1), frame_ts=0.0
        )


def test_detection_tag_requires_frame_ts():
    """Per the corrected contract, detections must carry the frame
    they came from — so the caller can correlate."""
    with pytest.raises(ValidationError):
        DetectionTag.model_validate(
            {"kind": "person", "confidence": 0.5, "bbox": [0, 0, 1, 1]}
        )


# ─── ActorMatch ──────────────────────────────────────────────────────


def test_actor_match_match_method_is_enum():
    with pytest.raises(ValidationError):
        ActorMatch(
            actor_id="a",
            confidence=0.5,
            match_method="unknown_method",  # type: ignore[arg-type]
            frame_ts=0.0,
        )


# ─── ActorEnrollmentEvent ────────────────────────────────────────────


def test_actor_enrollment_with_face_embedding_roundtrips():
    ev = ActorEnrollmentEvent(
        actor_id="actor_alice",
        action="enrolled",
        name="Alice",
        role="resident",
        access_profile="full",
        face_embedding=tuple(0.1 * i for i in range(128)),
    )
    raw = json.dumps(ev.model_dump(mode="json"))
    rebuilt = ActorEnrollmentEvent.model_validate_json(raw)
    assert rebuilt == ev


def test_actor_enrollment_action_must_be_known():
    with pytest.raises(ValidationError):
        ActorEnrollmentEvent(actor_id="a", action="archived")  # type: ignore[arg-type]


def test_actor_enrollment_deactivated_can_omit_embedding():
    ev = ActorEnrollmentEvent(actor_id="a", action="deactivated")
    assert ev.face_embedding is None
    assert ev.name is None


# ─── KnobAdjustment + PreprocessorStatus ─────────────────────────────


def test_knob_adjustment_accepts_float_string_or_bool():
    KnobAdjustment(knob_id="face.match_threshold", new_value=0.62)
    KnobAdjustment(knob_id="yolo.weights", new_value="yolo11x-tuned")
    KnobAdjustment(knob_id="pet.enabled", new_value=False)


def test_preprocessor_status_roundtrips():
    s = PreprocessorStatus(
        healthy=True,
        uptime_seconds=12345.6,
        model_versions={"yolo": "11x-2.1", "face": "arcface-r100-v3"},
        cameras_active=4,
        cameras_total=5,
        frame_windows_served_total=987,
        actors_cached=8,
    )
    rebuilt = PreprocessorStatus.model_validate_json(s.model_dump_json())
    assert rebuilt == s


# ─── Subject string stability ────────────────────────────────────────


def test_canonical_subject_strings_are_stable():
    """Subject strings are an external contract — pinned by test so
    a careless rename gets caught in CI."""
    assert SUBJECT_ACTOR_ENROLLED == "sentihome.memory.actor.enrolled"
    assert SUBJECT_ACTOR_UPDATED == "sentihome.memory.actor.updated"
    assert SUBJECT_ACTOR_DEACTIVATED == "sentihome.memory.actor.deactivated"
    assert ALL_ACTOR_SUBJECTS == (
        SUBJECT_ACTOR_ENROLLED,
        SUBJECT_ACTOR_UPDATED,
        SUBJECT_ACTOR_DEACTIVATED,
    )


def test_no_preprocessor_output_subject():
    """Defensively: the removed broadcast subject must not have been
    re-introduced. Confirms the corrected architecture stays
    corrected."""
    from sentihome_shared.preprocessor import nats_subjects

    public = {name for name in dir(nats_subjects) if not name.startswith("_")}
    forbidden = {"SUBJECT_PREPROCESSOR_OUTPUT"}
    assert not (public & forbidden), (
        f"Preprocessor must not broadcast detection events — found "
        f"forbidden subject re-introduced: {public & forbidden}"
    )


# ─── CameraConfigEvent ───────────────────────────────────────────────


def test_camera_config_event_configured_roundtrips():
    from sentihome_shared.preprocessor import CameraConfigEvent

    ev = CameraConfigEvent(
        action="configured",
        camera_id="front_porch",
        stream_url="rtsp://user:pass@192.168.1.20:554/h264Preview_01_sub",
        stream_protocol="rtsp",
        vendor="reolink",
        sub_stream=True,
    )
    rebuilt = CameraConfigEvent.model_validate_json(ev.model_dump_json())
    assert rebuilt == ev


def test_camera_config_event_removed_omits_stream_url():
    from sentihome_shared.preprocessor import CameraConfigEvent

    ev = CameraConfigEvent(action="removed", camera_id="front_porch")
    assert ev.stream_url is None
    assert ev.stream_protocol is None


def test_camera_config_event_hls_refresh_lifetime():
    """HLS URLs typically carry short-lived tokens. The
    refresh_after_seconds hint tells the subscriber when to expect
    a follow-up event."""
    from sentihome_shared.preprocessor import CameraConfigEvent

    ev = CameraConfigEvent(
        action="configured",
        camera_id="cam_a",
        stream_url="http://homeassistant:8123/api/hls/abc.m3u8?token=xyz",
        stream_protocol="hls",
        refresh_after_seconds=240.0,
    )
    assert ev.refresh_after_seconds == 240.0


def test_camera_config_event_rejects_unknown_action():
    from pydantic import ValidationError
    from sentihome_shared.preprocessor import CameraConfigEvent

    with pytest.raises(ValidationError):
        CameraConfigEvent(action="disabled", camera_id="cam_a")  # type: ignore[arg-type]


def test_camera_config_event_rejects_unknown_protocol():
    from pydantic import ValidationError
    from sentihome_shared.preprocessor import CameraConfigEvent

    with pytest.raises(ValidationError):
        CameraConfigEvent(
            action="configured",
            camera_id="cam_a",
            stream_url="webrtc://example",
            stream_protocol="webrtc",  # type: ignore[arg-type]
        )


# ─── IdentifiedEntity ───────────────────────────────────────────────


def test_identified_entity_roundtrips():
    from sentihome_shared.preprocessor import IdentifiedEntity

    ent = IdentifiedEntity(
        frame_ts=1234.5,
        kind="person",
        actor_id="actor_alice",
        actor_name="Alice",
        bbox=(0.1, 0.2, 0.4, 0.8),
        detection_confidence=0.95,
        identity_confidence=0.92,
        identity_method="face_arcface",
        track_id="t-7",
    )
    rebuilt = IdentifiedEntity.model_validate_json(ent.model_dump_json())
    assert rebuilt == ent


def test_identified_entity_kind_constrained_to_4_classes():
    """package / animal / etc. don't have identity pipelines yet —
    contract refuses them so callers can't accidentally claim
    identities for classes we don't recognize."""
    from pydantic import ValidationError
    from sentihome_shared.preprocessor import IdentifiedEntity

    with pytest.raises(ValidationError):
        IdentifiedEntity(
            frame_ts=0.0,
            kind="package",  # type: ignore[arg-type]
            actor_id="x",
            actor_name="x",
            bbox=(0, 0, 1, 1),
            detection_confidence=0.5,
            identity_confidence=0.7,
            identity_method="face_arcface",
        )


def test_identified_entity_clamps_confidences():
    from pydantic import ValidationError
    from sentihome_shared.preprocessor import IdentifiedEntity

    with pytest.raises(ValidationError):
        IdentifiedEntity(
            frame_ts=0.0,
            kind="person",
            actor_id="x",
            actor_name="x",
            bbox=(0, 0, 1, 1),
            detection_confidence=1.5,  # out of range
            identity_confidence=0.7,
            identity_method="face_arcface",
        )


def test_frame_ref_annotated_uri_defaults_none():
    """Most frames have no annotation — the field should be optional
    and default to None so old callers keep working unchanged."""
    fw = FrameWindow(camera_id="c", ts_start=0.0, ts_end=1.0)
    assert fw.identified_entities == ()
    fr = FrameRef(ts=0.0, uri="x://")
    assert fr.annotated_uri is None


def test_frame_window_with_identified_entities_roundtrips():
    from sentihome_shared.preprocessor import IdentifiedEntity

    fw = FrameWindow(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=10.0,
        frames=(
            FrameRef(
                ts=1.0,
                uri="x://raw.jpg",
                annotated_uri="x://annotated.jpg",
            ),
        ),
        identified_entities=(
            IdentifiedEntity(
                frame_ts=1.0,
                kind="dog",
                actor_id="actor_rex",
                actor_name="Rex",
                bbox=(0.4, 0.4, 0.6, 0.7),
                detection_confidence=0.88,
                identity_confidence=0.79,
                identity_method="pet_dinov2",
                track_id="t-3",
            ),
        ),
    )
    rebuilt = FrameWindow.model_validate_json(fw.model_dump_json())
    assert rebuilt == fw


def test_camera_subjects_are_stable():
    from sentihome_shared.preprocessor import (
        ALL_CAMERA_SUBJECTS,
        SUBJECT_CAMERA_CONFIGURED,
        SUBJECT_CAMERA_REMOVED,
    )

    assert SUBJECT_CAMERA_CONFIGURED == "sentihome.ha.camera.configured"
    assert SUBJECT_CAMERA_REMOVED == "sentihome.ha.camera.removed"
    assert ALL_CAMERA_SUBJECTS == (
        SUBJECT_CAMERA_CONFIGURED,
        SUBJECT_CAMERA_REMOVED,
    )
