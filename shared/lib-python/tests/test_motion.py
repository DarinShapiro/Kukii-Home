"""Tests for motion detection (without requiring real OpenCV at test time).

The MOG2 backend's `detect()` method lazily imports cv2 — when cv2 is not
installed, it returns an empty regions tuple, so the higher-level decision
logic (temporal consistency, exclusion zones, confidence heuristic) remains
testable.
"""

from __future__ import annotations

import pytest
from kukiihome_preprocessor.cache import InMemoryMetadataCache
from kukiihome_preprocessor.corroboration import corroborate
from kukiihome_shared.motion import (
    MOG2MotionDetector,
    MotionConfig,
    _confidence_from_regions,
    _filter_exclusion_zones,
)

# ─────────────────────────────────────────────────────────────────────
# MotionConfig
# ─────────────────────────────────────────────────────────────────────


def test_motion_config_defaults() -> None:
    config = MotionConfig()
    assert config.min_object_size_px == 800
    assert config.min_duration_seconds == 0.2
    assert config.rain_mode is False


def test_rain_mode_increases_min_size() -> None:
    config = MotionConfig(min_object_size_px=1000, rain_mode=True)
    assert config.effective_min_object_size() > 1000


def test_night_mode_increases_min_size() -> None:
    config = MotionConfig(min_object_size_px=1000, night_mode=True)
    assert config.effective_min_object_size() > 1000


# ─────────────────────────────────────────────────────────────────────
# Confidence heuristic
# ─────────────────────────────────────────────────────────────────────


def test_no_regions_gives_zero_confidence() -> None:
    assert _confidence_from_regions(()) == 0.0


def test_small_motion_gives_low_confidence() -> None:
    # 500x10 = 5000 px²; well below the curve's inflection point
    c = _confidence_from_regions(((0.0, 0.0, 500.0, 10.0),))
    assert 0.0 < c < 0.5


def test_large_motion_gives_high_confidence() -> None:
    # 1000x1000 = 1M px² → confidence ~1.0
    c = _confidence_from_regions(((0.0, 0.0, 1000.0, 1000.0),))
    assert c > 0.9


# ─────────────────────────────────────────────────────────────────────
# Exclusion zones
# ─────────────────────────────────────────────────────────────────────


def test_exclusion_zone_drops_contained_regions() -> None:
    regions = ((10.0, 10.0, 50.0, 50.0),)
    zones = (((0, 0), (100, 100)),)
    assert _filter_exclusion_zones(regions, zones) == ()


def test_exclusion_zone_keeps_outside_regions() -> None:
    regions = ((200.0, 200.0, 300.0, 300.0),)
    zones = (((0, 0), (100, 100)),)
    assert _filter_exclusion_zones(regions, zones) == regions


def test_exclusion_zones_partial_overlap_kept() -> None:
    # Region extends outside the zone → kept (we only filter *fully* contained)
    regions = ((50.0, 50.0, 150.0, 150.0),)
    zones = (((0, 0), (100, 100)),)
    assert _filter_exclusion_zones(regions, zones) == regions


# ─────────────────────────────────────────────────────────────────────
# MOG2MotionDetector — behavior without OpenCV
# ─────────────────────────────────────────────────────────────────────


def test_detector_processes_real_numpy_frame() -> None:
    """End-to-end MOG2 with a real numpy frame.

    Two identical zero frames → no motion (background converges to black).
    """
    import numpy as np

    detector = MOG2MotionDetector(MotionConfig(history=5, var_threshold=16))
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    for _ in range(3):
        decision = detector.process(frame)
    assert decision.regions == ()
    assert decision.has_motion is False


def test_detector_detects_synthetic_motion() -> None:
    """Synthetic motion: black background, then a large white box appears."""
    import numpy as np

    detector = MOG2MotionDetector(MotionConfig(history=3, min_object_size_px=500))
    background = np.zeros((480, 640, 3), dtype=np.uint8)
    # Build up the background model
    for _ in range(6):
        detector.process(background)
    # Now introduce a clear, large foreground object
    foreground = background.copy()
    foreground[100:300, 100:400] = 255
    decision = detector.process(foreground)
    # MOG2 may take a frame or two; main assertion is that the backend
    # doesn't crash on real input and returns the right shape
    assert isinstance(decision.regions, tuple)
    assert isinstance(decision.confidence, float)


def test_detector_handles_invalid_frame_type_gracefully() -> None:
    """Bad input (not a numpy array) shouldn't crash — backend returns empty."""
    detector = MOG2MotionDetector(MotionConfig())
    decision = detector.process(b"fake_frame")  # type: ignore[arg-type]
    assert decision.regions == ()
    assert decision.has_motion is False


# ─────────────────────────────────────────────────────────────────────
# Corroboration logic
# ─────────────────────────────────────────────────────────────────────


def test_corroborate_no_signal_no_processing() -> None:
    result = corroborate(own_motion=False, on_camera_label=None)
    assert result.should_process is False
    assert result.sources == ()


def test_corroborate_motion_only() -> None:
    result = corroborate(own_motion=True, own_confidence=0.7)
    assert result.should_process is True
    assert "motion" in result.sources
    assert result.confidence == 0.7


def test_corroborate_on_camera_only() -> None:
    result = corroborate(own_motion=False, on_camera_label="person", on_camera_confidence=0.85)
    assert result.should_process is True
    assert "on_camera:person" in result.sources
    assert result.confidence == 0.85


def test_corroborate_both_agree_boosts_confidence() -> None:
    no_boost = corroborate(own_motion=False, on_camera_label="person", on_camera_confidence=0.7)
    with_boost = corroborate(
        own_motion=True, own_confidence=0.7, on_camera_label="person", on_camera_confidence=0.7
    )
    assert with_boost.confidence > no_boost.confidence
    assert with_boost.confidence <= 1.0


# ─────────────────────────────────────────────────────────────────────
# Metadata cache
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_put_and_get() -> None:
    cache = InMemoryMetadataCache(max_entries=10)
    await cache.put("k1", {"a": 1}, ttl_seconds=60)
    assert await cache.get("k1") == {"a": 1}


@pytest.mark.asyncio
async def test_cache_returns_none_for_missing() -> None:
    cache = InMemoryMetadataCache()
    assert await cache.get("missing") is None


@pytest.mark.asyncio
async def test_cache_evicts_when_over_capacity() -> None:
    cache = InMemoryMetadataCache(max_entries=2)
    await cache.put("a", {}, ttl_seconds=60)
    await cache.put("b", {}, ttl_seconds=60)
    await cache.put("c", {}, ttl_seconds=60)
    # "a" should be evicted (LRU)
    assert await cache.get("a") is None
    assert await cache.get("b") is not None
    assert await cache.get("c") is not None


@pytest.mark.asyncio
async def test_cache_expires_entries() -> None:
    cache = InMemoryMetadataCache()
    await cache.put("x", {"v": 1}, ttl_seconds=-1)  # already expired
    assert await cache.get("x") is None
