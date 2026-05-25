"""Tests for the Detector facade (model loading, routing, frame selection)."""

from __future__ import annotations

import numpy as np
import pytest
from sentihome_detector import Detection, Detector, DetectorConfig


def _zero_frame(h: int = 480, w: int = 640) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_default_config_enables_yolo_and_face_detection() -> None:
    detector = Detector()
    enabled = detector.enabled_models
    assert "yolo" in enabled
    assert "face_detector" in enabled


def test_config_can_disable_yolo() -> None:
    detector = Detector(DetectorConfig(yolo=False, face_detection=False))
    assert "yolo" not in detector.enabled_models
    assert "face_detector" not in detector.enabled_models


def test_enrich_returns_empty_when_all_disabled() -> None:
    detector = Detector(DetectorConfig(yolo=False, face_detection=False))
    result = detector.enrich(_zero_frame())
    assert result.detections == []
    assert result.faces == []
    assert result.poses == []


def test_enrich_default_returns_empty_lists_with_stub_models() -> None:
    """Stubs return [] until real models are wired; facade composes correctly."""
    detector = Detector()
    result = detector.enrich(_zero_frame())
    assert isinstance(result.detections, list)
    assert isinstance(result.faces, list)
    assert isinstance(result.quality_flags, list)


def test_enable_face_recognition_adds_model() -> None:
    detector = Detector(DetectorConfig(face_recognition=True))
    assert "face_recognizer" in detector.enabled_models


def test_enable_reid_and_pose_adds_models() -> None:
    detector = Detector(DetectorConfig(body_reid=True, pose=True))
    assert "body_reid" in detector.enabled_models
    assert "pose" in detector.enabled_models


def test_select_best_frames_returns_first_n_in_stub() -> None:
    detector = Detector()
    frames = [_zero_frame() for _ in range(20)]
    indices = detector.select_best_frames(frames, target_count=5)
    assert indices == [0, 1, 2, 3, 4]


def test_select_best_frames_caps_at_input_size() -> None:
    detector = Detector()
    frames = [_zero_frame() for _ in range(3)]
    indices = detector.select_best_frames(frames, target_count=8)
    assert indices == [0, 1, 2]


def test_detection_dataclass_is_frozen() -> None:
    d = Detection(class_name="person", confidence=0.9, bbox=(0, 0, 100, 100))
    with pytest.raises(AttributeError):
        d.confidence = 0.5  # type: ignore[misc]
