"""Always-embed → persist → resolve loop (Build #292, body-ID vertical slice).

Proves the loop end-to-end without ONNX/torch by stubbing the recognizer:

* BodyIdPipeline.embed() returns an embedding for every tracked person with
  NO corpus and NO matching (the decoupled half of body-ID).
* collect_embeddings() runs only embed-capable pipelines.
* a TrackEmbedding persisted via DetectionStore with NO actor enrolled is
  resolved against a corpus enrolled AFTER THE FACT — the whole point of
  always-embed: retroactive identity with no re-inference over the frames.
"""

from __future__ import annotations

import numpy as np
import pytest
from kukiihome_preprocessor.detection_store import DetectionStore, EmbeddingRow
from kukiihome_preprocessor.pipelines.body_id import DetectedBody
from kukiihome_preprocessor.pipelines.gait import DetectedGait
from kukiihome_preprocessor.pipelines.identity import (
    BodyIdPipeline,
    EnrolledCorpus,
    GaitPipeline,
    PetPipeline,
    collect_embeddings,
    collect_track_embeddings,
    resolve_event,
)
from kukiihome_preprocessor.pipelines.identity.router import (
    EmbeddingPipeline,
    TemporalEmbeddingPipeline,
)
from kukiihome_preprocessor.pipelines.pet import DetectedPet
from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame
from kukiihome_shared.preprocessor import DetectionTag, TrackEmbedding


def _unit(vec) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    return arr / np.linalg.norm(arr)


