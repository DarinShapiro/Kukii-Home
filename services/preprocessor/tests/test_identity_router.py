"""Unit tests for IdentityRouter + EnrolledCorpus.

The router is a pure dispatcher — no model calls, no I/O. These
tests exercise its gating + concurrent dispatch with stub pipelines
that record what they were invoked with.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
from sentihome_preprocessor.pipelines.identity.router import (
    EnrolledCorpus,
    IdentityRouter,
)
from sentihome_preprocessor.pipelines.rolling_buffer import BufferedFrame
from sentihome_preprocessor.state import ActorCache
from sentihome_shared.preprocessor import (
    ActorEnrollmentEvent,
    ActorMatch,
    DetectionTag,
)

# ─── Fixtures + stubs ────────────────────────────────────────────────


def _frame(ts: float) -> BufferedFrame:
    return BufferedFrame(ts=ts, jpeg_bytes=b"\xff\xd8\xff\xd9", width=100, height=100)


def _det(kind: str, ts: float, track_id: str | None = "t1") -> DetectionTag:
    return DetectionTag(
        kind=kind,
        confidence=0.9,
        bbox=(0.0, 0.0, 1.0, 1.0),
        frame_ts=ts,
        track_id=track_id,
    )


class _StubPipeline:
    """Records invocations; returns a fixed ActorMatch per call.

    The Protocol is structural so this satisfies IdentityPipeline by
    duck-typing — no explicit subclassing needed.
    """

    def __init__(
        self,
        name: str,
        triggers_on: frozenset[str],
        has_enroll: bool = True,
        delay_seconds: float = 0.0,
    ) -> None:
        self.name = name
        self.triggers_on = triggers_on
        self._has_enroll = has_enroll
        self._delay = delay_seconds
        self.calls: list[tuple[float, tuple[str, ...]]] = []
        # (frame_ts, det_kinds)

    def has_enrollments(self, corpus: EnrolledCorpus) -> bool:
        _ = corpus
        return self._has_enroll

    async def run(self, *, frame, detections, corpus):
        _ = corpus
        if self._delay:
            await asyncio.sleep(self._delay)
        self.calls.append((frame.ts, tuple(d.kind for d in detections)))
        return (
            ActorMatch(
                actor_id=f"actor_for_{self.name}",
                confidence=0.9,
                match_method=self.name,
                frame_ts=frame.ts,
                track_id="t1",
            ),
        )


# ─── EnrolledCorpus ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_corpus_projects_face_embeddings_from_cache():
    cache = ActorCache()
    await cache.upsert(
        ActorEnrollmentEvent(
            actor_id="alice",
            action="enrolled",
            name="Alice",
            face_embedding=(1.0, 0.0, 0.0),
        )
    )
    await cache.upsert(
        ActorEnrollmentEvent(
            actor_id="bob",
            action="enrolled",
            name="Bob",
            face_embedding=(0.0, 1.0, 0.0),
        )
    )

    corpus = await EnrolledCorpus.from_cache(cache)
    assert set(corpus.faces.keys()) == {"alice", "bob"}
    np.testing.assert_allclose(corpus.faces["alice"], [1.0, 0.0, 0.0])
    assert corpus.actor_names == {"alice": "Alice", "bob": "Bob"}
    assert corpus.pets == {}
    assert corpus.plates == {}


@pytest.mark.asyncio
async def test_corpus_skips_actors_without_face_embedding():
    """A pet-only actor (face_embedding=None) is in actor_names but
    not in faces. Lets face-pipeline gate cleanly."""
    cache = ActorCache()
    await cache.upsert(
        ActorEnrollmentEvent(
            actor_id="rex",
            action="enrolled",
            name="Rex",
            pet_dinov2_centroid=(0.1, 0.2, 0.3),
        )
    )
    corpus = await EnrolledCorpus.from_cache(cache)
    assert corpus.faces == {}
    assert "rex" in corpus.pets
    assert corpus.actor_names == {"rex": "Rex"}


# ─── IdentityRouter dispatch ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_router_with_no_pipelines_returns_empty():
    router = IdentityRouter([])
    out = await router.identify(
        buffered=[_frame(1.0)],
        detections=(_det("person", 1.0),),
        cache=ActorCache(),
    )
    assert out == ()


@pytest.mark.asyncio
async def test_router_with_no_detections_skips_dispatch():
    p = _StubPipeline("face_arcface", frozenset({"person"}))
    router = IdentityRouter([p])
    out = await router.identify(buffered=[_frame(1.0)], detections=(), cache=ActorCache())
    assert out == ()
    assert p.calls == []


@pytest.mark.asyncio
async def test_router_skips_pipeline_with_no_kind_overlap():
    """Vehicle-only frame doesn't invoke the face pipeline."""
    face = _StubPipeline("face_arcface", frozenset({"person"}))
    router = IdentityRouter([face])
    out = await router.identify(
        buffered=[_frame(1.0)],
        detections=(_det("vehicle", 1.0),),
        cache=ActorCache(),
    )
    assert out == ()
    assert face.calls == []


