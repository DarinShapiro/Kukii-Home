"""End-to-end RTSP-NVR mode integration test.

Spins up a media server in a container (``bluenviron/mediamtx`` with
publisher mode disabled — we'll publish a test stream into it from
the test process via ``ffmpeg`` so we don't need an actual IP camera).

Then runs a real :class:`CameraCaptureTask` against the served URL
and verifies:

* The capture task connects, decodes frames, and writes
  ``BufferedFrame`` entries into the :class:`RollingBuffer`.
* A subsequent ``RTSPFrameBuffer.get_window`` call returns those
  frames.
* The bytes returned by ``serve_frame`` are valid JPEG.

Marked ``integration``; skips cleanly without Docker or ffmpeg.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import socket
import subprocess
import time

import pytest
from kukiihome_preprocessor.pipelines.rolling_buffer import RollingBuffer
from kukiihome_preprocessor.pipelines.rtsp_capture import CameraCaptureTask
from kukiihome_preprocessor.pipelines.rtsp_frame_buffer import RTSPFrameBuffer
from kukiihome_preprocessor.state import ActorCache


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def mediamtx_url():
    """Run mediamtx on an ephemeral host port. We publish a synthetic
    H.264 stream INTO mediamtx from the test process; the capture
    task pulls from the same stream out."""
    if not _docker_available():
        pytest.skip("Docker not reachable; integration tests need Docker.")
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not on PATH; needed to publish a test RTSP stream.")

    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = DockerContainer("bluenviron/mediamtx:1.8.4").with_exposed_ports(8554)
    container.start()
    try:
        wait_for_logs(container, "RTSP listener opened", timeout=30)
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(8554))
        yield (host, port)
    finally:
        container.stop()


def _find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def ffmpeg_publisher(mediamtx_url):
    """Background ffmpeg that publishes a synthetic H.264 720p stream
    into the mediamtx instance at path ``/test_cam``."""
    host, port = mediamtx_url
    publish_url = f"rtsp://{host}:{port}/test_cam"
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=15",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-g",
            "15",  # keyframe every second @ 15 fps
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            publish_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    # Give ffmpeg a moment to actually start streaming.
    time.sleep(3.0)
    try:
        yield publish_url
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()


@pytest.mark.asyncio
async def test_capture_task_writes_frames_into_rolling_buffer(
    ffmpeg_publisher: str,
):
    rolling = RollingBuffer(horizon_seconds=60.0)
    task = CameraCaptureTask(
        camera_id="cam_a",
        rtsp_url=ffmpeg_publisher,
        buffer=rolling,
        target_interval_seconds=0.5,  # tighter than default to hit assertions fast
    )
    await task.start()

    # Wait up to 15s for at least a few frames.
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if await rolling.size("cam_a") >= 2:
            break
        await asyncio.sleep(0.5)

    await task.stop()

    sz = await rolling.size("cam_a")
    assert sz >= 2, f"expected ≥2 buffered frames, got {sz}"
    assert task.state.frames_captured_total >= 2
    assert task.state.connected is True or task.state.last_frame_ts is not None


@pytest.mark.asyncio
async def test_rtsp_frame_buffer_serves_real_jpegs(ffmpeg_publisher: str):
    """End-to-end: capture → rolling buffer → RTSPFrameBuffer.get_window
    → RTSPFrameBuffer.serve_frame returns valid JPEG bytes."""
    rolling = RollingBuffer(horizon_seconds=60.0)
    task = CameraCaptureTask(
        camera_id="cam_a",
        rtsp_url=ffmpeg_publisher,
        buffer=rolling,
        target_interval_seconds=0.5,
    )
    await task.start()
    try:
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if await rolling.size("cam_a") >= 2:
                break
            await asyncio.sleep(0.5)

        frame_buffer = RTSPFrameBuffer(
            rolling_buffer=rolling,
            configured_cameras=["cam_a"],
            node_id="test",
            external_base_url="http://example:8090",
        )
        fw = await frame_buffer.get_window(
            camera_id="cam_a",
            ts_start=time.time() - 30.0,
            ts_end=time.time(),
            enrich=True,
            cache=ActorCache(),
        )
        assert len(fw.frames) >= 2

        # Fetch the first frame's bytes via serve_frame; verify JPEG
        # magic bytes (0xFF 0xD8 0xFF).
        first = fw.frames[0]
        data = await frame_buffer.serve_frame("cam_a", first.ts)
        assert data is not None and len(data) > 0
        assert data[:3] == b"\xff\xd8\xff", "expected JPEG SOI marker"
    finally:
        await task.stop()
