"""Unit tests for the CCReIDPipeline adapter (Epic 10.11.5).

CC-ReID reuses :class:`BodyIdRecognizer` (the generic person-crop
embedder), so the model layer is covered by test_body_id.py. Here we
cover the pipeline-Protocol surface that makes CC-ReID a *distinct*
durable modality:

* modality is ``body_shape`` (its own corpus slice), not ``body``
* match_method stamped is ``ccreid_cal``, not ``body_id_osnet``
* triggers_on / depends_on / skip threshold mirror body-ID
* corpus.slice("body_shape") forwarded to the recognizer
* untracked dets dropped; corrupt JPEG short-circuits cleanly
"""

from __future__ import annotations

import numpy as np
import pytest
from kukiihome_preprocessor.pipelines.body_id import DetectedBody
from kukiihome_preprocessor.pipelines.identity import CCReIDPipeline
from kukiihome_preprocessor.pipelines.identity.router import EnrolledCorpus
from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame
from kukiihome_shared.preprocessor import DetectionTag


def _real_jpeg(ts: float, w: int = 100, h: int = 100) -> BufferedFrame:
    import cv2

    img = np.full((h, w, 3), 128, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", img)
    assert ok
    return BufferedFrame(ts=ts, jpeg_bytes=jpeg.tobytes(), width=w, height=h)


class _StubRecognizer:
    def __init__(self, bodies: tuple = ()) -> None:
        self._bodies = bodies
        self.calls: list[tuple] = []  # (bgr_shape, persons, enrolled_keys)

    async def identify_persons(self, bgr, persons, enrolled):
        self.calls.append((bgr.shape, tuple(persons), tuple(sorted(enrolled.keys()))))
        return self._bodies


# ─── Protocol surface ───────────────────────────────────────────────


def test_ccreid_pipeline_advertises_protocol_fields():
    p = CCReIDPipeline(_StubRecognizer())
    assert p.name == "ccreid_cal"
    assert p.modality == "body_shape"
    assert p.triggers_on == frozenset({"person"})
    assert p.depends_on == ("face_arcface",)
    assert p.skip_when_upstream_matched_above == 0.85
    assert p.temporal is False


def test_has_enrollments_reads_body_shape_slice():
    p = CCReIDPipeline(_StubRecognizer())
    # An enrolled OSNet body (modality "body") must NOT enable CC-ReID.
    assert p.has_enrollments(EnrolledCorpus(bodies={"alice": np.array([1.0, 0.0])})) is False
    corpus = EnrolledCorpus(templates={"body_shape": {"alice": np.array([1.0, 0.0])}})
    assert p.has_enrollments(corpus) is True


# ─── run() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_forwards_body_shape_slice_to_recognizer():
    rec = _StubRecognizer()
    p = CCReIDPipeline(rec)
    corpus = EnrolledCorpus(
        templates={"body_shape": {"alice": np.array([1.0, 0.0], dtype=np.float32)}}
    )
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
async def test_run_emits_ccreid_actor_match():
    rec = _StubRecognizer(
        bodies=(
            DetectedBody(
                track_id="t9",
                embedding=np.zeros(4096, dtype=np.float32),
                matched_actor_id="alice",
                match_confidence=0.66,
            ),
        )
    )
    p = CCReIDPipeline(rec)
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
        corpus=EnrolledCorpus(templates={"body_shape": {"alice": np.zeros(4096)}}),
    )
    assert len(matches) == 1
    m = matches[0]
    assert m.actor_id == "alice"
    assert m.match_method == "ccreid_cal"
    assert m.confidence == pytest.approx(0.66)
    assert m.track_id == "t9"
    assert m.frame_ts == 42.0


@pytest.mark.asyncio
async def test_run_drops_unmatched():
    rec = _StubRecognizer(
        bodies=(
            DetectedBody(
                track_id="t1",
                embedding=np.zeros(4096),
                matched_actor_id=None,
                match_confidence=0.0,
            ),
        )
    )
    p = CCReIDPipeline(rec)
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
        corpus=EnrolledCorpus(templates={"body_shape": {"alice": np.zeros(4096)}}),
    )
    assert matches == ()


@pytest.mark.asyncio
async def test_run_skips_untracked_person_dets():
    rec = _StubRecognizer()
    p = CCReIDPipeline(rec)
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
        corpus=EnrolledCorpus(templates={"body_shape": {"alice": np.zeros(4096)}}),
    )
    assert matches == ()
    assert rec.calls == []


@pytest.mark.asyncio
async def test_run_returns_empty_when_jpeg_undecodable():
    rec = _StubRecognizer()
    p = CCReIDPipeline(rec)
    bad = BufferedFrame(ts=1.0, jpeg_bytes=b"not-a-jpeg", width=100, height=100)
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
        frame=bad,
        detections=dets,
        corpus=EnrolledCorpus(templates={"body_shape": {"alice": np.zeros(4096)}}),
    )
    assert matches == ()
    assert rec.calls == []
