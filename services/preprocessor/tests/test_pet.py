"""Unit tests for the pet recognition pipeline (DINOv2) — model layer.

The ONNX session is mocked so these run without the dinov2 ONNX
file. Crop math, preprocessing, L2 normalization, cosine match, and
DetectedPet -> ActorMatch conversion are exercised end-to-end. A
slow integration test against a real DINOv2 ONNX lands with the
export script (Phase 10.5.3b).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from sentihome_preprocessor.pipelines.pet import (
    DetectedPet,
    PetConfig,
    PetRecognizer,
    _crop,
    _l2_normalize_rows,
    _match,
    _preprocess,
    detected_pet_to_actor_match,
)


def _unit(v: list[float]) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(a)
    return a / n if n > 0 else a


# ─── _crop ──────────────────────────────────────────────────────────


def test_crop_extracts_region():
    bgr = np.zeros((100, 200, 3), dtype=np.uint8)
    bgr[20:80, 50:150] = 255
    crop = _crop(bgr, (0.25, 0.20, 0.75, 0.80), w=200, h=100)
    assert crop is not None
    assert crop.shape == (60, 100, 3)
    assert (crop == 255).all()


def test_crop_none_for_degenerate_bbox():
    bgr = np.zeros((100, 200, 3), dtype=np.uint8)
    assert _crop(bgr, (0.5, 0.5, 0.5, 0.5), w=200, h=100) is None


# ─── _preprocess ────────────────────────────────────────────────────


def test_preprocess_square_chw_float32():
    crop = np.full((90, 140, 3), 128, dtype=np.uint8)
    out = _preprocess(crop, size=224)
    assert out.shape == (3, 224, 224)
    assert out.dtype == np.float32


# ─── _match ─────────────────────────────────────────────────────────


def test_match_highest_above_threshold():
    target = _unit([1.0, 0.0, 0.0])
    enrolled = {"rex": _unit([0.95, 0.05, 0.0]), "mittens": _unit([0.0, 1.0, 0.0])}
    actor, sim = _match(target, enrolled, threshold=0.6)
    assert actor == "rex"
    assert sim > 0.99


def test_match_none_below_threshold():
    target = _unit([1.0, 0.0, 0.0])
    enrolled = {"rex": _unit([0.5, 0.866, 0.0])}  # cosine 0.5
    actor, sim = _match(target, enrolled, threshold=0.6)
    assert actor is None
    assert sim == 0.0


def test_match_empty_enrolled():
    actor, sim = _match(_unit([1.0, 0.0]), {}, threshold=0.6)
    assert actor is None
    assert sim == 0.0


# ─── detected_pet_to_actor_match ────────────────────────────────────


def test_detected_pet_to_actor_match_none_for_unmatched():
    pet = DetectedPet(
        track_id="t1",
        kind="dog",
        embedding=_unit([1.0, 0.0]),
        matched_actor_id=None,
        match_confidence=0.0,
    )
    assert detected_pet_to_actor_match(pet, frame_ts=1.0) is None


def test_detected_pet_to_actor_match_builds_match():
    pet = DetectedPet(
        track_id="t7",
        kind="dog",
        embedding=_unit([1.0, 0.0]),
        matched_actor_id="rex",
        match_confidence=0.81,
    )
    m = detected_pet_to_actor_match(pet, frame_ts=10.0)
    assert m is not None
    assert m.actor_id == "rex"
    assert m.match_method == "pet_dinov2"
    assert m.confidence == 0.81
    assert m.track_id == "t7"
    assert m.frame_ts == 10.0


# ─── PetRecognizer (mocked ONNX) ────────────────────────────────────


def _cfg() -> PetConfig:
    return PetConfig(model_path="/dev/null/never-loaded.onnx")


@pytest.mark.asyncio
async def test_recognizer_empty_when_no_pets():
    rec = PetRecognizer(_cfg())
    out = await rec.identify_pets(np.zeros((100, 100, 3), dtype=np.uint8), [], {})
    assert out == ()


@pytest.mark.asyncio
async def test_recognizer_empty_when_model_load_fails():
    rec = PetRecognizer(_cfg())
    out = await rec.identify_pets(
        np.zeros((100, 100, 3), dtype=np.uint8),
        [("t1", "dog", (0.0, 0.0, 1.0, 1.0))],
        {"rex": _unit([1.0, 0.0, 0.0])},
    )
    assert out == ()
    assert rec._load_failed is True


@pytest.mark.asyncio
async def test_recognizer_batches_and_matches():
    rec = PetRecognizer(_cfg())
    mock_session = MagicMock()
    mock_session.get_inputs.return_value = [MagicMock()]
    mock_session.get_inputs.return_value[0].name = "input"
    # Two crops -> two embeddings, both ~ rex's enrolled vector.
    raw = np.tile(np.array([10.0, 0.0, 0.0], dtype=np.float32), (2, 1))
    mock_session.run.return_value = [raw]
    rec._session = mock_session

    bgr = np.zeros((200, 200, 3), dtype=np.uint8)
    pets = [
        ("t1", "dog", (0.0, 0.0, 0.5, 1.0)),
        ("t2", "cat", (0.5, 0.0, 1.0, 1.0)),
    ]
    out = await rec.identify_pets(bgr, pets, {"rex": _unit([1.0, 0.0, 0.0])})
    assert {p.track_id for p in out} == {"t1", "t2"}
    # kind preserved per detection.
    by_id = {p.track_id: p for p in out}
    assert by_id["t1"].kind == "dog"
    assert by_id["t2"].kind == "cat"
    assert all(p.matched_actor_id == "rex" for p in out)
    # Batched into one session.run with 2 entries.
    feed = mock_session.run.call_args[0][1]
    assert feed["input"].shape[0] == 2
    assert feed["input"].shape[1:] == (3, 224, 224)


@pytest.mark.asyncio
async def test_recognizer_handles_inference_exception():
    rec = PetRecognizer(_cfg())
    mock_session = MagicMock()
    mock_session.get_inputs.return_value = [MagicMock()]
    mock_session.get_inputs.return_value[0].name = "input"
    mock_session.run.side_effect = RuntimeError("boom")
    rec._session = mock_session
    out = await rec.identify_pets(
        np.zeros((100, 100, 3), dtype=np.uint8),
        [("t1", "dog", (0.0, 0.0, 1.0, 1.0))],
        {"rex": _unit([1.0, 0.0, 0.0])},
    )
    assert out == ()


def test_l2_normalize_rows():
    arr = np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32)
    out = _l2_normalize_rows(arr)
    np.testing.assert_allclose(np.linalg.norm(out[0]), 1.0, atol=1e-6)
    np.testing.assert_allclose(out[1], [0.0, 0.0])
