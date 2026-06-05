"""EventRecorder state-machine + persistence tests.

Drives the recorder with a fake rolling buffer and a fake frame buffer (no
torch, no RTSP) so the [t-10, t+30] window logic — open on motion, extend on
new motion, close after post-roll, cap at max-duration, persist the FULL
window including stationary frames — is verified deterministically.
"""

from __future__ import annotations

import json

import pytest
from kukiihome_preprocessor.pipelines.event_recorder import (
    EventRecorder,
    EventRecorderConfig,
)
from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame


class FakeRolling:
    """Holds frames in a list; get_window filters by ts like the real one."""

    def __init__(self, frames: list[BufferedFrame]):
        self.frames = frames

    async def get_window(self, camera_id, *, ts_start, ts_end):
        return tuple(f for f in self.frames if ts_start <= f.ts <= ts_end)


class FakeFrameWindow:
    def __init__(self, detections=(), identified_entities=()):
        self.detections = detections
        self.identified_entities = identified_entities


class FakeFrameBuffer:
    """Records the get_window calls so we can assert enrich_motion_only=False."""

    def __init__(self):
        self.calls = []

    async def get_window(
        self, *, camera_id, ts_start, ts_end, enrich, cache, enrich_motion_only=None
    ):
        self.calls.append(
            {
                "ts_start": ts_start,
                "ts_end": ts_end,
                "enrich": enrich,
                "motion_only": enrich_motion_only,
            }
        )
        return FakeFrameWindow()


def _frame(ts: float, motion: bool) -> BufferedFrame:
    return BufferedFrame(ts=ts, jpeg_bytes=b"\xff\xd8jpeg", width=8, height=8, has_motion=motion)


def _recorder(tmp_path, frames, **cfg):
    rolling = FakeRolling(frames)
    fb = FakeFrameBuffer()
    rec = EventRecorder(
        rolling_buffer=rolling,
        frame_buffer=fb,
        cache=object(),
        cameras=["pool"],
        config=EventRecorderConfig(
            store_dir=tmp_path, pre_roll_s=10, post_roll_s=30, max_duration_s=180, **cfg
        ),
    )
    return rec, fb


async def _poll_each_second(rec, lo, hi):
    """Simulate the real 1s poll cadence over [lo, hi]."""
    for now in range(lo, hi + 1):
        await rec._tick("pool", now=float(now))


@pytest.mark.asyncio
async def test_motion_opens_window_with_pre_roll(tmp_path):
    # Static then motion at t=100..105. Per-second polling catches onset at 100.
    frames = [_frame(t, motion=(100 <= t <= 105)) for t in range(85, 141)]
    rec, fb = _recorder(tmp_path, frames)
    await _poll_each_second(rec, 86, 140)
    await rec.drain()
    assert rec.events_written == 1
    # window = [trigger-10, last_motion+30] = [90, 135]; enriched ALL frames
    call = fb.calls[-1]
    assert call["ts_start"] == pytest.approx(90)
    assert call["ts_end"] == pytest.approx(135)
    assert call["motion_only"] is False  # the whole point: stationary analyzed


@pytest.mark.asyncio
async def test_new_motion_extends_window(tmp_path):
    # Motion at 100-101, then again at 120-121 (within post-roll) -> extends.
    frames = [_frame(t, motion=(t in (100, 101, 120, 121))) for t in range(85, 200)]
    rec, fb = _recorder(tmp_path, frames)
    await _poll_each_second(rec, 86, 160)
    await rec.drain()
    assert rec.events_written == 1
    assert fb.calls[-1]["ts_end"] == pytest.approx(151)  # 121 + 30


@pytest.mark.asyncio
async def test_max_duration_caps_event(tmp_path):
    # Continuous motion -> force-close at max_duration even without a quiet gap.
    frames = [_frame(t, motion=True) for t in range(0, 400)]
    rec, _ = _recorder(tmp_path, frames)
    await rec._tick("pool", now=5)  # open at trigger=0
    await rec._tick("pool", now=190)  # duration 190 > max 180 -> close (capped)
    await rec.drain()
    assert rec.events_written == 1


@pytest.mark.asyncio
async def test_persists_frames_and_manifest(tmp_path):
    frames = [_frame(t, motion=(100 <= t <= 103)) for t in range(85, 141)]
    rec, _ = _recorder(tmp_path, frames)
    await rec._tick("pool", now=104)
    await rec._tick("pool", now=140)
    await rec.drain()
    ev_dirs = list((tmp_path / "pool").glob("pool_*"))
    assert len(ev_dirs) == 1
    ev = ev_dirs[0]
    manifest = json.loads((ev / "event.json").read_text())
    assert manifest["schema_version"] == "event.v1"
    assert manifest["camera_id"] == "pool"
    assert manifest["frame_count"] > 0
    assert manifest["motion_frame_count"] == 4
    # frames written to disk, one per buffered frame, + frame_index in manifest
    jpgs = list(ev.glob("frame_*.jpg"))
    assert len(jpgs) == manifest["frame_count"]
    assert len(manifest["frame_index"]) == manifest["frame_count"]


@pytest.mark.asyncio
async def test_enrich_disabled_persists_but_skips_detection(tmp_path):
    # enrich=False (CPU mode): frames persisted durably, but NO get_window
    # enrich call (which would starve capture on CPU).
    frames = [_frame(t, motion=(100 <= t <= 103)) for t in range(85, 141)]
    rec, fb = _recorder(tmp_path, frames, enrich=False)
    await rec._tick("pool", now=104)
    await rec._tick("pool", now=140)
    await rec.drain()
    assert rec.events_written == 1  # still persisted
    assert fb.calls == []  # but never enriched
    ev = next((tmp_path / "pool").glob("pool_*"))
    manifest = json.loads((ev / "event.json").read_text())
    assert manifest["enriched"] is False
    assert len(list(ev.glob("frame_*.jpg"))) == manifest["frame_count"]


@pytest.mark.asyncio
async def test_no_motion_no_event(tmp_path):
    frames = [_frame(t, motion=False) for t in range(0, 60)]
    rec, fb = _recorder(tmp_path, frames)
    await rec._tick("pool", now=10)
    await rec._tick("pool", now=60)
    assert rec.events_written == 0
    assert fb.calls == []
