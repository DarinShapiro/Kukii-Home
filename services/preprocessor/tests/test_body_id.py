"""Unit tests for the body re-ID pipeline (OSNet) — model layer.

The real ONNX session is mocked so these tests run without the
osnet_x1_0.onnx file on disk. The bbox-crop math, preprocessing
(resize + normalize), L2 normalization, cosine match, and
DetectedBody -> ActorMatch conversion are exercised end-to-end.

A slow integration test against a real OSNet ONNX lands once the
export script + a known-good model are in the repo (separate
commit).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from kukiihome_preprocessor.pipelines.body_id import (
    BodyIdConfig,
    BodyIdRecognizer,
    DetectedBody,
    _crop_person,
    _l2_normalize_rows,
    _match,
    _preprocess,
    detected_body_to_actor_match,
)


def _unit(v: list[float]) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(a)
    return a / n if n > 0 else a


# ─── _crop_person ───────────────────────────────────────────────────


def test_crop_person_extracts_pixel_region():
    bgr = np.zeros((100, 200, 3), dtype=np.uint8)
    bgr[20:80, 50:150] = 255  # interior block
    crop = _crop_person(bgr, (0.25, 0.20, 0.75, 0.80), w=200, h=100)
    assert crop is not None
    assert crop.shape == (60, 100, 3)
    # White block should fill the crop.
    assert (crop == 255).all()


def test_crop_person_returns_none_for_degenerate_bbox():
    bgr = np.zeros((100, 200, 3), dtype=np.uint8)
    crop = _crop_person(bgr, (0.5, 0.5, 0.5, 0.5), w=200, h=100)
    assert crop is None


def test_crop_person_clamps_out_of_bounds_bbox():
    """Bbox extending past the frame edge -> clamped to frame
    bounds, not a slice IndexError."""
    bgr = np.zeros((100, 200, 3), dtype=np.uint8)
    crop = _crop_person(bgr, (-0.1, -0.1, 1.5, 1.5), w=200, h=100)
    assert crop is not None
    assert crop.shape == (100, 200, 3)


# ─── _preprocess ────────────────────────────────────────────────────


def test_preprocess_produces_chw_float32_correct_size():
    crop = np.full((300, 150, 3), 128, dtype=np.uint8)
    out = _preprocess(crop, height=256, width=128)
    assert out.shape == (3, 256, 128)
    assert out.dtype == np.float32


def test_preprocess_normalizes_with_imagenet_stats():
    """A solid mid-gray crop (128/255 ≈ 0.502) after ImageNet
    normalization is approximately (0.502 - mean) / std per channel."""
    crop = np.full((10, 10, 3), 128, dtype=np.uint8)
    out = _preprocess(crop, height=8, width=8)
    # OpenCV BGR2RGB swaps channels — pre-normalize the channels
    # are all 128/255. After ImageNet normalize, the per-channel
    # mean should be roughly (0.502 - imagenet_mean) / imagenet_std.
    means = out.mean(axis=(1, 2))
    expected = (0.502 - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])
    np.testing.assert_allclose(means, expected, atol=1e-2)


# ─── _l2_normalize_rows ─────────────────────────────────────────────


def test_l2_normalize_rows_makes_each_row_unit():
    arr = np.array([[3.0, 4.0], [1.0, 0.0], [0.0, 0.0]], dtype=np.float32)
    out = _l2_normalize_rows(arr)
    np.testing.assert_allclose(np.linalg.norm(out[0]), 1.0, atol=1e-6)
    np.testing.assert_allclose(np.linalg.norm(out[1]), 1.0, atol=1e-6)
    # Zero row stays zero (safe-divide path).
    np.testing.assert_allclose(out[2], [0.0, 0.0])


# ─── _match ─────────────────────────────────────────────────────────


def test_match_returns_highest_above_threshold():
    target = _unit([1.0, 0.0, 0.0])
    enrolled = {
        "alice": _unit([0.95, 0.05, 0.0]),  # cosine ~ 0.998
        "bob": _unit([0.0, 1.0, 0.0]),
    }
    actor, sim = _match(target, enrolled, threshold=0.6)
    assert actor == "alice"
    assert sim > 0.99


def test_match_returns_none_below_threshold():
    target = _unit([1.0, 0.0, 0.0])
    enrolled = {"alice": _unit([0.5, 0.866, 0.0])}  # cosine 0.5
    actor, sim = _match(target, enrolled, threshold=0.6)
    assert actor is None
    assert sim == 0.0


def test_match_empty_enrolled_returns_none():
    target = _unit([1.0, 0.0, 0.0])
    actor, sim = _match(target, {}, threshold=0.6)
    assert actor is None
    assert sim == 0.0


# ─── detected_body_to_actor_match ───────────────────────────────────


def test_detected_body_to_actor_match_returns_none_for_unmatched():
    body = DetectedBody(
        track_id="t1",
        embedding=_unit([1.0, 0.0]),
        matched_actor_id=None,
        match_confidence=0.0,
    )
    assert detected_body_to_actor_match(body, frame_ts=1.0) is None


def test_detected_body_to_actor_match_builds_actor_match():
    body = DetectedBody(
        track_id="t9",
        embedding=_unit([1.0, 0.0]),
        matched_actor_id="alice",
        match_confidence=0.72,
    )
    match = detected_body_to_actor_match(body, frame_ts=42.0)
    assert match is not None
    assert match.actor_id == "alice"
    assert match.confidence == 0.72
    assert match.match_method == "body_id_osnet"
    assert match.frame_ts == 42.0
    assert match.track_id == "t9"


# ─── BodyIdRecognizer with mocked ONNX session ──────────────────────


def _config_with_dummy_model() -> BodyIdConfig:
    """Config pointing at a path that won't be loaded (we patch the
    session in tests)."""
    return BodyIdConfig(model_path="/dev/null/never-loaded.onnx")


@pytest.mark.asyncio
async def test_recognizer_returns_empty_when_no_persons():
    rec = BodyIdRecognizer(_config_with_dummy_model())
    out = await rec.identify_persons(
        np.zeros((100, 100, 3), dtype=np.uint8), persons=[], enrolled={}
    )
    assert out == ()


@pytest.mark.asyncio
async def test_recognizer_returns_empty_when_model_load_fails():
    """ONNX file missing -> ensure_session() sets _load_failed and
    returns None -> identify_persons returns empty. Preprocessor
    stays up despite the misconfig."""
    rec = BodyIdRecognizer(_config_with_dummy_model())
    out = await rec.identify_persons(
        np.zeros((100, 100, 3), dtype=np.uint8),
        persons=[("t1", (0.0, 0.0, 1.0, 1.0))],
        enrolled={"alice": _unit([1.0, 0.0, 0.0])},
    )
    assert out == ()
    assert rec._load_failed is True


@pytest.mark.asyncio
async def test_recognizer_runs_session_per_call_with_batched_crops():
    """Two person bboxes -> single ONNX session.run with batch
    shape (2, 3, 256, 128). Per-track embeddings get matched."""
    rec = BodyIdRecognizer(_config_with_dummy_model())

    # Fake ONNX session returning a (N, 512) tensor.
    mock_session = MagicMock()
    mock_session.get_inputs.return_value = [MagicMock(name="input.1")]
    mock_session.get_inputs.return_value[0].name = "input.1"
    # Two crops -> two pre-normalized embeddings, identical to
    # alice's enrolled vector (high cosine match expected).
    raw = np.tile(np.array([10.0, 0.0, 0.0], dtype=np.float32), (2, 1))  # shape (2, 3)
    mock_session.run.return_value = [raw]
    rec._session = mock_session

    bgr = np.zeros((200, 200, 3), dtype=np.uint8)
    persons = [
        ("t1", (0.0, 0.0, 0.5, 1.0)),
        ("t2", (0.5, 0.0, 1.0, 1.0)),
    ]
    enrolled = {"alice": _unit([1.0, 0.0, 0.0])}

    out = await rec.identify_persons(bgr, persons, enrolled)
    assert len(out) == 2
    assert {b.track_id for b in out} == {"t1", "t2"}
    # Both should match alice (raw [10, 0, 0] normalized -> [1, 0, 0]).
    assert all(b.matched_actor_id == "alice" for b in out)
    assert all(b.match_confidence == pytest.approx(1.0, abs=1e-5) for b in out)

    # Verify the batch input was 2 entries.
    call_args = mock_session.run.call_args
    feed = call_args[0][1]
    batch = feed["input.1"]
    assert batch.shape[0] == 2
    assert batch.shape[1:] == (3, 256, 128)


@pytest.mark.asyncio
async def test_recognizer_drops_degenerate_crops_silently():
    """A zero-area bbox in the input list shouldn't crash; the
    remaining crop should still produce an embedding."""
    rec = BodyIdRecognizer(_config_with_dummy_model())

    mock_session = MagicMock()
    mock_session.get_inputs.return_value = [MagicMock()]
    mock_session.get_inputs.return_value[0].name = "input.1"
    # Return one embedding for the one valid crop.
    mock_session.run.return_value = [np.array([[10.0, 0.0, 0.0]], dtype=np.float32)]
    rec._session = mock_session

    bgr = np.zeros((100, 100, 3), dtype=np.uint8)
    persons = [
        ("t_bad", (0.5, 0.5, 0.5, 0.5)),  # degenerate
        ("t_good", (0.0, 0.0, 1.0, 1.0)),  # valid
    ]
    out = await rec.identify_persons(bgr, persons, enrolled={"alice": _unit([1.0, 0.0, 0.0])})
    assert len(out) == 1
    assert out[0].track_id == "t_good"


@pytest.mark.asyncio
async def test_recognizer_handles_inference_exception():
    """ONNX session.run raising shouldn't kill the pipeline — log
    and return empty."""
    rec = BodyIdRecognizer(_config_with_dummy_model())
    mock_session = MagicMock()
    mock_session.get_inputs.return_value = [MagicMock()]
    mock_session.get_inputs.return_value[0].name = "input.1"
    mock_session.run.side_effect = RuntimeError("CUDA OOM")
    rec._session = mock_session

    out = await rec.identify_persons(
        np.zeros((100, 100, 3), dtype=np.uint8),
        persons=[("t1", (0.0, 0.0, 1.0, 1.0))],
        enrolled={"alice": _unit([1.0, 0.0, 0.0])},
    )
    assert out == ()
