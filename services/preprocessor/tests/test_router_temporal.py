"""Router temporal-dispatch tests (Epic 10.11.6).

Temporal pipelines (gait) run once per window over a per-track frame
sequence, AFTER the per-frame branch barrier, and are gated by the
per-frame matches (skip tracks face already nailed). These tests use
fake pipelines so no real models are needed. They assert:

* the per-frame path is untouched (branches exclude temporal pipelines)
* run_sequence receives the track->sequence index built from detections
* tracks an upstream matched above the skip threshold are dropped
* the temporal pipeline is gated by has_enrollments + window kinds
"""

from __future__ import annotations

import numpy as np
import pytest
from kukiihome_preprocessor.pipelines.identity.router import IdentityRouter
from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame
from kukiihome_preprocessor.state import ActorCache
from kukiihome_shared.preprocessor import ActorEnrollmentEvent, ActorMatch, DetectionTag


def _frame(ts: float) -> BufferedFrame:
    return BufferedFrame(ts=ts, jpeg_bytes=b"x", width=100, height=100, has_motion=True)


def _det(track_id: str, ts: float, kind: str = "person") -> DetectionTag:
    return DetectionTag(
        kind=kind, confidence=0.9, bbox=(0.1, 0.1, 0.5, 0.9), track_id=track_id, frame_ts=ts
    )


class _FakeFacePipeline:
    """Per-frame pipeline that emits a configured match per track."""

    name = "face_arcface"
    modality = "face"
    triggers_on = frozenset({"person"})
    depends_on: tuple[str, ...] = ()
    skip_when_upstream_matched_above = None
    resource_class = "gpu"
    temporal = False

    def __init__(self, matches_by_track: dict[str, float]) -> None:
        self._matches = matches_by_track

    def has_enrollments(self, corpus) -> bool:
        return True

    async def run(self, *, frame, detections, corpus):
        out = []
        for d in detections:
            if d.track_id in self._matches:
                out.append(
                    ActorMatch(
                        actor_id="alice",
                        confidence=self._matches[d.track_id],
                        match_method="face_arcface",
                        frame_ts=frame.ts,
                        track_id=d.track_id,
                    )
                )
        return tuple(out)


class _FakeTemporalPipeline:
    name = "gait_opengait"
    modality = "gait"
    triggers_on = frozenset({"person"})
    depends_on: tuple[str, ...] = ()
    skip_when_upstream_matched_above: float | None = 0.85
    resource_class = "gpu"
    temporal = True

    def __init__(self, *, enrolled: bool = True) -> None:
        self._enrolled = enrolled
        self.seen_tracks: dict | None = None

    def has_enrollments(self, corpus) -> bool:
        return self._enrolled

    async def run(self, *, frame, detections, corpus):
        return ()

    async def run_sequence(self, *, tracks, corpus):
        self.seen_tracks = tracks
        # Emit a gait match for every track it was handed.
        return tuple(
            ActorMatch(
                actor_id="bob",
                confidence=0.5,
                match_method="gait_opengait",
                frame_ts=seq[-1][0].ts,
                track_id=tid,
            )
            for tid, seq in tracks.items()
        )


async def _cache_with_gait() -> ActorCache:
    cache = ActorCache()
    await cache.upsert(
        ActorEnrollmentEvent(
            actor_id="bob",
            action="enrolled",
            name="Bob",
            gait_embedding=tuple(np.zeros(4096).tolist()),
        )
    )
    return cache


def test_temporal_pipeline_excluded_from_branches():
    router = IdentityRouter([_FakeFacePipeline({}), _FakeTemporalPipeline()])
    # branch_summary covers only per-frame pipelines.
    assert router.branch_summary == (("face_arcface",),)
    # but pipeline_names lists everything registered.
    assert set(router.pipeline_names) == {"face_arcface", "gait_opengait"}


@pytest.mark.asyncio
async def test_run_sequence_receives_track_index():
    temporal = _FakeTemporalPipeline()
    router = IdentityRouter([_FakeFacePipeline({}), temporal])
    buffered = (_frame(1.0), _frame(2.0))
    dets = (_det("t1", 1.0), _det("t1", 2.0))
    cache = await _cache_with_gait()

    matches = await router.identify(buffered=buffered, detections=dets, cache=cache)

    assert temporal.seen_tracks is not None
    assert set(temporal.seen_tracks) == {"t1"}
    seq = temporal.seen_tracks["t1"]
    # Two frames, chronological, each a (frame, bbox) pair.
    assert [f.ts for f, _bbox in seq] == [1.0, 2.0]
    assert all(len(pair) == 2 for pair in seq)
    # The gait match flowed out.
    assert any(m.match_method == "gait_opengait" and m.track_id == "t1" for m in matches)


@pytest.mark.asyncio
async def test_face_nailed_track_skipped_by_temporal():
    temporal = _FakeTemporalPipeline()
    # Face matches t1 at 0.9 (>= 0.85 skip) but not t2.
    router = IdentityRouter([_FakeFacePipeline({"t1": 0.9}), temporal])
    buffered = (_frame(1.0),)
    dets = (_det("t1", 1.0), _det("t2", 1.0))
    cache = await _cache_with_gait()

    await router.identify(buffered=buffered, detections=dets, cache=cache)

    assert temporal.seen_tracks is not None
    # t1 was nailed by face -> gait only runs on t2.
    assert set(temporal.seen_tracks) == {"t2"}


@pytest.mark.asyncio
async def test_temporal_skipped_when_no_gait_enrolled():
    temporal = _FakeTemporalPipeline(enrolled=False)
    router = IdentityRouter([_FakeFacePipeline({}), temporal])
    buffered = (_frame(1.0),)
    dets = (_det("t1", 1.0),)
    cache = await _cache_with_gait()

    await router.identify(buffered=buffered, detections=dets, cache=cache)
    assert temporal.seen_tracks is None  # never invoked


@pytest.mark.asyncio
async def test_temporal_only_router_runs_without_frame_pipelines():
    temporal = _FakeTemporalPipeline()
    router = IdentityRouter([temporal])
    buffered = (_frame(1.0),)
    dets = (_det("t1", 1.0),)
    cache = await _cache_with_gait()

    matches = await router.identify(buffered=buffered, detections=dets, cache=cache)
    assert temporal.seen_tracks == {"t1": ((buffered[0], (0.1, 0.1, 0.5, 0.9)),)}
    assert any(m.match_method == "gait_opengait" for m in matches)
