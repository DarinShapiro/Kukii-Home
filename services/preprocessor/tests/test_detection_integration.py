"""Integration test that actually loads YOLO and runs inference.

Verifies the path end-to-end: real model load, real inference, real
DetectionTag output shape. Skipped when ultralytics isn't importable
so unit-test runs on bare environments stay fast.

Marked ``slow`` so PR CI excludes by default; nightly + on-demand
runs pick it up. On CPU the first run takes ~10s (model download +
torch warmup); subsequent runs ~1-2s per inference. CUDA hosts run
~30ms.
"""

from __future__ import annotations

import importlib.util

import cv2
import numpy as np
import pytest


def _ultralytics_available() -> bool:
    return importlib.util.find_spec("ultralytics") is not None


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _ultralytics_available(),
        reason="ultralytics not installed; pip install ultralytics to run.",
    ),
]


def _person_silhouette_jpeg(width: int = 640, height: int = 480) -> bytes:
    """Draw a vertical bar shape that vaguely resembles a person
    silhouette. We don't actually expect YOLO to detect it as a
    person (it's not a real photo) — the test verifies the call
    path runs end-to-end without raising and returns a tuple of
    DetectionTag (possibly empty).
    """
    img = np.full((height, width, 3), 80, dtype=np.uint8)
    # A 100x300 darker rectangle near the center.
    img[100:400, 270:370] = 200
    ok, jpeg = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    assert ok
    return jpeg.tobytes()


@pytest.mark.asyncio
async def test_real_yolo_detect_returns_tuple_of_detection_tags():
    """Smoke test: model loads, inference runs, output shape is
    compatible with our DetectionTag contract."""
    from kukiihome_preprocessor.pipelines.detection import (
        DetectionConfig,
        YOLODetector,
    )
    from kukiihome_shared.preprocessor import DetectionTag

    detector = YOLODetector(
        DetectionConfig(
            weights="yolo11n.pt",  # smallest model — fast first download
            confidence_min=0.1,
        )
    )
    tags = await detector.detect(_person_silhouette_jpeg(), frame_ts=42.0)
    # Whatever YOLO finds (likely nothing on a synthetic shape, but
    # could be a low-confidence false positive), the return must be
    # a tuple of DetectionTag — that's the contract.
    assert isinstance(tags, tuple)
    for tag in tags:
        assert isinstance(tag, DetectionTag)
        assert tag.frame_ts == 42.0
        assert 0.0 <= tag.confidence <= 1.0
        x1, y1, x2, y2 = tag.bbox
        assert 0.0 <= x1 <= 1.0 and 0.0 <= y1 <= 1.0
        assert 0.0 <= x2 <= 1.0 and 0.0 <= y2 <= 1.0
        assert x1 <= x2 and y1 <= y2


@pytest.mark.asyncio
async def test_real_yolo_batch_processes_multiple_frames():
    """Batch path: detect_batch over 3 frames returns DetectionTags
    whose frame_ts values come from our input list."""
    from kukiihome_preprocessor.pipelines.detection import YOLODetector

    detector = YOLODetector()
    frames = [
        (_person_silhouette_jpeg(), 10.0),
        (_person_silhouette_jpeg(), 11.0),
        (_person_silhouette_jpeg(), 12.0),
    ]
    tags = await detector.detect_batch(frames)
    assert isinstance(tags, tuple)
    # If anything was detected, its frame_ts must be one of the
    # values we handed in.
    for tag in tags:
        assert tag.frame_ts in {10.0, 11.0, 12.0}


@pytest.mark.asyncio
async def test_real_yolo_warmup_does_not_raise():
    """warmup() should load the model without side effects beyond
    the load itself."""
    from kukiihome_preprocessor.pipelines.detection import YOLODetector

    detector = YOLODetector()
    await detector.warmup()
    # Model is loaded; subsequent detect() calls go straight to
    # inference.
    assert detector._model is not None