def _real_jpeg(ts: float, w: int = 100, h: int = 100) -> BufferedFrame:
    import cv2

    img = np.full((h, w, 3), 128, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", img)
    assert ok
    return BufferedFrame(ts=ts, jpeg_bytes=jpeg.tobytes(), width=w, height=h)


def _person(track_id: str, ts: float = 1.0) -> DetectionTag:
    return DetectionTag(
        kind="person", confidence=0.9, bbox=(0.1, 0.1, 0.9, 0.9),
        track_id=track_id, frame_ts=ts,
    )


class _StubBodyIdRecognizer:
    """Returns canned DetectedBody rows; records the enrolled corpus it saw."""

    def __init__(self, bodies: tuple = ()) -> None:
        self._bodies = bodies
        self.enrolled_seen: list[tuple] = []

    async def identify_persons(self, bgr, persons, enrolled):
        self.enrolled_seen.append(tuple(sorted(enrolled.keys())))
        return self._bodies


# ─── embed() — always-embed, ungated ────────────────────────────────


@pytest.mark.asyncio
async def test_embed_returns_track_embedding_with_no_corpus():
    emb = _unit([1.0, 2.0, 3.0, 4.0])
    rec = _StubBodyIdRecognizer(
        bodies=(DetectedBody(track_id="t1", embedding=emb,
                             matched_actor_id=None, match_confidence=0.0),)
    )
    p = BodyIdPipeline(rec)
    out = await p.embed(frame=_real_jpeg(42.0), detections=(_person("t1", 42.0),))

    assert len(out) == 1
    te = out[0]
    assert isinstance(te, TrackEmbedding)
    assert te.modality == "body"
    assert te.match_method == "body_id_osnet"
    assert te.track_id == "t1"
    assert te.frame_ts == 42.0
    np.testing.assert_allclose(np.asarray(te.embedding, dtype=np.float32), emb, rtol=1e-6)
    # embed never consults the corpus: the recognizer saw an empty enrolled set.
    assert rec.enrolled_seen == [()]


@pytest.mark.asyncio
async def test_embed_drops_zero_vectors_and_untracked():
    rec = _StubBodyIdRecognizer(
        bodies=(
            DetectedBody(track_id="t1", embedding=np.zeros(4, dtype=np.float32),
                         matched_actor_id=None, match_confidence=0.0),
        )
    )
    p = BodyIdPipeline(rec)
    # zero embedding (degenerate crop) → dropped; never persist pure noise.
    assert await p.embed(frame=_real_jpeg(1.0), detections=(_person("t1"),)) == ()
    # untracked person → never reaches the recognizer.
    untracked = DetectionTag(kind="person", confidence=0.9, bbox=(0.0, 0.0, 1.0, 1.0),
                             track_id=None, frame_ts=1.0)
    rec.enrolled_seen.clear()
    assert await p.embed(frame=_real_jpeg(1.0), detections=(untracked,)) == ()
    assert rec.enrolled_seen == []


@pytest.mark.asyncio
async def test_body_pipeline_is_embedding_capable():
    p = BodyIdPipeline(_StubBodyIdRecognizer())
    assert isinstance(p, EmbeddingPipeline)


# ─── collect_embeddings — only embed-capable pipelines ──────────────


class _MatchOnlyPipeline:
    """An IdentityPipeline-shaped object with NO embed() (e.g. plate OCR)."""

    name = "plate_lpr"
    modality = "plate"
    triggers_on = frozenset({"vehicle"})


@pytest.mark.asyncio
async def test_collect_skips_pipelines_without_embed():
    emb = _unit([1.0, 0.0, 0.0])
    body = BodyIdPipeline(_StubBodyIdRecognizer(
        bodies=(DetectedBody(track_id="t1", embedding=emb,
                             matched_actor_id=None, match_confidence=0.0),)
    ))
    out = await collect_embeddings(
        [body, _MatchOnlyPipeline()],
        frame=_real_jpeg(1.0),
        detections=(_person("t1"),),
    )
    assert len(out) == 1
    assert out[0].match_method == "body_id_osnet"


# ─── resolve_event — the retroactive half of the loop ───────────────


def _persist(store: DetectionStore, event_id: str, cam: str, embeddings) -> None:
    store.register_event(event_id=event_id, camera_id=cam, captured_ts=10.0)
    store.add_embeddings([
        EmbeddingRow(
            event_id=event_id, camera_id=cam, track_id=te.track_id, frame_ts=te.frame_ts,
            modality=te.modality, match_method=te.match_method,
            embedding=np.asarray(te.embedding, dtype=np.float32),
        )
        for te in embeddings
    ])


@pytest.mark.asyncio
async def test_persist_with_no_actor_then_resolve_after_enrollment(tmp_path):
    """The headline loop: embed an unknown person now, name them later."""
    alice_vec = _unit([0.2, 0.9, 0.1, 0.3])
    body = BodyIdPipeline(_StubBodyIdRecognizer(
        bodies=(DetectedBody(track_id="t1", embedding=alice_vec,
                             matched_actor_id=None, match_confidence=0.0),)
    ))
    # 1. Embed + persist with an EMPTY corpus (nobody enrolled yet).
    embeddings = await body.embed(frame=_real_jpeg(7.0), detections=(_person("t1", 7.0),))
    store = DetectionStore(tmp_path / "det.db")
    _persist(store, "e1", "front_door", embeddings)

    # 2. Before enrollment, resolving against an empty corpus names nobody.
    assert resolve_event(store, "e1", EnrolledCorpus()) == ()

    # 3. Enroll Alice with the body template, then resolve retroactively.
    corpus = EnrolledCorpus(bodies={"alice": alice_vec})
    matches = resolve_event(store, "e1", corpus)
    assert len(matches) == 1
    m = matches[0]
    assert m.actor_id == "alice"
    assert m.match_method == "body_id_osnet"  # preserved through persist→resolve
    assert m.track_id == "t1"
    assert m.frame_ts == 7.0
    assert m.confidence == pytest.approx(1.0, abs=1e-3)


def test_resolve_below_threshold_names_nobody(tmp_path):
    stored = TrackEmbedding(modality="body", match_method="body_id_osnet",
                            track_id="t1", frame_ts=1.0,
                            embedding=tuple(_unit([1.0, 0.0, 0.0]).tolist()))
    store = DetectionStore(tmp_path / "det.db")
    _persist(store, "e1", "cam", [stored])
    # Near-orthogonal enrollment → cosine well under the 0.6 body threshold.
    corpus = EnrolledCorpus(bodies={"bob": _unit([0.0, 1.0, 0.0])})
    assert resolve_event(store, "e1", corpus) == ()
    # ...but a generous override threshold lets the weak match through.
    loose = resolve_event(store, "e1", corpus, thresholds={"body": -1.0})
    assert len(loose) == 1 and loose[0].actor_id == "bob"


# ─── pet: per-frame embed, same shape as body ───────────────────────


class _StubPetRecognizer:
    def __init__(self, pets: tuple = ()) -> None:
        self._pets = pets
        self.enrolled_seen: list[tuple] = []

    async def identify_pets(self, bgr, pets, enrolled):
        self.enrolled_seen.append(tuple(sorted(enrolled.keys())))
        return self._pets


def _dog(track_id: str, ts: float = 1.0) -> DetectionTag:
    return DetectionTag(kind="dog", confidence=0.9, bbox=(0.2, 0.2, 0.8, 0.8),
                        track_id=track_id, frame_ts=ts)


@pytest.mark.asyncio
async def test_pet_embed_ungated_and_resolves(tmp_path):
    rex = _unit([0.3, 0.7, 0.1, 0.2])
    pet = PetPipeline(_StubPetRecognizer(
        pets=(DetectedPet(track_id="d1", kind="dog", embedding=rex,
                          matched_actor_id=None, match_confidence=0.0),)
    ))
    assert isinstance(pet, EmbeddingPipeline)

    out = await pet.embed(frame=_real_jpeg(5.0), detections=(_dog("d1", 5.0),))
    assert len(out) == 1
    te = out[0]
    assert te.modality == "pet"
    assert te.match_method == "pet_dinov2"
    assert te.track_id == "d1"

    # persist with no pet enrolled → resolve after enrolling Rex.
    store = DetectionStore(tmp_path / "det.db")
    _persist(store, "e1", "yard", out)
    assert resolve_event(store, "e1", EnrolledCorpus(templates={"pet": {}})) == ()
    matches = resolve_event(store, "e1", EnrolledCorpus(templates={"pet": {"rex": rex}}))
    assert len(matches) == 1
    assert matches[0].actor_id == "rex"
    assert matches[0].match_method == "pet_dinov2"


@pytest.mark.asyncio
async def test_collect_embeddings_routes_person_and_pet(tmp_path):
    """Worker hands ALL tracked dets to collect_embeddings; each pipeline
    self-filters by triggers_on (body→person, pet→dog)."""
    body = BodyIdPipeline(_StubBodyIdRecognizer(
        bodies=(DetectedBody(track_id="p1", embedding=_unit([1.0, 0.0]),
                             matched_actor_id=None, match_confidence=0.0),)
    ))
    pet = PetPipeline(_StubPetRecognizer(
        pets=(DetectedPet(track_id="d1", kind="dog", embedding=_unit([0.0, 1.0]),
                          matched_actor_id=None, match_confidence=0.0),)
    ))
    out = await collect_embeddings(
        [body, pet], frame=_real_jpeg(1.0), detections=(_person("p1"), _dog("d1")),
    )
    assert {te.modality for te in out} == {"body", "pet"}


# ─── gait: temporal (sequence) embed ────────────────────────────────


class _StubGaitRecognizer:
    def __init__(self, gaits: tuple = ()) -> None:
        self._gaits = gaits
        self.enrolled_seen: list[tuple] = []

    async def identify_tracks(self, tracks, enrolled):
        self.enrolled_seen.append(tuple(sorted(enrolled.keys())))
        return self._gaits


@pytest.mark.asyncio
async def test_gait_embed_sequence_ungated_and_resolves(tmp_path):
    walk = _unit([0.1] * 8)
    rec = _StubGaitRecognizer(
        gaits=(DetectedGait(track_id="t1", embedding=walk, matched_actor_id=None,
                            match_confidence=0.0, frame_ts=9.0, n_silhouettes=18),)
    )
    gait = GaitPipeline(rec)
    # gait is temporal: NOT a per-frame EmbeddingPipeline, IS a TemporalEmbeddingPipeline.
    assert not isinstance(gait, EmbeddingPipeline)
    assert isinstance(gait, TemporalEmbeddingPipeline)

    seq = {"t1": ((_real_jpeg(7.0), (0.1, 0.1, 0.9, 0.9)),)}
    out = await gait.embed_sequence(tracks=seq)
    assert len(out) == 1
    te = out[0]
    assert te.modality == "gait"
    assert te.match_method == "gait_opengait"
    assert te.track_id == "t1"
    assert te.frame_ts == 9.0  # representative (last) frame, from the recognizer
    assert rec.enrolled_seen == [()]  # embed never consults a corpus

    store = DetectionStore(tmp_path / "det.db")
    _persist(store, "e1", "drive", out)
    matches = resolve_event(store, "e1", EnrolledCorpus(templates={"gait": {"alice": walk}}))
    assert len(matches) == 1
    assert matches[0].actor_id == "alice"
    assert matches[0].match_method == "gait_opengait"


@pytest.mark.asyncio
async def test_collect_track_embeddings_picks_only_temporal():
    body = BodyIdPipeline(_StubBodyIdRecognizer())  # per-frame, not temporal
    gait = GaitPipeline(_StubGaitRecognizer(
        gaits=(DetectedGait(track_id="t1", embedding=_unit([0.1] * 8),
                            matched_actor_id=None, match_confidence=0.0,
                            frame_ts=1.0, n_silhouettes=20),)
    ))
    seq = {"t1": ((_real_jpeg(1.0), (0.0, 0.0, 1.0, 1.0)),)}
    out = await collect_track_embeddings([body, gait], tracks=seq)
    assert len(out) == 1 and out[0].modality == "gait"


def test_resolve_only_requested_modalities(tmp_path):
    store = DetectionStore(tmp_path / "det.db")
    _persist(store, "e1", "cam", [
        TrackEmbedding(modality="body", match_method="body_id_osnet", track_id="t1",
                       frame_ts=1.0, embedding=tuple(_unit([1.0, 0.0]).tolist())),
        TrackEmbedding(modality="gait", match_method="gait_opengait", track_id="t1",
                       frame_ts=1.0, embedding=tuple(_unit([1.0, 0.0, 0.0]).tolist())),
    ])
    corpus = EnrolledCorpus(templates={
        "body": {"alice": _unit([1.0, 0.0])},
        "gait": {"alice": _unit([1.0, 0.0, 0.0])},
    })
    both = resolve_event(store, "e1", corpus)
    assert {m.match_method for m in both} == {"body_id_osnet", "gait_opengait"}
    body_only = resolve_event(store, "e1", corpus, modalities=["body"])
    assert {m.match_method for m in body_only} == {"body_id_osnet"}
