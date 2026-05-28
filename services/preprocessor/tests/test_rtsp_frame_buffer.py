"""Unit tests for the RTSPFrameBuffer.

The buffer reads from a RollingBuffer that's pre-populated by the
test (skipping the real RTSP capture path; that's covered separately
by integration tests against a media-server testcontainer).
"""

from __future__ import annotations

import pytest
from sentihome_preprocessor.pipelines.rolling_buffer import (
    BufferedFrame,
    RollingBuffer,
)
from sentihome_preprocessor.pipelines.rtsp_frame_buffer import RTSPFrameBuffer
from sentihome_preprocessor.state import ActorCache


@pytest.fixture
async def rolling() -> RollingBuffer:
    return RollingBuffer(horizon_seconds=3600.0)


@pytest.fixture
async def buf(rolling: RollingBuffer) -> RTSPFrameBuffer:
    return RTSPFrameBuffer(
        rolling_buffer=rolling,
        configured_cameras=["cam_a", "cam_b"],
        node_id="test",
        external_base_url="http://example:8090",
    )


def _f(ts: float, *, size: int = 100, w: int = 1280, h: int = 720) -> BufferedFrame:
    return BufferedFrame(ts=ts, jpeg_bytes=b"x" * size, width=w, height=h)


# ─── get_window ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_window_returns_frames_from_rolling_buffer(
    rolling: RollingBuffer, buf: RTSPFrameBuffer
):
    for ts in (100.0, 101.0, 102.0):
        await rolling.write("cam_a", _f(ts))
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=100.0,
        ts_end=102.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert [f.ts for f in fw.frames] == [100.0, 101.0, 102.0]
    assert fw.camera_id == "cam_a"
    assert fw.preprocessor_node_id == "test"


@pytest.mark.asyncio
async def test_get_window_emits_absolute_uris_using_external_base_url(
    rolling: RollingBuffer, buf: RTSPFrameBuffer
):
    await rolling.write("cam_a", _f(123.456))
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert len(fw.frames) == 1
    assert fw.frames[0].uri == "http://example:8090/frames/cam_a/123.456.jpg"


@pytest.mark.asyncio
async def test_get_window_unknown_camera_empty(buf: RTSPFrameBuffer):
    fw = await buf.get_window(
        camera_id="ghost_cam",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.frames == ()


@pytest.mark.asyncio
async def test_get_window_inverted_window_empty(buf: RTSPFrameBuffer):
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=100.0,
        ts_end=50.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.frames == ()


@pytest.mark.asyncio
async def test_get_window_no_enrichment_in_phase_10_1_5(
    rolling: RollingBuffer, buf: RTSPFrameBuffer
):
    """RTSPFrameBuffer doesn't compute detections / actor matches
    against real frames yet — those wire in Phase 10.3+. Until then,
    the contract is: frames present, enrichment fields empty."""
    await rolling.write("cam_a", _f(100.0))
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=99.0,
        ts_end=101.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.detections == ()
    assert fw.actor_matches == ()


@pytest.mark.asyncio
async def test_get_window_records_latency(
    rolling: RollingBuffer, buf: RTSPFrameBuffer
):
    await rolling.write("cam_a", _f(100.0))
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.enrichment_latency_ms >= 0


# ─── serve_frame ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serve_frame_returns_bytes_for_buffered_ts(
    rolling: RollingBuffer, buf: RTSPFrameBuffer
):
    await rolling.write("cam_a", _f(123.456, size=42))
    data = await buf.serve_frame("cam_a", 123.456)
    assert data is not None
    assert len(data) == 42


@pytest.mark.asyncio
async def test_serve_frame_unknown_camera_returns_none(buf: RTSPFrameBuffer):
    assert await buf.serve_frame("ghost_cam", 100.0) is None


@pytest.mark.asyncio
async def test_serve_frame_missing_ts_returns_none(
    rolling: RollingBuffer, buf: RTSPFrameBuffer
):
    await rolling.write("cam_a", _f(100.0))
    assert await buf.serve_frame("cam_a", 999.0) is None
