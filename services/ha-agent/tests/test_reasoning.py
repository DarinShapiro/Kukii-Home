"""Tests for the stub reasoner — the VLM stand-in that gates alerts."""

from __future__ import annotations

import pytest
from sentihome_ha_agent.reasoning import (
    ReasoningPolicy,
    StubReasoner,
    should_notify,
)
from sentihome_shared.generated.events.vlm_response import Criticality
from sentihome_shared.preprocessor import DetectionTag, FrameWindow, IdentifiedEntity

pytestmark = pytest.mark.asyncio


def _alert(**extra) -> dict:
    base = {
        "alert_id": "evt1",
        "camera_id": "poolcam",
        "camera_name": "Pool Cam",
        "sensor_classification": "",
        "confidence": 0.85,
    }
    base.update(extra)
    return base


def _fw(*, detections=(), identified=()) -> FrameWindow:
    return FrameWindow(
        camera_id="poolcam",
        ts_start=0.0,
        ts_end=1.0,
        detections=tuple(detections),
        identified_entities=tuple(identified),
    )


def _person_det(conf=0.9) -> DetectionTag:
    return DetectionTag(kind="person", confidence=conf, bbox=(0, 0, 1, 1), frame_ts=0.5)


def _known_person(name="Alice", idconf=0.95) -> IdentifiedEntity:
    return IdentifiedEntity(
        frame_ts=0.5,
        kind="person",
        actor_id="actor_alice",
        actor_name=name,
        detection_confidence=0.9,
        identity_confidence=idconf,
        identity_method="face_arcface",
        bbox=(0, 0, 1, 1),
    )


def _animal_det() -> DetectionTag:
    return DetectionTag(kind="dog", confidence=0.8, bbox=(0, 0, 1, 1), frame_ts=0.5)


# ─── HA-classification fallback (no preprocessor) ────────────────────


async def test_ha_person_classification_alerts():
    r = StubReasoner()
    d = await r.reason(_alert(sensor_classification="person"), None)
    assert d.criticality == Criticality.alert
    assert should_notify(d)
    assert d.backend == "stub_heuristic"
    assert "Pool Cam" in (d.explanation or "")


async def test_ha_unclassified_motion_dismissed_by_default():
    """The pool-ripple case: generic motion with no class → info →
    silent. This is the flood-suppression default."""
    r = StubReasoner()
    d = await r.reason(_alert(sensor_classification="motion"), None)
    assert d.criticality == Criticality.info
    assert not should_notify(d)


async def test_empty_classification_treated_as_unclassified():
    r = StubReasoner()
    d = await r.reason(_alert(sensor_classification=""), None)
    assert d.criticality == Criticality.info
    assert not should_notify(d)


async def test_unclassified_motion_can_be_opted_into_alerting():
    r = StubReasoner(policy=ReasoningPolicy(alert_on_unclassified_motion=True))
    d = await r.reason(_alert(sensor_classification="motion"), None)
    assert d.criticality == Criticality.warning
    assert should_notify(d)


async def test_ha_animal_classification_dismissed_by_default():
    r = StubReasoner()
    d = await r.reason(_alert(sensor_classification="animal"), None)
    assert d.criticality == Criticality.info
    assert not should_notify(d)


async def test_ha_vehicle_classification_warns_by_default():
    r = StubReasoner()
    d = await r.reason(_alert(sensor_classification="vehicle"), None)
    assert d.criticality == Criticality.warning
    assert should_notify(d)


async def test_vehicle_can_be_dismissed_by_policy():
    r = StubReasoner(policy=ReasoningPolicy(vehicles_warrant_alert=False))
    d = await r.reason(_alert(sensor_classification="vehicle"), None)
    assert d.criticality == Criticality.info
    assert not should_notify(d)


# ─── preprocessor evidence (rich path) ───────────────────────────────


async def test_unknown_person_detection_alerts():
    r = StubReasoner()
    d = await r.reason(_alert(), _fw(detections=[_person_det()]))
    assert d.criticality == Criticality.alert
    assert should_notify(d)
    assert d.confidence == pytest.approx(0.9, abs=0.01)


async def test_known_person_is_silent():
    """A recognized resident is 'boring' → info → no notification."""
    r = StubReasoner()
    d = await r.reason(_alert(), _fw(identified=[_known_person("Alice")]))
    assert d.criticality == Criticality.info
    assert not should_notify(d)
    assert d.identified_actors and d.identified_actors[0].name == "Alice"
    assert "Alice" in (d.explanation or "")


async def test_low_confidence_identity_counts_as_unknown():
    """A person matched below the known-actor threshold is treated as
    unknown → alert, not silently dismissed as 'known'."""
    r = StubReasoner()
    d = await r.reason(_alert(), _fw(identified=[_known_person("Alice", idconf=0.3)]))
    assert d.criticality == Criticality.alert
    assert should_notify(d)


async def test_unknown_person_dominates_known_person():
    """A recognized resident plus a stranger: two person boxes but only
    one identified → one surplus unknown → alert (you still want to know
    about the stranger)."""
    r = StubReasoner()
    fw = _fw(
        identified=[_known_person("Alice")],
        detections=[_person_det(), _person_det()],  # 2 person boxes, 1 identified
    )
    d = await r.reason(_alert(), fw)
    assert d.criticality == Criticality.alert


async def test_animal_only_evidence_is_silent():
    r = StubReasoner()
    d = await r.reason(_alert(), _fw(detections=[_animal_det()]))
    assert d.criticality == Criticality.info
    assert not should_notify(d)


async def test_preprocessor_evidence_preferred_over_ha_class():
    """Even if HA said 'person', a preprocessor window showing only a
    dog should dismiss — richer evidence wins."""
    r = StubReasoner()
    d = await r.reason(_alert(sensor_classification="person"), _fw(detections=[_animal_det()]))
    assert d.criticality == Criticality.info
    assert not should_notify(d)


async def test_response_is_valid_vlm_contract():
    """The stub emits the real VLMResponse shape so the router drops in."""
    r = StubReasoner()
    d = await r.reason(_alert(sensor_classification="person"), None)
    # round-trips through the schema (extra=forbid would reject junk)
    assert d.model_dump()["criticality"] == "alert"
    assert d.request_id == "evt1"
    assert 0.0 <= d.confidence <= 1.0
