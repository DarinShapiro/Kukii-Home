"""Integration test that actually loads OSNet ONNX and runs inference.

Verifies the body re-ID round-trip end-to-end against a real model
file: load, batched inference, L2-normalized 512-d output, cosine
match identifies "the same person" higher than "different person".

Skipped when:
* onnxruntime isn't installed (bare CI env)
* The OSNet ONNX file isn't at the configured path. Run
  ``scripts/dev/export_osnet_onnx.py`` once to produce it. The test
  doesn't trigger the export automatically — it'd pull torchreid +
  gdown + tensorboard into the test runner, which is overkill for a
  smoke test.

Marked ``slow`` so PR CI excludes by default.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import numpy as np
import pytest
from kukiihome_preprocessor.pipelines.body_id import (
    BodyIdConfig,
    BodyIdRecognizer,
)


def _onnxruntime_available() -> bool:
    return importlib.util.find_spec("onnxruntime") is not None


def _resolve_model_path() -> Path | None:
    """Look for a real OSNet ONNX in a few candidate locations.

    Order of preference:
    1. ``KUKIIHOME_TEST_OSNET_PATH`` env override (CI explicit path)
    2. The preprocessor's production-default path
    3. A dev-friendly path next to the repo
    """
    candidates = []
    env = os.environ.get("KUKIIHOME_TEST_OSNET_PATH")
    if env:
        candidates.append(Path(env))
    candidates.append(Path("/data/kukiihome/models/osnet_x1_0.onnx"))
    candidates.append(Path.home() / ".cache" / "kukiihome" / "osnet_x1_0.onnx")
    for c in candidates:
        if c.is_file():
            return c
    return None


_MODEL_PATH = _resolve_model_path()

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _onnxruntime_available(),
        reason="onnxruntime not installed; pip install onnxruntime to run.",
    ),
    pytest.mark.skipif(
        _MODEL_PATH is None,
        reason=(
            "No OSNet ONNX found. Run "
            "scripts/dev/export_osnet_onnx.py and set "
            "KUKIIHOME_TEST_OSNET_PATH if not at the default."
        ),
    ),
]


def _synthetic_person_crop(seed: int, height: int = 400, width: int = 200) -> np.ndarray:
    """Build a deterministic 'person-shaped' BGR crop.

    Not a real photo — OSNet won't produce semantically meaningful
    embeddings on it. But it gives us:
    * A consistent crop per seed (same input -> same embedding)
    * Different crops for different seeds (different embeddings)

    That's enough to verify the inference + match math runs without
    asserting actual identity quality (which would need real photos
    + a labelled benchmark).
    """
    rng = np.random.default_rng(seed)
    # Solid background + a vertical "person-shaped" colored bar.
    bgr = (rng.integers(80, 120, size=(height, width, 3))).astype(np.uint8)
    color = tuple(int(c) for c in rng.integers(50, 200, size=3))
    bar_x1 = width // 4
    bar_x2 = 3 * width // 4
    bar_y1 = height // 8
    bar_y2 = 7 * height // 8
    bgr[bar_y1:bar_y2, bar_x1:bar_x2] = color
    return bgr


@pytest.mark.asyncio
async def test_real_osnet_produces_l2_normalized_512d_embeddings():
    """Smoke test: real model loads, runs on a 2-person batch, the
    output is shape (2, 512) and L2-normalized."""
    rec = BodyIdRecognizer(BodyIdConfig(model_path=str(_MODEL_PATH)))

    bgr = np.zeros((600, 800, 3), dtype=np.uint8)
    # Two non-overlapping "persons" by bbox.
    bgr[100:500, 100:300] = _synthetic_person_crop(seed=1)
    bgr[100:500, 500:700] = _synthetic_person_crop(seed=2)
    persons = [
        ("t1", (100 / 800, 100 / 600, 300 / 800, 500 / 600)),
        ("t2", (500 / 800, 100 / 600, 700 / 800, 500 / 600)),
    ]

    out = await rec.identify_persons(bgr, persons, enrolled={})
    assert len(out) == 2
    # Both embeddings are 512-d.
    assert all(b.embedding.shape == (512,) for b in out)
    # And L2-normalized to ~1.0.
    for b in out:
        np.testing.assert_allclose(np.linalg.norm(b.embedding), 1.0, atol=1e-4)
    # With no enrolled corpus, both are unmatched.
    assert all(b.matched_actor_id is None for b in out)


@pytest.mark.asyncio
async def test_real_osnet_matches_self_higher_than_other():
    """Run inference twice on the same crop -> embeddings should
    have cosine ~ 1.0. Run on different crops -> cosine noticeably
    lower. Establishes the basic 'same input maps to same point'
    property the matching algorithm relies on."""
    rec = BodyIdRecognizer(BodyIdConfig(model_path=str(_MODEL_PATH)))

    # Identical crops in two "frames".
    crop_alice = _synthetic_person_crop(seed=42)
    crop_bob = _synthetic_person_crop(seed=99)

    def _full_frame_with(crop: np.ndarray) -> np.ndarray:
        bgr = np.zeros((600, 400, 3), dtype=np.uint8)
        bgr[100:500, 100:300] = crop
        return bgr

    persons = [("t1", (100 / 400, 100 / 600, 300 / 400, 500 / 600))]

    out_alice = await rec.identify_persons(_full_frame_with(crop_alice), persons, enrolled={})
    out_alice_again = await rec.identify_persons(_full_frame_with(crop_alice), persons, enrolled={})
    out_bob = await rec.identify_persons(_full_frame_with(crop_bob), persons, enrolled={})

    sim_self = float(np.dot(out_alice[0].embedding, out_alice_again[0].embedding))
    sim_other = float(np.dot(out_alice[0].embedding, out_bob[0].embedding))

    # Same crop -> embeddings effectively identical (cosine ~ 1.0).
    assert sim_self == pytest.approx(1.0, abs=1e-4), (
        f"identical inputs should produce identical embeddings; got cosine={sim_self}"
    )
    # Different crop -> lower cosine. Loose bound because synthetic
    # crops can still look superficially similar to OSNet.
    assert sim_other < sim_self


@pytest.mark.asyncio
async def test_real_osnet_match_via_enrolled_corpus():
    """The full match path: pre-compute an 'enrolled' embedding,
    feed the same crop at inference time, expect the recognizer to
    surface it as a match."""
    rec = BodyIdRecognizer(BodyIdConfig(model_path=str(_MODEL_PATH), match_threshold=0.5))

    crop = _synthetic_person_crop(seed=123)
    bgr = np.zeros((600, 400, 3), dtype=np.uint8)
    bgr[100:500, 100:300] = crop
    persons = [("t1", (100 / 400, 100 / 600, 300 / 400, 500 / 600))]

    # Step 1: enroll — compute the embedding once.
    enrolled_out = await rec.identify_persons(bgr, persons, enrolled={})
    enrolled = {"alice": enrolled_out[0].embedding}

    # Step 2: re-run inference on the same crop with alice enrolled.
    matched_out = await rec.identify_persons(bgr, persons, enrolled=enrolled)
    assert len(matched_out) == 1
    assert matched_out[0].matched_actor_id == "alice"
    assert matched_out[0].match_confidence == pytest.approx(1.0, abs=1e-4)
