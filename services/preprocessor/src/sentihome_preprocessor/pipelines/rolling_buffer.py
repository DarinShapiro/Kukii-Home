"""In-memory rolling buffer of JPEG-encoded keyframes per camera.

The preprocessor's continuous-ingestion side (RTSP capture task) writes
``(ts, jpeg_bytes)`` entries here; the ``/frame_window`` RPC side reads
a time-slice back out.

Design notes:

* **Per-camera independent buffers**. One camera dropping its stream
  doesn't evict another's keyframes. Each camera has its own deque +
  lock.
* **Two-pronged eviction**. We trim on every write by
  (a) ``max_entries_per_camera`` so a single noisy camera can't blow
  out RAM, and (b) ``horizon_seconds`` so out-of-window entries are
  released even when no new writes arrive.
* **Read snapshots, never references**. ``get_window`` copies the
  matching entries out under the lock so the caller can iterate
  without worrying about concurrent eviction.
* **Bytes are immutable** — the buffer never re-encodes. The capture
  task does the JPEG encode once at ingest time.

The size estimate from our planning math: 5 cameras x ~50 KB JPEG x
300 entries (5 min @ 1/sec) ~= 75 MB. Well within container limits.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class BufferedFrame:
    """One stored keyframe."""

    ts: float
    """Unix-seconds the frame was captured (camera-side timestamp if
    we can get it, otherwise the moment we wrote it to the buffer)."""

    jpeg_bytes: bytes
    """JPEG-encoded frame. Always non-empty."""

    width: int
    height: int

    has_motion: bool = False
    """Set by the capture task when the upstream MOG2 motion detector
    decides this frame contains real motion. ``False`` for frames
    captured when nothing moved (steady-state quiet scene). The
    RTSPFrameBuffer can use this to skip YOLO inference on quiet
    frames — typically ~85% of frames in a residential camera feed."""


class RollingBuffer:
    """Per-camera time-windowed ring buffer."""

    def __init__(
        self,
        *,
        horizon_seconds: float = 300.0,
        max_entries_per_camera: int = 1024,
    ) -> None:
        if horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if max_entries_per_camera <= 0:
            raise ValueError("max_entries_per_camera must be positive")
        self._horizon = horizon_seconds
        self._max_per_cam = max_entries_per_camera
        self._cams: dict[str, deque[BufferedFrame]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _ensure_cam(self, camera_id: str) -> tuple[deque[BufferedFrame], asyncio.Lock]:
        if camera_id not in self._cams:
            self._cams[camera_id] = deque(maxlen=self._max_per_cam)
            self._locks[camera_id] = asyncio.Lock()
        return self._cams[camera_id], self._locks[camera_id]

    async def write(self, camera_id: str, frame: BufferedFrame) -> None:
        """Insert a frame. Older frames outside the horizon are
        evicted as a side effect."""
        buf, lock = self._ensure_cam(camera_id)
        cutoff = frame.ts - self._horizon
        async with lock:
            # Evict everything older than the horizon. deque is FIFO
            # so this is just a popleft loop.
            while buf and buf[0].ts < cutoff:
                buf.popleft()
            buf.append(frame)

    async def get_window(
        self, camera_id: str, *, ts_start: float, ts_end: float
    ) -> tuple[BufferedFrame, ...]:
        """Return all buffered frames within ``[ts_start, ts_end]`` in
        chronological order.

        Empty tuple for unknown cameras, inverted windows, or windows
        with no matching frames. Snapshots the matching entries — the
        caller owns the returned tuple."""
        if camera_id not in self._cams or ts_end <= ts_start:
            return ()
        buf, lock = self._cams[camera_id], self._locks[camera_id]
        async with lock:
            return tuple(f for f in buf if ts_start <= f.ts <= ts_end)

    async def last_frame_ts(self, camera_id: str) -> float | None:
        """Wall-clock timestamp of the most-recent frame, or None if
        the buffer for this camera is empty."""
        if camera_id not in self._cams:
            return None
        buf, lock = self._cams[camera_id], self._locks[camera_id]
        async with lock:
            return buf[-1].ts if buf else None

    async def size(self, camera_id: str) -> int:
        if camera_id not in self._cams:
            return 0
        buf, lock = self._cams[camera_id], self._locks[camera_id]
        async with lock:
            return len(buf)

    async def total_bytes(self, camera_id: str | None = None) -> int:
        """Approximate JPEG-bytes footprint. Useful for ``/status`` +
        sizing dashboards. Cheap O(N) scan of pointers — does NOT
        re-read the JPEG payloads."""
        cams = [camera_id] if camera_id else list(self._cams.keys())
        total = 0
        for cam in cams:
            if cam not in self._cams:
                continue
            buf, lock = self._cams[cam], self._locks[cam]
            async with lock:
                total += sum(len(f.jpeg_bytes) for f in buf)
        return total

    async def get_at(self, camera_id: str, ts: float) -> BufferedFrame | None:
        """Look up an exact-ts frame. Used by ``GET /frames/{cam}/{ts}.jpg``
        to serve a single keyframe the caller pulled by URI."""
        if camera_id not in self._cams:
            return None
        buf, lock = self._cams[camera_id], self._locks[camera_id]
        async with lock:
            for f in buf:
                if f.ts == ts:
                    return f
        return None
