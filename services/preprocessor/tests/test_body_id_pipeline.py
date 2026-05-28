"""Unit tests for the BodyIdPipeline adapter.

The model layer (BodyIdRecognizer) is tested separately in
test_body_id.py. Here we cover the pipeline-Protocol surface:

* triggers_on + has_enrollments + depends_on + skip threshold
* corpus.bodies forwarded to the recognizer
* untracked person dets dropped silently
* corrupt JPEG short-circuits cleanly
* DetectedBody -> ActorMatch conversion (drop unmatched)
"""

from __future__ import annotations

import numpy as np
import pytest
from sentihome_preprocessor.pipelines.body_id import DetectedBody
from sentihome_preprocessor.pipelines.identity import BodyIdPipeline
from sentihome_preprocessor.pipelines.identity.router import EnrolledCorpus
from sentihome_preprocessor.pipelines.rolling_buffer import BufferedFrame
from sentihome_shared.preprocessor import DetectionTag


def _real_jpeg(ts: float, w: int = 100, h: int = 100) -> BufferedFrame:
    import cv2

    img = np.full((h, w, 3), 128, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", img)
    assert ok
    return BufferedFrame(ts=ts, jpeg_bytes=jpeg.tobytes(), width=w, height=h)


class _StubBodyIdRecognizer:
    def __init__(self, bodies: tuple = ()) -> None:
        self._bodies = bodies
        self.calls: list[tuple] = []  # (bgr_shape, persons, enrolled_keys)

    async def identify_persons(self, bgr, persons, enrolled):
        self.calls.append((bgr.shape, tuple(persons), tuple(sorted(enrolled.keys()))))
        return self._bodies


# ─── Protocol surface ───────────────────────────────────────────────


def test_body_id_pipeline_advertises_protocol_fields():
    p = BodyIdPipeline(_StubBodyIdRecognizer())
    assert p.name == "body_id_osnet"
    assert p.triggers_on == frozenset({"person"})
    assert p.depends_on == ("face_arcface",)
    assert p.skip_when_upstream_matched_above == 0.85


def test_has_enrollments_reads_bodies_slice():
    p = BodyIdPipeline(_StubBodyIdRecognizer())
    assert p.has_enrollments(EnrolledCorpus()) is False
    assert p.has_enrollments(EnrolledCorpus(bodies={"alice": np.array([1.0, 0.0])})) is True


# ─── run() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_forwards_corpus_bodies_to_recognizer():
    rec = _StubBodyIdRecognizer()
    p = BodyIdPipeline(rec)
    corpus = EnrolledCorpus(bodies={"alice": np.array([1.0, 0.0], dtype=np.float32)})
    dets = (
        DetectionTag(
            kind="person",
            confidence=0.9,
            bbox=(0.1, 0.1, 0.9, 0.9),
            track_id="t1",
            frame_ts=1.0,
        ),
    )
    await p.run(frame=_real_jpeg(1.0), detections=dets, corpus=corpus)
    assert len(rec.calls) == 1
    _shape, persons, enrolled_keys = rec.calls[0]
    assert persons == (("t1", (0.1, 0.1, 0.9, 0.9)),)
    assert enrolled_keys == ("alice",)


@pytest.mark.asyncio
async def test_run_emits_actor_match_for_matched_body():
    rec = _StubBodyIdRecognizer(
        bodies=(
            DetectedBody(
                track_id="t9",
                embedding=np.array([1.0, 0.0]),
                matched_actor_id="alice",
                match_confidence=0.72,
            ),
        )
    )
    p = BodyIdPipeline(rec)
    dets = (
        DetectionTag(
            kind="person",
            confidence=0.9,
            bbox=(0.0, 0.0, 1.0, 1.0),
            track_id="t9",
            frame_ts=42.0,
        ),
    )
    matches = await p.run(
        frame=_real_jpeg(42.0),
        detections=dets,
        corpus=EnrolledCorpus(bodies={"alice": np.array([1.0, 0.0])}),
    )
    assert len(matches) == 1
    m = matches[0]
    assert m.actor_id == "alice"
    assert m.match_method == "body_id_osnet"
    assert m.confidence == pytest.approx(0.72)
    assert m.track_id == "t9"
    assert m.frame_ts == 42.0


@pytest.mark.asyncio
async def test_run_drops_unmatched_bodies():
    rec = _StubBodyIdRecognizer(
        bodies=(
            DetectedBody(
                track_id="t1",
                embedding=np.array([1.0, 0.0]),
                matched_actor_id=None,
                match_confidence=0.0,
            ),
        )
    )
    p = BodyIdPipeline(rec)
    dets = (
        DetectionTag(
            kind="person",
            confidence=0.9,
            bbox=(0.0, 0.0, 1.0, 1.0),
            track_id="t1",
            frame_ts=1.0,
        ),
    )
    matches = await p.run(
        frame=_real_jpeg(1.0),
        detections=dets,
        corpus=EnrolledCorpus(bodies={"alice": np.array([1.0, 0.0])}),
    )
    assert matches == ()


@pytest.mark.asyncio
async def test_run_skips_untracked_person_dets():
    """Untracked person dets can't be correlated downstream — drop
    them before invoking the recognizer."""
    rec = _StubBodyIdRecognizer()
    p = BodyIdPipeline(rec)
    dets = (
        DetectionTag(
            kind="person",
            confidence=0.9,
            bbox=(0.0, 0.0, 1.0, 1.0),
            track_id=None,
            frame_ts=1.0,
        ),
    )
    matches = await p.run(
        frame=_real_jpeg(1.0),
        detections=dets,
        corpus=EnrolledCorpus(bodies={"alice": np.array([1.0, 0.0])}),
    )
    assert matches == ()
    # Short-circuited before reaching the recognizer.
    assert rec.calls == []


@pytest.mark.asyncio
async def test_run_returns_empty_when_jpeg_undecodable():
    rec = _StubBodyIdRecognizer()
    p = BodyIdPipeline(rec)
    bad_frame = BufferedFrame(ts=1.0, jpeg_bytes=b"not-a-jpeg", width=100, height=100)
    dets = (
        DetectionTag(
            kind="person",
            confidence=0.9,
            bbox=(0.0, 0.0, 1.0, 1.0),
            track_id="t1",
            frame_ts=1.0,
        ),
    )
    matches = await p.run(
        frame=bad_frame,
        detections=dets,
        corpus=EnrolledCorpus(bodies={"alice": np.array([1.0, 0.0])}),
    )
    assert matches == ()
    assert rec.calls == []
