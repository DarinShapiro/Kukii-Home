"""Per-camera RTSP capture task.

Opens the camera's H.264 sub-stream via PyAV, decodes one keyframe per
second (matching typical sub-stream GOP cadence), JPEG-encodes it, and
writes to the :class:`RollingBuffer`. Handles disconnects with bounded
exponential backoff.

Why H.264 sub-stream: cheap to decode (5 streams concurrently is
trivial CPU), sufficient for motion gating + general object detection
+ VLM grounding. Main-stream pulls for face/plate detail are deferred
to Phase 10.4.

This module deliberately keeps no state outside the RollingBuffer —
on process restart the buffer is gone; the capture tasks re-open RTSP
and start refilling. That's the right semantics for an in-memory ring
buffer (we don't persist; a separate Phase 10.x feature can layer
disk archival on top later).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import av
import cv2
import numpy as np
import structlog

from sentihome_preprocessor.pipelines.rolling_buffer import (
    BufferedFrame,
    RollingBuffer,
)

logger = structlog.get_logger(__name__)


# Backoff for failed RTSP connects. Capped so we don't drift to
# minute-long retries — the more important property is to come back
# quickly when the camera comes back online.
_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 30.0
_BACKOFF_FACTOR = 2.0

# JPEG encode quality. 75 is a sane tradeoff between bytes and
# visible quality at 720p; the consumer rarely needs better than
# this for VLM grounding.
_JPEG_QUALITY = 75


@dataclass
class CameraCaptureState:
    """Per-task health snapshot, exposed via /status."""

    camera_id: str
    rtsp_url_sanitized: str
    """RTSP URL with credentials stripped — safe to surface to /status."""

    connected: bool = False
    last_frame_ts: float | None = None
    frames_captured_total: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    started_ts: float = field(default_factory=time.time)


class CameraCaptureTask:
    """A single camera's continuous RTSP→buffer task.

    One per camera, managed by :class:`RTSPCaptureSupervisor`. The
    task runs forever until cancelled; it self-heals on RTSP errors
    with backoff.
    """

    def __init__(
        self,
        *,
        camera_id: str,
        rtsp_url: str,
        buffer: RollingBuffer,
        target_interval_seconds: float = 1.0,
    ) -> None:
        if not rtsp_url:
            raise ValueError(f"empty RTSP url for camera {camera_id!r}")
        self._camera_id = camera_id
        self._rtsp_url = rtsp_url
        self._buffer = buffer
        self._target_interval = target_interval_seconds
        self.state = CameraCaptureState(
            camera_id=camera_id,
            rtsp_url_sanitized=_sanitize_url(rtsp_url),
        )
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run(), name=f"rtsp-capture-{self._camera_id}"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    # ─── internals ────────────────────────────────────────────────

    async def _run(self) -> None:
        backoff = _BACKOFF_INITIAL_S
        try:
            while True:
                try:
                    await self._capture_loop()
                    # Normal exit only happens if the stream ends —
                    # treat that as a transient and reconnect.
                    backoff = _BACKOFF_INITIAL_S
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.state.connected = False
                    self.state.consecutive_failures += 1
                    self.state.last_error = str(e)
                    logger.warning(
                        "preprocessor.rtsp.connect_failed",
                        camera_id=self._camera_id,
                        error=str(e),
                        backoff_s=backoff,
                        consecutive_failures=self.state.consecutive_failures,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX_S)
        except asyncio.CancelledError:
            logger.info("preprocessor.rtsp.task_cancelled", camera_id=self._camera_id)
            raise

    async def _capture_loop(self) -> None:
        """One connect-decode session. Runs until the stream errors
        or ends; the outer loop reconnects with backoff."""
        loop = asyncio.get_running_loop()
        # PyAV's container.open + decode iterator are blocking; run
        # in a thread so we don't stall the event loop.

        def _drive_stream() -> None:
            container = av.open(
                self._rtsp_url,
                # FFmpeg options. rtsp_transport=tcp is more reliable
                # than the default UDP for most home cameras.
                options={
                    "rtsp_transport": "tcp",
                    "stimeout": "5000000",  # 5s socket timeout (µs)
                },
            )
            try:
                self.state.connected = True
                self.state.consecutive_failures = 0
                self.state.last_error = None
                logger.info(
                    "preprocessor.rtsp.connected",
                    camera_id=self._camera_id,
                    url=self.state.rtsp_url_sanitized,
                )

                video_stream = next(
                    s for s in container.streams if s.type == "video"
                )
                last_capture_ts = 0.0
                for frame in container.decode(video_stream):
                    now = time.time()
                    if now - last_capture_ts < self._target_interval:
                        continue
                    last_capture_ts = now

                    img = frame.to_ndarray(format="bgr24")
                    ok, jpeg = cv2.imencode(
                        ".jpg",
                        img,
                        [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY],
                    )
                    if not ok:
                        continue

                    height, width = img.shape[:2]
                    buffered = BufferedFrame(
                        ts=round(now, 3),
                        jpeg_bytes=jpeg.tobytes(),
                        width=int(width),
                        height=int(height),
                    )
                    # Hand off to the event loop's RollingBuffer.
                    asyncio.run_coroutine_threadsafe(
                        self._write(buffered), loop
                    )
            finally:
                container.close()

        await loop.run_in_executor(None, _drive_stream)

    async def _write(self, frame: BufferedFrame) -> None:
        await self._buffer.write(self._camera_id, frame)
        self.state.last_frame_ts = frame.ts
        self.state.frames_captured_total += 1


class RTSPCaptureSupervisor:
    """Owns the capture tasks for every configured camera.

    Started once at process boot, stopped on shutdown. The
    individual ``CameraCaptureTask.state`` instances are exposed via
    the FastAPI ``/status`` route.
    """

    def __init__(
        self, *, camera_urls: dict[str, str], buffer: RollingBuffer
    ) -> None:
        self._buffer = buffer
        self._tasks: dict[str, CameraCaptureTask] = {
            cam: CameraCaptureTask(
                camera_id=cam, rtsp_url=url, buffer=buffer
            )
            for cam, url in camera_urls.items()
        }

    async def start(self) -> None:
        for task in self._tasks.values():
            await task.start()
        logger.info(
            "preprocessor.rtsp.supervisor_started",
            cameras=sorted(self._tasks.keys()),
        )

    async def stop(self) -> None:
        await asyncio.gather(
            *(t.stop() for t in self._tasks.values()),
            return_exceptions=True,
        )

    def state_snapshot(self) -> tuple[CameraCaptureState, ...]:
        return tuple(
            self._tasks[cam].state for cam in sorted(self._tasks.keys())
        )


# ─── helpers ─────────────────────────────────────────────────────────


def _sanitize_url(url: str) -> str:
    """Strip credentials from an rtsp URL for safe logging/surface."""
    # Quick parse — full urllib.parse handles RTSP but doesn't
    # cleanly strip just the userinfo. Manual split is fine.
    if "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    if "@" in rest:
        _, _, after_at = rest.partition("@")
        return f"{scheme}://***@{after_at}"
    return url


_ = np  # silence unused-import lint; numpy comes in transitively via cv2
