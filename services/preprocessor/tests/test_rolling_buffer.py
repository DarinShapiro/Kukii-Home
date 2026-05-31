"""Unit tests for the in-memory RollingBuffer.

No I/O — pure async data-structure testing. Covers writes, time-window
reads, horizon eviction, and exact-ts lookups (for the /frames route).
"""

from __future__ import annotations

import pytest
from kukiihome_preprocessor.pipelines.rolling_buffer import (
    BufferedFrame,
    RollingBuffer,
)


def _f(ts: float, *, size: int = 100) -> BufferedFrame:
    """Build a placeholder frame whose bytes payload is just ``b'x' * size``."""
    return BufferedFrame(ts=ts, jpeg_bytes=b"x" * size, width=1280, height=720)


# ─── Basic write + read ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_then_get_window_returns_in_chronological_order():
    buf = RollingBuffer(horizon_seconds=60.0)
    # Write out-of-order; get_window should still return in order.
    for ts in (105.0, 100.0, 110.0):
        await buf.write("cam_a", _f(ts))
    got = await buf.get_window("cam_a", ts_start=99.0, ts_end=111.0)
    # deque preserves insertion order; get_window doesn't sort, it
    # iterates. So the order is the insertion order. Pin that.
    assert [f.ts for f in got] == [105.0, 100.0, 110.0]


@pytest.mark.asyncio
async def test_get_window_filters_to_inclusive_bounds():
    buf = RollingBuffer(horizon_seconds=60.0)
    for ts in (100.0, 101.0, 102.0, 103.0, 104.0):
        await buf.write("cam_a", _f(ts))
    got = await buf.get_window("cam_a", ts_start=101.0, ts_end=103.0)
    assert [f.ts for f in got] == [101.0, 102.0, 103.0]


@pytest.mark.asyncio
async def test_get_window_unknown_camera_returns_empty():
    buf = RollingBuffer(horizon_seconds=60.0)
    got = await buf.get_window("ghost_cam", ts_start=0.0, ts_end=1000.0)
    assert got == ()


@pytest.mark.asyncio
async def test_get_window_inverted_returns_empty():
    buf = RollingBuffer(horizon_seconds=60.0)
    await buf.write("cam_a", _f(100.0))
    got = await buf.get_window("cam_a", ts_start=200.0, ts_end=100.0)
    assert got == ()


# ─── Horizon eviction ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_horizon_evicts_old_frames_on_subsequent_writes():
    """horizon_seconds=10, so anything older than (newest - 10)
    should drop out when a new frame arrives."""
    buf = RollingBuffer(horizon_seconds=10.0)
    await buf.write("cam_a", _f(100.0))
    await buf.write("cam_a", _f(105.0))
    await buf.write("cam_a", _f(115.0))  # newest; cutoff = 105
    # 100.0 is older than the cutoff → evicted. 105.0 is at the
    # cutoff (>= 105) → stays.
    assert await buf.size("cam_a") == 2
    got = await buf.get_window("cam_a", ts_start=0.0, ts_end=1000.0)
    assert [f.ts for f in got] == [105.0, 115.0]


@pytest.mark.asyncio
async def test_max_entries_caps_buffer_even_without_horizon_evict():
    """max_entries_per_camera caps the deque even if every frame is
    inside the horizon. Protects against a single noisy camera
    blowing memory."""
    buf = RollingBuffer(horizon_seconds=3600.0, max_entries_per_camera=5)
    for ts in range(10):
        await buf.write("cam_a", _f(float(ts)))
    # First 5 fell off; last 5 retained.
    sz = await buf.size("cam_a")
    assert sz == 5
    got = await buf.get_window("cam_a", ts_start=0.0, ts_end=100.0)
    assert [f.ts for f in got] == [5.0, 6.0, 7.0, 8.0, 9.0]


