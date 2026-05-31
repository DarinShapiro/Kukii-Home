"""Unit tests for the PetPipeline adapter."""

from __future__ import annotations

import numpy as np
import pytest
from kukiihome_preprocessor.pipelines.identity import PetPipeline
from kukiihome_preprocessor.pipelines.identity.router import EnrolledCorpus
from kukiihome_preprocessor.pipelines.pet import DetectedPet
from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame
from kukiihome_shared.preprocessor import DetectionTag


def _real_jpeg(ts: float, w: int = 100, h: int = 100) -> BufferedFrame:
    import cv2

    img = np.full((h, w, 3), 128, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", img)
    assert ok
    return BufferedFrame(ts=ts, jpeg_bytes=jpeg.tobytes(), width=w, height=h)


class _StubPetRecognizer:
    def __init__(self, pets: tuple = ()) -> None:
        self._pets = pets
        self.calls: list[tuple] = []

    async def identify_pets(self, bgr, pets, enrolled):
        self.calls.append((bgr.shape, tuple(pets), tuple(sorted(enrolled.keys()))))
        return self._pets


def _det(kind: str, track_id: str | None = "t1") -> DetectionTag:
    return DetectionTag(
        kind=kind,
        confidence=0.9,
        bbox=(0.1, 0.1, 0.9, 0.9),
        track_id=track_id,
        frame_ts=1.0,
    )


# ─── Protocol surface ───────────────────────────────────────────────


def test_pet_pipeline_protocol_fields():
    p = PetPipeline(_StubPetRecognizer())
    assert p.name == "pet_dinov2"
    assert p.triggers_on == frozenset({"dog", "cat"})
    assert p.depends_on == ()  # independent
    assert p.skip_when_upstream_matched_above is None


def test_has_enrollments_reads_pets_slice():
    p = PetPipeline(_StubPetRecognizer())
    assert p.has_enrollments(EnrolledCorpus()) is False
    assert p.has_enrollments(EnrolledCorpus(pets={"rex": np.array([1.0, 0.0])})) is True


# ─── run() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_forwards_dog_and_cat_with_kind():
    rec = _StubPetRecognizer()
    p = PetPipeline(rec)
    corpus = EnrolledCorpus(pets={"rex": np.array([1.0, 0.0], dtype=np.float32)})
    dets = (_det("dog", "t1"), _det("cat", "t2"))
    await p.run(frame=_real_jpeg(1.0), detections=dets, corpus=corpus)
    assert len(rec.calls) == 1
    _shape, pets, enrolled_keys = rec.calls[0]
    # (track_id, kind, bbox) tuples; both kinds forwarded.
    assert {(t, k) for t, k, _bbox in pets} == {("t1", "dog"), ("t2", "cat")}
    assert enrolled_keys == ("rex",)


@pytest.mark.asyncio
async def test_run_ignores_non_pet_detections():
    """A person/vehicle in the same frame must not be sent to the
    pet recognizer."""
    rec = _StubPetRecognizer()
    p = PetPipeline(rec)
    dets = (_det("person", "t1"), _det("vehicle", "t2"), _det("dog", "t3"))
    await p.run(
        frame=_real_jpeg(1.0),
        detections=dets,
        corpus=EnrolledCorpus(pets={"rex": np.array([1.0, 0.0])}),
    )
    _shape, pets, _keys = rec.calls[0]
    assert {t for t, _k, _b in pets} == {"t3"}  # only the dog


@pytest.mark.asyncio
async def test_run_emits_actor_match_for_matched_pet():
    rec = _StubPetRecognizer(
        pets=(
            DetectedPet(
                track_id="t9",
                kind="dog",
                embedding=np.array([1.0, 0.0]),
                matched_actor_id="rex",
                match_confidence=0.77,
            ),
        )
    )
    p = PetPipeline(rec)
    matches = await p.run(
        frame=_real_jpeg(5.0),
        detections=(_det("dog", "t9"),),
        corpus=EnrolledCorpus(pets={"rex": np.array([1.0, 0.0])}),
    )
    assert len(matches) == 1
    m = matches[0]
    assert m.actor_id == "rex"
    assert m.match_method == "pet_dinov2"
    assert m.track_id == "t9"
    assert m.confidence == pytest.approx(0.77)


@pytest.mark.asyncio
async def test_run_drops_unmatched_and_untracked():
    rec = _StubPetRecognizer(
        pets=(
            DetectedPet(
                track_id="t1",
                kind="cat",
                embedding=np.array([1.0, 0.0]),
                matched_actor_id=None,
                match_confidence=0.0,
            ),
        )
    )
    p = PetPipeline(rec)
    # Unmatched -> no ActorMatch.
    matches = await p.run(
        frame=_real_jpeg(1.0),
        detections=(_det("cat", "t1"),),
        corpus=EnrolledCorpus(pets={"rex": np.array([1.0, 0.0])}),
    )
    assert matches == ()
    # Untracked dog -> never reaches recognizer.
    rec.calls.clear()
    matches = await p.run(
        frame=_real_jpeg(1.0),
        detections=(_det("dog", None),),
        corpus=EnrolledCorpus(pets={"rex": np.array([1.0, 0.0])}),
    )
    assert matches == ()
    assert rec.calls == []
