"""Unit tests for the FacePipeline adapter.

The adapter wraps a FaceRecognizer and conforms to the
IdentityPipeline Protocol. Tests exercise:

* triggers_on + has_enrollments gates
* corpus.faces forwarded to the recognizer
* face-to-person association via IoU
* unmatched faces produce no ActorMatch
* corrupt JPEG short-circuits cleanly
"""

from __future__ import annotations

import numpy as np
import pytest
from sentihome_preprocessor.pipelines.face import DetectedFace
from sentihome_preprocessor.pipelines.identity import FacePipeline
from sentihome_preprocessor.pipelines.identity.router import EnrolledCorpus
from sentihome_preprocessor.pipelines.rolling_buffer import BufferedFrame
from sentihome_shared.preprocessor import DetectionTag


def _real_jpeg(ts: float, w: int = 100, h: int = 100) -> BufferedFrame:
    import cv2

    img = np.full((h, w, 3), 128, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", img)
    assert ok
    return BufferedFrame(ts=ts, jpeg_bytes=jpeg.tobytes(), width=w, height=h)


class _StubRecognizer:
    """Captures arguments + returns a fixed face list."""

    def __init__(self, faces: tuple = ()) -> None:
        self.faces = faces
        self.calls: list[tuple] = []

    async def detect_and_match(self, bgr, enrolled):
        self.calls.append((bgr.shape, tuple(sorted(enrolled.keys()))))
        return self.faces


# ─── Protocol surface ───────────────────────────────────────────────


def test_face_pipeline_advertises_protocol_fields():
    p = FacePipeline(_StubRecognizer())
    assert p.name == "face_arcface"
    assert p.triggers_on == frozenset({"person"})


def test_has_enrollments_true_when_corpus_has_faces():
    p = FacePipeline(_StubRecognizer())
    corpus = EnrolledCorpus(faces={"alice": np.array([1.0, 0.0])})
    assert p.has_enrollments(corpus) is True


def test_has_enrollments_false_when_corpus_faces_empty():
    p = FacePipeline(_StubRecognizer())
    corpus = EnrolledCorpus()
    assert p.has_enrollments(corpus) is False


# ─── run() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_forwards_corpus_faces_to_recognizer():
    rec = _StubRecognizer()
    p = FacePipeline(rec)
    corpus = EnrolledCorpus(faces={"alice": np.array([1.0, 0.0], dtype=np.float32)})
    dets = (
        DetectionTag(
            kind="person",
            confidence=0.9,
            bbox=(0.0, 0.0, 1.0, 1.0),
            track_id="t1",
            frame_ts=1.0,
        ),
    )
    await p.run(frame=_real_jpeg(1.0), detections=dets, corpus=corpus)
    assert len(rec.calls) == 1
    _shape, enrolled_keys = rec.calls[0]
    assert enrolled_keys == ("alice",)


@pytest.mark.asyncio
async def test_run_emits_actor_match_for_matched_face_in_person_bbox():
    embedding = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    rec = _StubRecognizer(
        faces=(
            DetectedFace(
                bbox=(0.4, 0.4, 0.6, 0.6),  # inside the person bbox
                det_confidence=0.95,
                embedding=embedding,
                matched_actor_id="alice",
                match_confidence=0.83,
            ),
        )
    )
    p = FacePipeline(rec)
    corpus = EnrolledCorpus(faces={"alice": embedding})
    dets = (
        DetectionTag(
            kind="person",
            confidence=0.9,
            bbox=(0.0, 0.0, 1.0, 1.0),
            track_id="t9",
            frame_ts=42.0,
        ),
    )
    matches = await p.run(frame=_real_jpeg(42.0), detections=dets, corpus=corpus)
    assert len(matches) == 1
    m = matches[0]
    assert m.actor_id == "alice"
    assert m.confidence == pytest.approx(0.83)
    assert m.match_method == "face_arcface"
    assert m.track_id == "t9"
    assert m.frame_ts == 42.0


@pytest.mark.asyncio
async def test_run_drops_unmatched_faces():
    rec = _StubRecognizer(
        faces=(
            DetectedFace(
                bbox=(0.4, 0.4, 0.6, 0.6),
                det_confidence=0.95,
                embedding=np.array([1.0, 0.0], dtype=np.float32),
                matched_actor_id=None,  # unmatched
                match_confidence=0.0,
            ),
        )
    )
    p = FacePipeline(rec)
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
        corpus=EnrolledCorpus(faces={"alice": np.array([1.0, 0.0])}),
    )
    assert matches == ()


@pytest.mark.asyncio
async def test_run_returns_empty_when_no_tracked_person_detections():
    """All person dets have None track_id -> face match can't
    inherit a track_id, so nothing to correlate downstream."""
    rec = _StubRecognizer(
        faces=(
            DetectedFace(
                bbox=(0.4, 0.4, 0.6, 0.6),
                det_confidence=0.95,
                embedding=np.array([1.0, 0.0]),
                matched_actor_id="alice",
                match_confidence=0.9,
            ),
        )
    )
    p = FacePipeline(rec)
    dets = (
        DetectionTag(
            kind="person",
            confidence=0.9,
            bbox=(0.0, 0.0, 1.0, 1.0),
            track_id=None,  # untracked
            frame_ts=1.0,
        ),
    )
    matches = await p.run(
        frame=_real_jpeg(1.0),
        detections=dets,
        corpus=EnrolledCorpus(faces={"alice": np.array([1.0, 0.0])}),
    )
    assert matches == ()
    # Short-circuited before calling the recognizer.
    assert rec.calls == []


@pytest.mark.asyncio
async def test_run_returns_empty_when_jpeg_undecodable():
    rec = _StubRecognizer()
    p = FacePipeline(rec)
    bad_frame = BufferedFrame(
        ts=1.0,
        jpeg_bytes=b"not-a-jpeg",
        width=100,
        height=100,
    )
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
        corpus=EnrolledCorpus(faces={"alice": np.array([1.0, 0.0])}),
    )
    assert matches == ()
    assert rec.calls == []