# ─── Per-camera isolation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cameras_are_independent():
    buf = RollingBuffer(horizon_seconds=60.0)
    await buf.write("cam_a", _f(100.0))
    await buf.write("cam_b", _f(200.0))
    a_got = await buf.get_window("cam_a", ts_start=0.0, ts_end=1000.0)
    b_got = await buf.get_window("cam_b", ts_start=0.0, ts_end=1000.0)
    assert [f.ts for f in a_got] == [100.0]
    assert [f.ts for f in b_got] == [200.0]


# ─── get_at + size + total_bytes ─────────────────────────────────────


@pytest.mark.asyncio
async def test_get_at_returns_exact_ts_frame():
    buf = RollingBuffer(horizon_seconds=60.0)
    await buf.write("cam_a", _f(100.0))
    await buf.write("cam_a", _f(100.5))
    got = await buf.get_at("cam_a", 100.5)
    assert got is not None
    assert got.ts == 100.5


@pytest.mark.asyncio
async def test_get_at_returns_none_for_missing_ts():
    buf = RollingBuffer(horizon_seconds=60.0)
    await buf.write("cam_a", _f(100.0))
    got = await buf.get_at("cam_a", 999.0)
    assert got is None


@pytest.mark.asyncio
async def test_total_bytes_sums_payload_sizes():
    buf = RollingBuffer(horizon_seconds=60.0)
    await buf.write("cam_a", _f(100.0, size=50))
    await buf.write("cam_a", _f(101.0, size=75))
    await buf.write("cam_b", _f(100.0, size=200))
    assert await buf.total_bytes("cam_a") == 125
    assert await buf.total_bytes("cam_b") == 200
    assert await buf.total_bytes() == 325


@pytest.mark.asyncio
async def test_last_frame_ts_tracks_newest():
    buf = RollingBuffer(horizon_seconds=60.0)
    assert await buf.last_frame_ts("cam_a") is None
    await buf.write("cam_a", _f(100.0))
    await buf.write("cam_a", _f(105.0))
    assert await buf.last_frame_ts("cam_a") == 105.0


# ─── Validation ──────────────────────────────────────────────────────


def test_rejects_invalid_horizon():
    with pytest.raises(ValueError, match="horizon_seconds"):
        RollingBuffer(horizon_seconds=0)


def test_rejects_invalid_max_entries():
    with pytest.raises(ValueError, match="max_entries"):
        RollingBuffer(horizon_seconds=60.0, max_entries_per_camera=0)


# ─── AnnotationCache ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_annotation_cache_put_and_get():
    from kukiihome_preprocessor.pipelines.rolling_buffer import AnnotationCache

    c = AnnotationCache(horizon_seconds=60.0)
    await c.put("cam_a", 100.0, b"jpeg-bytes-here")
    got = await c.get("cam_a", 100.0)
    assert got == b"jpeg-bytes-here"


@pytest.mark.asyncio
async def test_annotation_cache_get_missing_returns_none():
    from kukiihome_preprocessor.pipelines.rolling_buffer import AnnotationCache

    c = AnnotationCache(horizon_seconds=60.0)
    assert await c.get("ghost", 999.0) is None


@pytest.mark.asyncio
async def test_annotation_cache_overwrite_replaces_value():
    from kukiihome_preprocessor.pipelines.rolling_buffer import AnnotationCache

    c = AnnotationCache(horizon_seconds=60.0)
    await c.put("cam_a", 100.0, b"v1")
    await c.put("cam_a", 100.0, b"v2")
    assert await c.get("cam_a", 100.0) == b"v2"


@pytest.mark.asyncio
async def test_annotation_cache_size_and_total_bytes():
    from kukiihome_preprocessor.pipelines.rolling_buffer import AnnotationCache

    c = AnnotationCache(horizon_seconds=60.0)
    await c.put("cam_a", 100.0, b"x" * 100)
    await c.put("cam_b", 200.0, b"y" * 250)
    assert await c.size() == 2
    assert await c.total_bytes() == 350


def test_annotation_cache_rejects_invalid_horizon():
    from kukiihome_preprocessor.pipelines.rolling_buffer import AnnotationCache

    with pytest.raises(ValueError, match="horizon_seconds"):
        AnnotationCache(horizon_seconds=0)
