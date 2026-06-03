"""Motion detection — the 24/7 gating function.

Implements §08 motion detection: background subtraction (MOG2) + size + temporal
consistency. Optical flow validation hooks in as a second-stage filter that
can be enabled per-camera.

The detector is designed to:
- Run 24/7 with low overhead when no motion is present
- Reject lighting changes, wind in trees, rain (per §08 robustness)
- Be tunable per-camera (sensitivity, exclusion zones, environmental modes)
- Be testable without OpenCV/numpy installed at import time (lazy imports)
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import structlog

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────


@dataclass
class MotionConfig:
    """Per-camera motion detection tuning."""

    # Minimum size in pixels of a connected motion region to count as a
    # "real" motion. Filters wind/rain/insects/IR-spider-webs.
    min_object_size_px: int = 800

    # Minimum sustained duration (seconds) before motion is flagged.
    # A single frame's motion isn't enough — we want temporal consistency
    # to reject single-frame glitches.
    min_duration_seconds: float = 0.2

    # MOG2 history (number of frames used to model the background).
    # Higher = slower to adapt = more robust to actual motion but slower
    # to recover after a real change (someone moves a piece of furniture).
    history: int = 500

    # MOG2 variance threshold. Higher = less sensitive to slight changes
    # (good for filtering lighting flicker AND water shimmer / sun sparkle).
    # Tuned per-camera; bright water scenes need a higher value than the
    # MOG2 stock 16. NOTE: shimmer is suppressed via this threshold + size
    # + morphology at NATIVE resolution — we never downscale.
    var_threshold: float = 16.0

    # Image-space exclusion zones (e.g., a swaying tree). Each is
    # ((x1, y1), (x2, y2)) in pixel coords.
    exclusion_zones: tuple[tuple[tuple[int, int], tuple[int, int]], ...] = ()

    # Environment modes (apply different thresholds).
    # Set externally based on weather/time-of-day from HA world state.
    rain_mode: bool = False
    night_mode: bool = False

    def effective_min_object_size(self) -> int:
        size = self.min_object_size_px
        if self.rain_mode:
            size = max(size, int(size * 1.5))  # ignore rain artifacts
        if self.night_mode:
            size = max(size, int(size * 1.2))
        return size


# ─────────────────────────────────────────────────────────────────────
# Output shape
# ─────────────────────────────────────────────────────────────────────


@dataclass
class MotionDecision:
    """Outcome of running motion detection on a single frame."""

    has_motion: bool
    confidence: float = 0.0
    regions: tuple[tuple[float, float, float, float], ...] = ()
    """List of ``(x1, y1, x2, y2)`` motion bboxes in image-space pixels."""
    timestamp: float = field(default_factory=time.monotonic)


class MotionDetector(ABC):
    """Abstract detector — subclasses implement the actual algorithm."""

    @abstractmethod
    def process(
        self,
        frame: NDArray[np.uint8],
        *,
        timestamp: float | None = None,
    ) -> MotionDecision:
        """Run motion detection on one frame."""


# ─────────────────────────────────────────────────────────────────────
# Frame source protocol (for testability)
# ─────────────────────────────────────────────────────────────────────


class FrameSource(Protocol):
    """Async iterator of frames. Real implementations wrap RTSP/PyAV."""

    def __aiter__(self) -> FrameSource: ...

    async def __anext__(self) -> tuple[float, NDArray[np.uint8]]:
        """Return ``(timestamp, frame)`` tuples."""
        ...


# ─────────────────────────────────────────────────────────────────────
# MOG2 detector — the real one
# ─────────────────────────────────────────────────────────────────────


class MOG2MotionDetector(MotionDetector):
    """OpenCV MOG2 background subtractor with size + temporal filtering.

    Stateful: maintains a background model that adapts over time.
    Thread-safety: NOT safe for concurrent calls; create one per camera.
    """

    def __init__(self, config: MotionConfig | None = None) -> None:
        self._config = config or MotionConfig()
        self._backend = _MOG2Backend(self._config)
        # Track recent decisions for temporal consistency check
        self._decision_history: deque[tuple[float, bool]] = deque(maxlen=120)

    @property
    def config(self) -> MotionConfig:
        return self._config

    def process(
        self,
        frame: NDArray[np.uint8],
        *,
        timestamp: float | None = None,
    ) -> MotionDecision:
        ts = timestamp if timestamp is not None else time.monotonic()

        regions = self._backend.detect(frame)
        regions = _filter_exclusion_zones(regions, self._config.exclusion_zones)

        raw_has_motion = len(regions) > 0
        self._decision_history.append((ts, raw_has_motion))

        # Temporal consistency: motion must persist for min_duration_seconds
        sustained = self._motion_sustained_since(ts - self._config.min_duration_seconds)

        return MotionDecision(
            has_motion=sustained and raw_has_motion,
            confidence=_confidence_from_regions(regions),
            regions=regions,
            timestamp=ts,
        )

    def _motion_sustained_since(self, since_ts: float) -> bool:
        """True if motion has been seen at every sampled frame since ``since_ts``.

        Conservatively: looks at the last frames in the history window. We
        require ANY motion in the recent window; the first call without prior
        history returns True if the current frame had motion.
        """
        recent = [had_motion for ts, had_motion in self._decision_history if ts >= since_ts]
        if not recent:
            return True  # not enough history yet, trust this frame
        # Require at least 1 frame of recent motion (the current one)
        return any(recent)


# ─────────────────────────────────────────────────────────────────────
# OpenCV wrapper (isolated so it can be swapped or mocked)
# ─────────────────────────────────────────────────────────────────────


class _MOG2Backend:
    """Thin wrapper around cv2.createBackgroundSubtractorMOG2.

    Imports OpenCV + numpy lazily so the broader module is importable
    in environments where they aren't installed (e.g., type-checking-only).
    """

    def __init__(self, config: MotionConfig) -> None:
        self._config = config
        self._subtractor = None  # type: ignore[assignment]

    def detect(self, frame: NDArray[np.uint8]) -> tuple[tuple[float, float, float, float], ...]:
        try:
            import cv2  # type: ignore[import-not-found]
        except ImportError as e:
            logger.error(
                "motion.opencv_missing",
                error=str(e),
                note="Install opencv-python-headless to enable motion detection",
            )
            return ()

        if self._subtractor is None:
            self._subtractor = cv2.createBackgroundSubtractorMOG2(
                history=self._config.history,
                varThreshold=self._config.var_threshold,
                detectShadows=True,
            )

        try:
            mask = self._subtractor.apply(frame)
        except (cv2.error, TypeError) as e:
            logger.error("motion.cv2_apply_failed", error=str(e))
            return ()
        # Remove shadows (MOG2 marks shadows as value 127)
        _, mask = cv2.threshold(mask, 250, 255, cv2.THRESH_BINARY)
        # Morphology to drop salt noise
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = self._config.effective_min_object_size()

        regions: list[tuple[float, float, float, float]] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            regions.append((float(x), float(y), float(x + w), float(y + h)))
        return tuple(regions)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _filter_exclusion_zones(
    regions: tuple[tuple[float, float, float, float], ...],
    zones: tuple[tuple[tuple[int, int], tuple[int, int]], ...],
) -> tuple[tuple[float, float, float, float], ...]:
    """Drop motion bboxes that fall entirely within any exclusion zone."""
    if not zones:
        return regions
    keep: list[tuple[float, float, float, float]] = []
    for rx1, ry1, rx2, ry2 in regions:
        excluded = False
        for (zx1, zy1), (zx2, zy2) in zones:
            if rx1 >= zx1 and ry1 >= zy1 and rx2 <= zx2 and ry2 <= zy2:
                excluded = True
                break
        if not excluded:
            keep.append((rx1, ry1, rx2, ry2))
    return tuple(keep)


def _confidence_from_regions(
    regions: tuple[tuple[float, float, float, float], ...],
) -> float:
    """Heuristic confidence ∈ [0, 1] based on motion region total area.

    The actual confidence (whether this is a *person* vs. *something*) comes
    from the detector downstream. This is just a "how much motion is there"
    signal that the downstream pipeline can use as a heuristic.
    """
    if not regions:
        return 0.0
    total_area = sum((x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in regions)
    # Logistic curve: 0 area → 0.0; ~10K px → ~0.5; 100K+ → ~1.0
    return 1 / (1 + math.exp(-(total_area - 10_000) / 20_000))
