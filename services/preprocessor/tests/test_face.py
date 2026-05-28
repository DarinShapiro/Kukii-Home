"""Unit tests for the ArcFace face-recognition pipeline.

InsightFace's :class:`FaceAnalysis` is mocked so these tests run
without the model bundle download + onnxruntime install. A slow
integration test (``test_face_integration.py``) loads a real
buffalo_s model against a fixture face image — that one is the
ground-truth check that the wrapper actually matches a known face.
Here we cover:

* embedding normalization (fallback when ``normed_embedding`` is
  missing on the face object)
* cosine-similarity matching above + below threshold
* detection-confidence filtering at the InsightFace boundary
* IoU-based face-to-person association (track-id inheritance)
* ``DetectedFace -> ActorMatch`` conversion drops unmatched faces
* JPEG decode failure path
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import numpy as np
import pytest
from sentihome_preprocessor.pipelines.face import (
    DetectedFace,
    FaceConfig,
    FaceRecognizer,
    _match,
    _normalized_embedding,
    associate_face_to_person,
    detected_face_to_actor_match,
    jpeg_to_bgr,
)

# ─── Helpers ─────────────────────────────────────────────────────────


@dataclass
class _FakeFace:
    """Stand-in for InsightFace's Face dataclass — only the
    attributes we read."""

    bbox: np.ndarray
    det_score: float
    normed_embedding: np.ndarray | None = None
    embedding: np.ndarray | None = None


def _unit(v: list[float]) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(a)
    return a / n if n > 0 else a


# ─── _normalized_embedding ──────────────────────────────────────────


def test_normalized_embedding_uses_prenormed_when_present():
    pre = _unit([1.0, 2.0, 3.0])
    f = _FakeFace(
        bbox=np.array([0, 0, 1, 1]),
        det_score=0.9,
        normed_embedding=pre,
    )
    out = _normalized_embedding(f)
    np.testing.assert_allclose(out, pre, atol=1e-6)


def test_normalized_embedding_normalizes_raw_when_no_normed():
    raw = np.array([3.0, 4.0], dtype=np.float32)  # norm 5
    f = _FakeFace(bbox=np.array([0, 0, 1, 1]), det_score=0.9, embedding=raw)
    out = _normalized_embedding(f)
    np.testing.assert_allclose(out, [0.6, 0.8], atol=1e-6)


def test_normalized_embedding_handles_zero_vector():
    zero = np.zeros(4, dtype=np.float32)
    f = _FakeFace(bbox=np.array([0, 0, 1, 1]), det_score=0.9, embedding=zero)
    out = _normalized_embedding(f)
    # Returned as-is rather than crashing on /0.
    assert out.shape == (4,)


# ─── _match ──────────────────────────────────────────────────────────


def test_match_picks_highest_above_threshold():
    target = _unit([1.0, 0.0, 0.0])
    enrolled = {
        "alice": _unit([0.9, 0.1, 0.0]),  # high cosine
        "bob": _unit([0.0, 1.0, 0.0]),  # orthogonal
    }
    actor, sim = _match(target, enrolled, threshold=0.5)
    assert actor == "alice"
    assert sim > 0.9


def test_match_returns_none_below_threshold():
    target = _unit([1.0, 0.0, 0.0])
    enrolled = {"bob": _unit([0.4, 1.0, 0.0])}  # cosine ~0.37
    actor, sim = _match(target, enrolled, threshold=0.5)
    assert actor is None
    assert sim == 0.0


def test_match_empty_enrolled_returns_none():
    target = _unit([1.0, 0.0, 0.0])
    actor, sim = _match(target, {}, threshold=0.5)
    assert actor is None
    assert sim == 0.0


# ─── associate_face_to_person ───────────────────────────────────────


def test_associate_face_to_person_picks_containing_bbox():
    # Face sitting in upper-left of "alice" person; "bob" elsewhere.
    face_bbox = (0.10, 0.10, 0.20, 0.20)
    persons = [
        ("alice", (0.00, 0.00, 0.50, 0.50)),
        ("bob", (0.60, 0.60, 0.90, 0.90)),
    ]
    assert associate_face_to_person(face_bbox, persons) == "alice"


def test_associate_face_to_person_returns_none_when_no_overlap():
    face_bbox = (0.95, 0.95, 0.99, 0.99)
    persons = [("alice", (0.00, 0.00, 0.50, 0.50))]
    assert associate_face_to_person(face_bbox, persons) is None


def test_associate_face_to_person_requires_min_overlap():
    """Face mostly outside the person bbox — only 10% of its area
    falls inside. Default min_overlap=0.5 rejects."""
    face_bbox = (0.45, 0.45, 0.65, 0.65)  # quarter-in / mostly-out
    persons = [("alice", (0.00, 0.00, 0.50, 0.50))]
    # 5%x5% = 0.0025 intersect / 0.04 face area = 0.0625 < 0.5
    assert associate_face_to_person(face_bbox, persons) is None


def test_associate_face_to_person_empty_persons_returns_none():
    assert associate_face_to_person((0, 0, 1, 1), []) is None


# ─── detected_face_to_actor_match ───────────────────────────────────


def test_detected_face_to_actor_match_returns_none_for_unmatched():
    face = DetectedFace(
        bbox=(0, 0, 0.1, 0.1),
        det_confidence=0.9,
        embedding=_unit([1.0, 0.0]),
        matched_actor_id=None,
        match_confidence=0.0,
    )
    assert detected_face_to_actor_match(face, frame_ts=1.0, track_id="t1") is None


def test_detected_face_to_actor_match_builds_actor_match_for_matched():
    face = DetectedFace(
        bbox=(0, 0, 0.1, 0.1),
        det_confidence=0.9,
        embedding=_unit([1.0, 0.0]),
        matched_actor_id="alice",
        match_confidence=0.78,
    )
    match = detected_face_to_actor_match(face, frame_ts=42.5, track_id="t9")
    assert match is not None
    assert match.actor_id == "alice"
    assert match.confidence == 0.78
    assert match.match_method == "face_arcface"
    assert match.frame_ts == 42.5
    assert match.track_id == "t9"


# ─── FaceRecognizer with mocked InsightFace ─────────────────────────


@pytest.mark.asyncio
async def test_recognizer_filters_low_detection_confidence():
    """Faces below ``det_confidence_min`` are dropped before
    embedding/matching. Verifies the detector-side gate."""
    cfg = FaceConfig(det_confidence_min=0.6)
    rec = FaceRecognizer(cfg)

    mock_app = MagicMock()
    mock_app.get.return_value = [
        _FakeFace(
            bbox=np.array([10.0, 10.0, 50.0, 50.0]),
            det_score=0.3,  # below threshold
            normed_embedding=_unit([1.0, 0.0]),
        ),
        _FakeFace(
            bbox=np.array([60.0, 60.0, 100.0, 100.0]),
            det_score=0.9,  # above
            normed_embedding=_unit([1.0, 0.0]),
        ),
    ]
    rec._app = mock_app  # bypass _ensure_app

    bgr = np.zeros((200, 200, 3), dtype=np.uint8)
    faces = await rec.detect_and_match(bgr, enrolled={})
    assert len(faces) == 1
    assert faces[0].det_confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_recognizer_normalizes_bbox_to_image_coords():
    cfg = FaceConfig(det_confidence_min=0.0)
    rec = FaceRecognizer(cfg)

    mock_app = MagicMock()
    mock_app.get.return_value = [
        _FakeFace(
            bbox=np.array([50.0, 100.0, 150.0, 200.0]),
            det_score=0.9,
            normed_embedding=_unit([1.0, 0.0]),
        )
    ]
    rec._app = mock_app

    bgr = np.zeros((400, 200, 3), dtype=np.uint8)  # h=400, w=200
    faces = await rec.detect_and_match(bgr, enrolled={})
    assert len(faces) == 1
    # x normalized by 200, y by 400.
    np.testing.assert_allclose(faces[0].bbox, (0.25, 0.25, 0.75, 0.5))


@pytest.mark.asyncio
async def test_recognizer_matches_against_enrolled():
    cfg = FaceConfig(det_confidence_min=0.0, match_threshold=0.5)
    rec = FaceRecognizer(cfg)
    target_emb = _unit([1.0, 0.0, 0.0])

    mock_app = MagicMock()
    mock_app.get.return_value = [
        _FakeFace(
            bbox=np.array([10.0, 10.0, 50.0, 50.0]),
            det_score=0.9,
            normed_embedding=target_emb,
        )
    ]
    rec._app = mock_app

    enrolled = {
        "alice": target_emb,  # perfect match
        "bob": _unit([0.0, 1.0, 0.0]),
    }
    bgr = np.zeros((100, 100, 3), dtype=np.uint8)
    faces = await rec.detect_and_match(bgr, enrolled=enrolled)
    assert len(faces) == 1
    assert faces[0].matched_actor_id == "alice"
    assert faces[0].match_confidence == pytest.approx(1.0, abs=1e-5)


@pytest.mark.asyncio
async def test_recognizer_returns_unmatched_when_no_enrolled_passes_threshold():
    """Face found + embedded, but no enrolled actor is similar
    enough -> matched_actor_id is None but the DetectedFace is
    still surfaced so the caller can decide what to do."""
    cfg = FaceConfig(det_confidence_min=0.0, match_threshold=0.95)
    rec = FaceRecognizer(cfg)

    mock_app = MagicMock()
    mock_app.get.return_value = [
        _FakeFace(
            bbox=np.array([10.0, 10.0, 50.0, 50.0]),
            det_score=0.9,
            normed_embedding=_unit([1.0, 0.0, 0.0]),
        )
    ]
    rec._app = mock_app

    enrolled = {"bob": _unit([0.0, 1.0, 0.0])}  # orthogonal -> cosine 0
    bgr = np.zeros((100, 100, 3), dtype=np.uint8)
    faces = await rec.detect_and_match(bgr, enrolled=enrolled)
    assert len(faces) == 1
    assert faces[0].matched_actor_id is None
    assert faces[0].match_confidence == 0.0


@pytest.mark.asyncio
async def test_recognizer_no_faces_returns_empty_tuple():
    rec = FaceRecognizer(FaceConfig(det_confidence_min=0.0))
    mock_app = MagicMock()
    mock_app.get.return_value = []
    rec._app = mock_app

    bgr = np.zeros((100, 100, 3), dtype=np.uint8)
    faces = await rec.detect_and_match(bgr, enrolled={})
    assert faces == ()


# ─── jpeg_to_bgr ────────────────────────────────────────────────────


def test_jpeg_to_bgr_decodes_valid_jpeg():
    import cv2

    img = np.full((40, 60, 3), 128, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", img)
    assert ok
    out = jpeg_to_bgr(jpeg.tobytes())
    assert out is not None
    assert out.shape == (40, 60, 3)


def test_jpeg_to_bgr_empty_bytes_returns_none():
    assert jpeg_to_bgr(b"") is None


def test_jpeg_to_bgr_garbage_returns_none():
    assert jpeg_to_bgr(b"\xff\xd8\xff\xff\x00not-a-jpeg") is None
