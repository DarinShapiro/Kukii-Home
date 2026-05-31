"""Tests for the triage worker — dedup, scoring, load shedding."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from kukiihome_core.triage import (
    SAFETY_BYPASS_EVENT_TYPES,
    BackpressureSignal,
    DedupCache,
    Tier,
    score_event,
)
from kukiihome_shared.generated.events.trigger_event import (
    EventType,
    OnCameraAiOpinion,
    PrivacyTier,
    Source,
    TriggerEvent,
)


def _make_event(
    *,
    event_id: str = "evt_1",
    event_type: EventType | None = None,
    confidence: float | None = None,
    label: str | None = None,
    camera_id: str = "front_door",
) -> TriggerEvent:
    opinion = None
    if confidence is not None or label is not None:
        opinion = OnCameraAiOpinion(label=label, confidence=confidence)
    return TriggerEvent(
        event_id=event_id,
        source=Source.adapter_rtsp_direct,
        timestamp=datetime.now(UTC),
        camera_id=camera_id,
        event_type=event_type,
        on_camera_ai_opinion=opinion,
        privacy_tier=PrivacyTier.cloud_eligible,
    )


# ─────────────────────────────────────────────────────────────────────
# DedupCache
# ─────────────────────────────────────────────────────────────────────


def test_dedup_first_seen_returns_false() -> None:
    cache = DedupCache(window_seconds=10)
    assert cache.is_dup("abc") is False


def test_dedup_second_within_window_returns_true() -> None:
    cache = DedupCache(window_seconds=10)
    cache.is_dup("abc")
    assert cache.is_dup("abc") is True


def test_dedup_outside_window_returns_false() -> None:
    cache = DedupCache(window_seconds=1)
    cache.is_dup("abc", now=100.0)
    assert cache.is_dup("abc", now=102.0) is False


def test_dedup_evicts_old_entries() -> None:
    cache = DedupCache(window_seconds=1, max_entries=2)
    cache.is_dup("a", now=100.0)
    cache.is_dup("b", now=100.5)
    cache.is_dup("c", now=101.0)  # evicts "a"
    assert cache.is_dup("a", now=101.5) is False  # "a" should be gone


# ─────────────────────────────────────────────────────────────────────
# score_event
# ─────────────────────────────────────────────────────────────────────


def test_score_doorbell_is_urgent() -> None:
    event = _make_event(event_type=EventType.doorbell)
    assert score_event(event) == Tier.VLM_URGENT


def test_score_package_is_urgent() -> None:
    event = _make_event(event_type=EventType.package)
    assert score_event(event) == Tier.VLM_URGENT


def test_score_high_confidence_person_is_urgent() -> None:
    event = _make_event(event_type=EventType.person, label="person", confidence=0.92)
    assert score_event(event) == Tier.VLM_URGENT


def test_score_low_confidence_person_is_normal() -> None:
    event = _make_event(event_type=EventType.person, label="person", confidence=0.5)
    assert score_event(event) == Tier.VLM_NORMAL


def test_score_vehicle_is_normal() -> None:
    event = _make_event(event_type=EventType.vehicle)
    assert score_event(event) == Tier.VLM_NORMAL


def test_score_animal_is_background() -> None:
    event = _make_event(event_type=EventType.animal)
    assert score_event(event) == Tier.VLM_BACKGROUND


def test_score_motion_no_classification_is_background() -> None:
    event = _make_event(event_type=EventType.motion)
    assert score_event(event) == Tier.VLM_BACKGROUND


def test_safety_event_types_constant_includes_basics() -> None:
    assert "smoke_alarm" in SAFETY_BYPASS_EVENT_TYPES
    assert "co_alarm" in SAFETY_BYPASS_EVENT_TYPES


# ─────────────────────────────────────────────────────────────────────
# BackpressureSignal — load shedding policy
# ─────────────────────────────────────────────────────────────────────


def test_backpressure_passes_through_when_below_thresholds() -> None:
    bp = BackpressureSignal(urgent_threshold=10, normal_threshold=50, background_threshold=200)
    bp.observe(Tier.VLM_URGENT, 0)
    assert bp.shed(Tier.VLM_URGENT) == Tier.VLM_URGENT


def test_backpressure_downgrades_urgent_to_normal_when_urgent_full() -> None:
    bp = BackpressureSignal(urgent_threshold=10, normal_threshold=50, background_threshold=200)
    bp.observe(Tier.VLM_URGENT, 11)
    bp.observe(Tier.VLM_NORMAL, 0)
    assert bp.shed(Tier.VLM_URGENT) == Tier.VLM_NORMAL


def test_backpressure_downgrades_normal_to_background_when_normal_full() -> None:
    bp = BackpressureSignal(urgent_threshold=10, normal_threshold=50, background_threshold=200)
    bp.observe(Tier.VLM_NORMAL, 51)
    assert bp.shed(Tier.VLM_NORMAL) == Tier.VLM_BACKGROUND


def test_backpressure_cascades_through_all_tiers() -> None:
    bp = BackpressureSignal(urgent_threshold=10, normal_threshold=50, background_threshold=200)
    bp.observe(Tier.VLM_URGENT, 99)
    bp.observe(Tier.VLM_NORMAL, 99)
    bp.observe(Tier.VLM_BACKGROUND, 0)
    assert bp.shed(Tier.VLM_URGENT) == Tier.VLM_BACKGROUND


def test_backpressure_drops_when_all_tiers_full() -> None:
    bp = BackpressureSignal(urgent_threshold=10, normal_threshold=50, background_threshold=200)
    bp.observe(Tier.VLM_URGENT, 99)
    bp.observe(Tier.VLM_NORMAL, 99)
    bp.observe(Tier.VLM_BACKGROUND, 999)
    assert bp.shed(Tier.VLM_URGENT) is None


def test_backpressure_never_sheds_sensor_bypass() -> None:
    bp = BackpressureSignal()
    # Even though all VLM tiers are full, safety bypass goes through.
    bp.observe(Tier.VLM_URGENT, 99)
    bp.observe(Tier.VLM_NORMAL, 99)
    bp.observe(Tier.VLM_BACKGROUND, 999)
    assert bp.shed(Tier.SENSOR_BYPASS) == Tier.SENSOR_BYPASS


# ─────────────────────────────────────────────────────────────────────
# Triage handler (integration with a fake Bus)
# ─────────────────────────────────────────────────────────────────────


class _FakeBus:
    """Test double for Bus that records publishes."""

    def __init__(self) -> None:
        self.published: list[tuple[str, TriggerEvent]] = []

    async def publish(self, subject: str, message, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.published.append((subject, message))


@pytest.mark.asyncio
async def test_triage_publishes_doorbell_to_urgent() -> None:
    from kukiihome_core.triage import Triage

    bus = _FakeBus()
    triage = Triage(bus=bus)  # type: ignore[arg-type]
    event = _make_event(event_id="d1", event_type=EventType.doorbell)
    await triage.handle(event)
    assert bus.published == [(Tier.VLM_URGENT, event)]


@pytest.mark.asyncio
async def test_triage_dedupes_within_window() -> None:
    from kukiihome_core.triage import Triage

    bus = _FakeBus()
    triage = Triage(bus=bus, dedup_window_seconds=10)  # type: ignore[arg-type]
    e1 = _make_event(event_id="x1", event_type=EventType.person, label="person", confidence=0.9)
    e2 = _make_event(event_id="x2", event_type=EventType.person, label="person", confidence=0.9)
    await triage.handle(e1)
    await triage.handle(e2)
    assert len(bus.published) == 1


@pytest.mark.asyncio
async def test_triage_downgrades_under_backpressure() -> None:
    from kukiihome_core.triage import Triage

    bus = _FakeBus()
    triage = Triage(bus=bus)  # type: ignore[arg-type]
    triage.backpressure.observe(Tier.VLM_URGENT, 99)
    event = _make_event(event_id="d2", event_type=EventType.doorbell)
    await triage.handle(event)
    assert bus.published == [(Tier.VLM_NORMAL, event)]


@pytest.mark.asyncio
async def test_triage_drops_when_all_full() -> None:
    from kukiihome_core.triage import Triage

    bus = _FakeBus()
    triage = Triage(bus=bus)  # type: ignore[arg-type]
    triage.backpressure.observe(Tier.VLM_URGENT, 99)
    triage.backpressure.observe(Tier.VLM_NORMAL, 99)
    triage.backpressure.observe(Tier.VLM_BACKGROUND, 999)
    event = _make_event(event_id="d3", event_type=EventType.doorbell)
    await triage.handle(event)
    assert bus.published == []
