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
        depends_on: tuple[str, ...] = (),
        skip_when_upstream_matched_above: float | None = None,
        match_confidence: float = 0.9,
        match_actor_id: str | None = None,
    ) -> None:
        self.name = name
        self.triggers_on = triggers_on
        self.depends_on = depends_on
        self.skip_when_upstream_matched_above = skip_when_upstream_matched_above
        self._has_enroll = has_enroll
        self._delay = delay_seconds
        self._match_confidence = match_confidence
        self._match_actor_id = match_actor_id or f"actor_for_{name}"
        self.calls: list[tuple[float, tuple[str, ...], tuple[str | None, ...]]] = []
        # (frame_ts, det_kinds, det_track_ids)

    def has_enrollments(self, corpus: EnrolledCorpus) -> bool:
        _ = corpus
        return self._has_enroll

    async def run(self, *, frame, detections, corpus):
        _ = corpus
        if self._delay:
            await asyncio.sleep(self._delay)
        self.calls.append(
            (
                frame.ts,
                tuple(d.kind for d in detections),
                tuple(d.track_id for d in detections),
            )
        )
        # Emit one match per (tracked) detection so chain tests can
        # inspect per-track behavior.
        out: list[ActorMatch] = []
        for d in detections:
            if d.track_id is None:
                continue
            out.append(
                ActorMatch(
                    actor_id=self._match_actor_id,
                    confidence=self._match_confidence,
                    match_method=self.name,
                    frame_ts=frame.ts,
                    track_id=d.track_id,
                )
            )
        return tuple(out)


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


def test_corpus_slice_supports_arbitrary_new_modality():
    """Pluggability keystone (10.11.1): a NEW modality plugs in via the
    generic templates map + slice() with zero edits to EnrolledCorpus.
    An unknown modality returns an empty slice, not KeyError."""
    corpus = EnrolledCorpus(
        templates={"gait": {"darin": np.array([0.1, 0.2], dtype=np.float32)}},
        actor_names={"darin": "Darin"},
    )
    assert set(corpus.slice("gait").keys()) == {"darin"}
    assert corpus.slice("shape3d") == {}  # unknown modality → empty, no raise
    assert corpus.actor_names == {"darin": "Darin"}


def test_corpus_legacy_kwargs_and_slice_are_equivalent():
    """The legacy faces=/bodies=/pets= kwargs and the per-modality
    accessors are thin views over the same generic store."""
    corpus = EnrolledCorpus(
        faces={"a": np.array([1.0])},
        bodies={"b": np.array([2.0])},
        pets={"c": np.array([3.0])},
    )
    assert set(corpus.slice("face").keys()) == {"a"}
    assert set(corpus.slice("body").keys()) == {"b"}
    assert "c" in corpus.pets
    assert corpus.slice("plate") == {} == corpus.plates


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
    _ts, kinds, _track_ids = face.calls[0]
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


# ─── Phase 10.5.1: depends_on + short-circuit chains ────────────────


def _det_with_track(kind: str, ts: float, track_id: str) -> DetectionTag:
    return DetectionTag(
        kind=kind,
        confidence=0.9,
        bbox=(0.0, 0.0, 1.0, 1.0),
        frame_ts=ts,
        track_id=track_id,
    )


def test_branch_summary_groups_dependent_pipelines_into_one_chain():
    """face + body_id (depends_on face) collapse to one branch;
    plate is independent. Branch summary surfaces this for ops."""
    face = _StubPipeline("face_arcface", frozenset({"person"}))
    body = _StubPipeline(
        "body_id_osnet",
        frozenset({"person"}),
        depends_on=("face_arcface",),
        skip_when_upstream_matched_above=0.85,
    )
    plate = _StubPipeline("plate_lpr", frozenset({"vehicle"}))
    router = IdentityRouter([face, body, plate])
    assert router.branch_summary == (
        ("face_arcface", "body_id_osnet"),
        ("plate_lpr",),
    )


def test_branch_summary_unmet_dep_becomes_singleton_branch():
    """body_id depends on face but face isn't registered. Router
    treats the missing dep as already-satisfied -> body_id is its
    own branch (runs on every triggering det, no short-circuit
    skip since there's no upstream to compare against)."""
    body = _StubPipeline(
        "body_id_osnet",
        frozenset({"person"}),
        depends_on=("face_arcface",),
        skip_when_upstream_matched_above=0.85,
    )
    router = IdentityRouter([body])
    assert router.branch_summary == (("body_id_osnet",),)


@pytest.mark.asyncio
async def test_chain_skips_downstream_for_high_confidence_upstream_match():
    """Face matches t1 at 0.91 (above body's 0.85 skip threshold).
    Body should not run for t1 — saves the inference cost."""
    face = _StubPipeline("face_arcface", frozenset({"person"}), match_confidence=0.91)
    body = _StubPipeline(
        "body_id_osnet",
        frozenset({"person"}),
        depends_on=("face_arcface",),
        skip_when_upstream_matched_above=0.85,
    )
    router = IdentityRouter([face, body])
    out = await router.identify(
        buffered=[_frame(1.0)],
        detections=(_det_with_track("person", 1.0, "t1"),),
        cache=ActorCache(),
    )
    # Face ran; body was triggered (kind matches + enrollments
    # exist) but received zero detections after short-circuit.
    assert len(face.calls) == 1
    # Body either didn't run at all (relevant filter dropped all
    # dets so run was skipped), or ran with empty input. Either is
    # a valid skip outcome; what matters is no body match emitted.
    assert all(m.match_method == "face_arcface" for m in out)


