"""Unit tests for the GaitPipeline adapter + gait helpers (Epic 10.11.6).

The model layer (YOLO-seg + GaitBase ONNX) needs real model files, so
the recognizer is stubbed here. We cover:

* the temporal Protocol surface (temporal=True, run() is a no-op)
* run_sequence forwards the gait corpus slice + emits gait_opengait
* DetectedGait -> ActorMatch conversion (drop unmatched)
* the pure silhouette + match helpers
"""

from __future__ import annotations

import numpy as np
import pytest
from kukiihome_preprocessor.pipelines.gait import (
    DetectedGait,
    _center_silhouette,
    _crop_padded,
    _match,
    detected_gait_to_actor_match,
)
from kukiihome_preprocessor.pipelines.identity import GaitPipeline
from kukiihome_preprocessor.pipelines.identity.router import EnrolledCorpus
from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame


def _frame(ts: float) -> BufferedFrame:
    return BufferedFrame(ts=ts, jpeg_bytes=b"x", width=100, height=100)


class _StubGaitRecognizer:
    def __init__(self, gaits: tuple = ()) -> None:
        self._gaits = gaits
        self.calls: list[tuple] = []  # (track_ids, enrolled_keys)

    async def identify_tracks(self, tracks, enrolled):
        self.calls.append((tuple(sorted(tracks)), tuple(sorted(enrolled))))
        return self._gaits


# ─── Protocol surface ───────────────────────────────────────────────


def test_gait_pipeline_advertises_temporal_protocol():
    p = GaitPipeline(_StubGaitRecognizer())
    assert p.name == "gait_opengait"
    assert p.modality == "gait"
    assert p.triggers_on == frozenset({"person"})
    assert p.temporal is True
    assert p.skip_when_upstream_matched_above == 0.85


def test_has_enrollments_reads_gait_slice():
    p = GaitPipeline(_StubGaitRecognizer())
    assert p.has_enrollments(EnrolledCorpus()) is False
    corpus = EnrolledCorpus(templates={"gait": {"alice": np.zeros(4096)}})
    assert p.has_enrollments(corpus) is True


@pytest.mark.asyncio
async def test_run_is_noop():
    p = GaitPipeline(_StubGaitRecognizer())
    assert await p.run(frame=_frame(1.0), detections=(), corpus=EnrolledCorpus()) == ()


# ─── run_sequence() ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_sequence_forwards_gait_slice_and_emits_match():
    rec = _StubGaitRecognizer(
        gaits=(
            DetectedGait(
                track_id="t1",
                embedding=np.zeros(4096),
                matched_actor_id="alice",
                match_confidence=0.52,
                frame_ts=9.0,
                n_silhouettes=20,
            ),
        )
    )
    p = GaitPipeline(rec)
    corpus = EnrolledCorpus(templates={"gait": {"alice": np.zeros(4096)}})
    tracks = {"t1": ((_frame(1.0), (0.1, 0.1, 0.5, 0.9)),)}
    matches = await p.run_sequence(tracks=tracks, corpus=corpus)
    assert rec.calls == [(("t1",), ("alice",))]
    assert len(matches) == 1
    m = matches[0]
    assert m.actor_id == "alice"
    assert m.match_method == "gait_opengait"
    assert m.confidence == pytest.approx(0.52)
    assert m.track_id == "t1"
    assert m.frame_ts == 9.0


@pytest.mark.asyncio
async def test_run_sequence_drops_unmatched():
    rec = _StubGaitRecognizer(
        gaits=(
            DetectedGait(
                track_id="t1",
                embedding=np.zeros(4096),
                matched_actor_id=None,
                match_confidence=0.0,
                frame_ts=1.0,
                n_silhouettes=20,
            ),
        )
    )
    p = GaitPipeline(rec)
    out = await p.run_sequence(
        tracks={"t1": ((_frame(1.0), (0.0, 0.0, 1.0, 1.0)),)},
        corpus=EnrolledCorpus(templates={"gait": {"alice": np.zeros(4096)}}),
    )
    assert out == ()


def test_detected_gait_to_actor_match_none_when_unmatched():
    g = DetectedGait(
        track_id="t1",
        embedding=np.zeros(4096),
        matched_actor_id=None,
        match_confidence=0.0,
        frame_ts=1.0,
        n_silhouettes=20,
    )
    assert detected_gait_to_actor_match(g) is None


# ─── pure helpers ───────────────────────────────────────────────────


def test_center_silhouette_output_shape_and_centering():
    # A blob in the left half should be recentered horizontally.
    mask = np.zeros((80, 60), np.uint8)
    mask[10:70, 5:15] = 255
    out = _center_silhouette(mask)
    assert out.shape == (64, 44)
    assert out.max() == 255
    # Centroid of the foreground should sit near the middle column.
    cols = np.where(out > 0)[1]
    assert abs(cols.mean() - 22) < 8


def test_center_silhouette_empty_mask():
    out = _center_silhouette(np.zeros((40, 40), np.uint8))
    assert out.shape == (64, 44)
    assert out.max() == 0


def test_crop_padded_degenerate_returns_none():
    bgr = np.zeros((100, 100, 3), np.uint8)
    assert _crop_padded(bgr, (0.5, 0.5, 0.5, 0.5), 0.0) is None


def test_crop_padded_expands_box():
    bgr = np.zeros((100, 100, 3), np.uint8)
    crop = _crop_padded(bgr, (0.4, 0.4, 0.6, 0.6), 0.1)
    assert crop is not None
    # 0.3..0.7 in a 100px frame -> ~40px each side.
    assert crop.shape[0] == 40 and crop.shape[1] == 40


def test_match_threshold_gate():
    enrolled = {"alice": np.array([1.0, 0.0], dtype=np.float32)}
    assert _match(np.array([1.0, 0.0]), enrolled, 0.35) == ("alice", pytest.approx(1.0))
    assert _match(np.array([0.0, 1.0]), enrolled, 0.35) == (None, 0.0)
    assert _match(np.array([1.0, 0.0]), {}, 0.35) == (None, 0.0)
