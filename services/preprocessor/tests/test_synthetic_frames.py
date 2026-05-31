"""Unit tests for the SyntheticFrameBuffer.

Determinism: same inputs → same outputs (critical for the FastAPI
tests). Out-of-config, out-of-horizon, and empty-window inputs all
produce empty FrameWindows rather than errors.
"""

from __future__ import annotations

import time

import pytest
from kukiihome_preprocessor.pipelines.synthetic_frames import SyntheticFrameBuffer
from kukiihome_preprocessor.state import ActorCache
from kukiihome_shared.preprocessor import ActorEnrollmentEvent


@pytest.fixture
def buf() -> SyntheticFrameBuffer:
    return SyntheticFrameBuffer(
        configured_cameras=["cam_a", "cam_b"],
        node_id="test",
        frames_per_second=2.0,
        buffer_horizon_seconds=300.0,
    )


# ─── Frame count + spacing ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_frame_count_matches_window_x_fps(buf: SyntheticFrameBuffer):
    """5-second window at 2 fps → 10 frames."""
    now = time.time()
    cache = ActorCache()
    fw = await buf.get_window(
        camera_id="cam_a", ts_start=now - 5.0, ts_end=now, enrich=False, cache=cache
    )
    assert len(fw.frames) == 10


@pytest.mark.asyncio
async def test_frames_are_evenly_spaced_starting_at_ts_start(
    buf: SyntheticFrameBuffer,
):
    now = time.time()
    cache = ActorCache()
    fw = await buf.get_window(
        camera_id="cam_a", ts_start=now - 2.0, ts_end=now, enrich=False, cache=cache
    )
    diffs = [fw.frames[i + 1].ts - fw.frames[i].ts for i in range(len(fw.frames) - 1)]
    assert all(abs(d - 0.5) < 1e-6 for d in diffs)


# ─── Out-of-config / out-of-horizon ──────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_camera_returns_empty(buf: SyntheticFrameBuffer):
    now = time.time()
    fw = await buf.get_window(
        camera_id="ghost_cam",
        ts_start=now - 1.0,
        ts_end=now,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.frames == ()
    assert fw.detections == ()


@pytest.mark.asyncio
async def test_old_window_returns_empty(buf: SyntheticFrameBuffer):
    """Anything outside the rolling buffer horizon (300s) is gone."""
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.frames == ()


@pytest.mark.asyncio
async def test_inverted_window_returns_empty(buf: SyntheticFrameBuffer):
    now = time.time()
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=now,
        ts_end=now - 1.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.frames == ()


# ─── Enrichment behavior ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_false_skips_quality_score_and_detections(
    buf: SyntheticFrameBuffer,
):
    now = time.time()
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=now - 1.0,
        ts_end=now,
        enrich=False,
        cache=ActorCache(),
    )
    assert fw.enrichment_mode == "frames_only"
    assert fw.detections == ()
    assert fw.actor_matches == ()
    for frame in fw.frames:
        assert frame.quality_score is None


@pytest.mark.asyncio
async def test_enrich_true_attaches_quality_score(buf: SyntheticFrameBuffer):
    now = time.time()
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=now - 1.0,
        ts_end=now,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.enrichment_mode == "enriched"
    for frame in fw.frames:
        assert frame.quality_score is not None
        assert 0.0 <= frame.quality_score <= 1.0


@pytest.mark.asyncio
async def test_actor_matches_require_populated_cache(buf: SyntheticFrameBuffer):
    """With empty cache: never any actor matches even when detections
    include person."""
    now = time.time()
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=now - 30.0,
        ts_end=now,
        enrich=True,
        cache=ActorCache(),
    )
    person_detections = [d for d in fw.detections if d.kind == "person"]
    # Even with potentially many person detections, none match
    # because cache is empty.
    assert fw.actor_matches == (), (
        f"Got {len(fw.actor_matches)} matches against empty cache; "
        f"{len(person_detections)} person detections were emitted"
    )


@pytest.mark.asyncio
async def test_actor_matches_appear_once_cache_populated(
    buf: SyntheticFrameBuffer,
):
    cache = ActorCache()
    await cache.upsert(
        ActorEnrollmentEvent(actor_id="actor_alice", action="enrolled", name="Alice")
    )
    now = time.time()
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=now - 60.0,  # large window → many person detections likely
        ts_end=now,
        enrich=True,
        cache=cache,
    )
    # 60 seconds at 2 fps = 120 frames; 35% are person-only + 3%
    # person+dog. Expected ~45 person detections; 40% match-rate
    # gives ~18 actor_matches. Assert at least one.
    assert len(fw.actor_matches) > 0
    assert all(m.actor_id == "actor_alice" for m in fw.actor_matches)
    # Every actor_match's frame_ts must correspond to a real frame.
    frame_ts = {f.ts for f in fw.frames}
    for m in fw.actor_matches:
        assert m.frame_ts in frame_ts


# ─── Determinism ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_inputs_produce_byte_identical_output(
    buf: SyntheticFrameBuffer,
):
    """Critical property: the buffer is stateless + deterministic on
    (camera_id, ts_start, ts_end). Re-calling produces the same
    FrameWindow modulo enrichment_latency_ms (which is a wall-clock
    measurement)."""
    # Use a fixed ts in the future-relative-to-skew so both calls
    # are within the horizon.
    base = time.time() - 10.0
    cache = ActorCache()
    fw1 = await buf.get_window(
        camera_id="cam_a",
        ts_start=base,
        ts_end=base + 1.0,
        enrich=True,
        cache=cache,
    )
    fw2 = await buf.get_window(
        camera_id="cam_a",
        ts_start=base,
        ts_end=base + 1.0,
        enrich=True,
        cache=cache,
    )
    assert fw1.frames == fw2.frames
    assert fw1.detections == fw2.detections
    assert fw1.actor_matches == fw2.actor_matches