@pytest.mark.asyncio
async def test_chain_runs_downstream_when_upstream_confidence_below_threshold():
    """Face matches t1 at 0.70 (below body's 0.85 skip threshold).
    Body should still run for t1 — the fallback case."""
    face = _StubPipeline("face_arcface", frozenset({"person"}), match_confidence=0.70)
    body = _StubPipeline(
        "body_id_osnet",
        frozenset({"person"}),
        depends_on=("face_arcface",),
        skip_when_upstream_matched_above=0.85,
        match_actor_id="alice_from_body",
    )
    router = IdentityRouter([face, body])
    out = await router.identify(
        buffered=[_frame(1.0)],
        detections=(_det_with_track("person", 1.0, "t1"),),
        cache=ActorCache(),
    )
    methods = {m.match_method for m in out}
    assert methods == {"face_arcface", "body_id_osnet"}


@pytest.mark.asyncio
async def test_chain_skips_only_track_ids_face_matched_confidently():
    """Two persons in frame: face nails t1 at 0.92 but misses t2.
    Body should skip t1 and run only for t2 — the per-track
    granularity the chain enables."""
    body = _StubPipeline(
        "body_id_osnet",
        frozenset({"person"}),
        depends_on=("face_arcface",),
        skip_when_upstream_matched_above=0.85,
        match_actor_id="someone_body",
    )

    # Custom stub: face only emits for t1 (t2 left unmatched).
    class _SelectiveFace:
        name = "face_arcface"
        triggers_on = frozenset({"person"})
        depends_on: tuple[str, ...] = ()
        skip_when_upstream_matched_above: float | None = None

        def __init__(self) -> None:
            self.calls: list = []

        def has_enrollments(self, corpus):
            return True

        async def run(self, *, frame, detections, corpus):
            from sentihome_shared.preprocessor import ActorMatch

            self.calls.append(tuple(d.track_id for d in detections))
            # Only emit for t1.
            return tuple(
                ActorMatch(
                    actor_id="alice",
                    confidence=0.92,
                    match_method="face_arcface",
                    frame_ts=frame.ts,
                    track_id=d.track_id,
                )
                for d in detections
                if d.track_id == "t1"
            )

    selective_face = _SelectiveFace()
    router = IdentityRouter([selective_face, body])
    await router.identify(
        buffered=[_frame(1.0)],
        detections=(
            _det_with_track("person", 1.0, "t1"),
            _det_with_track("person", 1.0, "t2"),
        ),
        cache=ActorCache(),
    )
    # Body was called once, with only t2 in its input.
    assert len(body.calls) == 1
    _ts, _kinds, track_ids = body.calls[0]
    assert track_ids == ("t2",)


@pytest.mark.asyncio
async def test_independent_branches_run_in_parallel_with_chain():
    """face+body chain runs concurrently with plate. Wall-clock =
    max(chain_total, plate)."""
    import time

    face = _StubPipeline(
        "face_arcface",
        frozenset({"person"}),
        delay_seconds=0.05,
        match_confidence=0.50,  # below body's skip threshold
    )
    body = _StubPipeline(
        "body_id_osnet",
        frozenset({"person"}),
        depends_on=("face_arcface",),
        skip_when_upstream_matched_above=0.85,
        delay_seconds=0.05,
        match_actor_id="alice_body",
    )
    plate = _StubPipeline("plate_lpr", frozenset({"vehicle"}), delay_seconds=0.05)
    router = IdentityRouter([face, body, plate])

    t0 = time.perf_counter()
    out = await router.identify(
        buffered=[_frame(1.0)],
        detections=(
            _det_with_track("person", 1.0, "t1"),
            _det_with_track("vehicle", 1.0, "tv1"),
        ),
        cache=ActorCache(),
    )
    elapsed = time.perf_counter() - t0

    methods = {m.match_method for m in out}
    assert methods == {"face_arcface", "body_id_osnet", "plate_lpr"}
    # Chain (face 0.05 + body 0.05 = 0.10) runs concurrently with
    # plate (0.05). Wall-clock ~= 0.10, not 0.15. Generous slack
    # for executor overhead.
    assert elapsed < 0.14, f"expected ~0.10s parallel, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_chain_skip_does_not_affect_other_frames():
    """Face nails t1 in frame 1 only. Body should still run for t1
    in frame 2 (matches are per-frame, not persistent across the
    window)."""

    # Face only matches in frame 1 — use a custom stub for
    # frame-conditional behavior.
    class _F:
        name = "face_arcface"
        triggers_on = frozenset({"person"})
        depends_on: tuple[str, ...] = ()
        skip_when_upstream_matched_above: float | None = None

        def has_enrollments(self, corpus):
            return True

        async def run(self, *, frame, detections, corpus):
            from sentihome_shared.preprocessor import ActorMatch

            if frame.ts == 1.0:
                return tuple(
                    ActorMatch(
                        actor_id="alice",
                        confidence=0.95,
                        match_method="face_arcface",
                        frame_ts=frame.ts,
                        track_id=d.track_id,
                    )
                    for d in detections
                )
            return ()

    body = _StubPipeline(
        "body_id_osnet",
        frozenset({"person"}),
        depends_on=("face_arcface",),
        skip_when_upstream_matched_above=0.85,
    )
    router = IdentityRouter([_F(), body])
    await router.identify(
        buffered=[_frame(1.0), _frame(2.0)],
        detections=(
            _det_with_track("person", 1.0, "t1"),
            _det_with_track("person", 2.0, "t1"),
        ),
        cache=ActorCache(),
    )
    # Body called once total — only for frame 2 (skipped in frame 1
    # because face matched above threshold there).
    assert len(body.calls) == 1
    frame_ts, _kinds, track_ids = body.calls[0]
    assert frame_ts == 2.0
    assert track_ids == ("t1",)