@pytest.mark.asyncio
async def test_router_skips_pipeline_with_empty_enrollments():
    """Face pipeline triggers on person, but corpus has no enrolled
    face. Router skips before calling pipeline.run()."""
    face = _StubPipeline("face_arcface", frozenset({"person"}), has_enroll=False)
    router = IdentityRouter([face])
    out = await router.identify(
        buffered=[_frame(1.0)],
        detections=(_det("person", 1.0),),
        cache=ActorCache(),
    )
    assert out == ()
    assert face.calls == []


@pytest.mark.asyncio
async def test_router_invokes_pipeline_with_filtered_detections():
    """Pipeline only sees detections whose kind is in triggers_on —
    a person+car frame routed to face_arcface only sees the person."""
    face = _StubPipeline("face_arcface", frozenset({"person"}))
    router = IdentityRouter([face])
    out = await router.identify(
        buffered=[_frame(1.0)],
        detections=(_det("person", 1.0), _det("vehicle", 1.0)),
        cache=ActorCache(),
    )
    assert len(out) == 1
    assert out[0].match_method == "face_arcface"
    assert len(face.calls) == 1
    _ts, kinds = face.calls[0]
    assert kinds == ("person",)  # vehicle filtered out


@pytest.mark.asyncio
async def test_router_dispatches_disjoint_branches_in_parallel():
    """Face (person) + plate (vehicle) on the same frame: both run.
    They have no kind overlap so they can run concurrently.
    """
    face = _StubPipeline("face_arcface", frozenset({"person"}), delay_seconds=0.10)
    plate = _StubPipeline("plate_lpr", frozenset({"vehicle"}), delay_seconds=0.10)
    router = IdentityRouter([face, plate])

    import time

    t0 = time.perf_counter()
    out = await router.identify(
        buffered=[_frame(1.0)],
        detections=(_det("person", 1.0), _det("vehicle", 1.0)),
        cache=ActorCache(),
    )
    elapsed = time.perf_counter() - t0

    assert len(out) == 2
    methods = {m.match_method for m in out}
    assert methods == {"face_arcface", "plate_lpr"}
    # Parallel: ~0.10s wall-clock, not ~0.20s. Give generous slack
    # for executor + event-loop overhead on slow CI runners.
    assert elapsed < 0.18, f"expected parallel ~0.10s, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_router_processes_multiple_frames_independently():
    """Two frames each with a person detection -> face pipeline
    invoked twice, once per frame."""
    face = _StubPipeline("face_arcface", frozenset({"person"}))
    router = IdentityRouter([face])
    await router.identify(
        buffered=[_frame(1.0), _frame(2.0)],
        detections=(_det("person", 1.0), _det("person", 2.0)),
        cache=ActorCache(),
    )
    assert {c[0] for c in face.calls} == {1.0, 2.0}


@pytest.mark.asyncio
async def test_router_skips_detection_without_buffered_frame():
    """Detection at ts=5.0 but only ts=1.0 is in the buffer -> the
    orphan detection is silently dropped (no frame to run on)."""
    face = _StubPipeline("face_arcface", frozenset({"person"}))
    router = IdentityRouter([face])
    out = await router.identify(
        buffered=[_frame(1.0)],
        detections=(_det("person", 5.0),),  # no matching frame
        cache=ActorCache(),
    )
    assert out == ()
    assert face.calls == []


@pytest.mark.asyncio
async def test_router_exposes_pipeline_names_for_telemetry():
    face = _StubPipeline("face_arcface", frozenset({"person"}))
    plate = _StubPipeline("plate_lpr", frozenset({"vehicle"}))
    router = IdentityRouter([face, plate])
    assert router.pipeline_names == ("face_arcface", "plate_lpr")
