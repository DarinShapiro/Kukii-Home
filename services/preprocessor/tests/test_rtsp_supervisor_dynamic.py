"""Tests for RTSPCaptureSupervisor's hot add/remove behavior.

We don't open any real RTSP connections — the supervisor accepts a
URL and constructs a CameraCaptureTask which would attempt PyAV
.open() in a thread. For the dynamic-management unit tests we just
verify the supervisor's bookkeeping (tasks dict, lifecycle) without
waiting for actual frame ingestion.

The capture task's start() spawns an asyncio task that runs the
PyAV connect in a thread; with an unreachable URL the task will
fail-and-backoff, but the supervisor doesn't care — its own state
is consistent regardless. We yield briefly to let the asyncio
scaffolding settle, then assert.
"""

from __future__ import annotations

import asyncio

import pytest
from sentihome_preprocessor.pipelines.rolling_buffer import RollingBuffer
from sentihome_preprocessor.pipelines.rtsp_capture import RTSPCaptureSupervisor


@pytest.fixture
async def supervisor() -> RTSPCaptureSupervisor:
    return RTSPCaptureSupervisor(buffer=RollingBuffer(horizon_seconds=60.0))


@pytest.mark.asyncio
async def test_add_starts_a_capture_task(supervisor: RTSPCaptureSupervisor):
    await supervisor.add(camera_id="cam_a", rtsp_url="rtsp://unreachable/sub")
    assert supervisor.camera_ids() == ("cam_a",)
    # Clean up before the test exits so the dangling task is cancelled.
    await supervisor.remove("cam_a")


@pytest.mark.asyncio
async def test_add_with_existing_camera_replaces_url(
    supervisor: RTSPCaptureSupervisor,
):
    await supervisor.add(camera_id="cam_a", rtsp_url="rtsp://old/sub")
    await supervisor.add(camera_id="cam_a", rtsp_url="rtsp://new/sub")
    # Still one task, same id.
    assert supervisor.camera_ids() == ("cam_a",)
    snap = supervisor.state_snapshot()
    assert len(snap) == 1
    # The sanitized URL surfaced via state reflects the latest URL.
    assert "new" in snap[0].rtsp_url_sanitized
    await supervisor.remove("cam_a")


@pytest.mark.asyncio
async def test_remove_returns_true_then_false(
    supervisor: RTSPCaptureSupervisor,
):
    await supervisor.add(camera_id="cam_a", rtsp_url="rtsp://unreachable/sub")
    assert await supervisor.remove("cam_a") is True
    assert await supervisor.remove("cam_a") is False
    assert supervisor.camera_ids() == ()


@pytest.mark.asyncio
async def test_multiple_cameras_managed_independently(
    supervisor: RTSPCaptureSupervisor,
):
    await supervisor.add(camera_id="cam_a", rtsp_url="rtsp://unreachable/a")
    await supervisor.add(camera_id="cam_b", rtsp_url="rtsp://unreachable/b")
    assert supervisor.camera_ids() == ("cam_a", "cam_b")
    await supervisor.remove("cam_a")
    assert supervisor.camera_ids() == ("cam_b",)
    await supervisor.remove("cam_b")
    assert supervisor.camera_ids() == ()


@pytest.mark.asyncio
async def test_stop_cancels_all_tasks(supervisor: RTSPCaptureSupervisor):
    await supervisor.add(camera_id="cam_a", rtsp_url="rtsp://unreachable/a")
    await supervisor.add(camera_id="cam_b", rtsp_url="rtsp://unreachable/b")
    await supervisor.stop()
    assert supervisor.camera_ids() == ()


@pytest.mark.asyncio
async def test_concurrent_add_remove_keeps_bookkeeping_consistent(
    supervisor: RTSPCaptureSupervisor,
):
    """Hammer the supervisor with concurrent mutations — verify the
    lock keeps the tasks dict consistent."""
    await asyncio.gather(
        supervisor.add(camera_id="cam_a", rtsp_url="rtsp://unreachable/a"),
        supervisor.add(camera_id="cam_b", rtsp_url="rtsp://unreachable/b"),
        supervisor.add(camera_id="cam_c", rtsp_url="rtsp://unreachable/c"),
    )
    assert set(supervisor.camera_ids()) == {"cam_a", "cam_b", "cam_c"}

    await asyncio.gather(
        supervisor.remove("cam_a"),
        supervisor.remove("cam_c"),
        # And a concurrent add to verify add+remove don't deadlock:
        supervisor.add(camera_id="cam_d", rtsp_url="rtsp://unreachable/d"),
    )
    assert set(supervisor.camera_ids()) == {"cam_b", "cam_d"}
    await supervisor.stop()
