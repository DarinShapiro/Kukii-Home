"""RTSPDirectAdapter — direct RTSP ingest, no NVR.

The actual RTSP decode + frame buffering is delegated to the
kukiihome.preprocessor service (which is the in-process "native" implementation
for this adapter — see §03.5). This module's job is to:

1. Maintain a list of configured cameras
2. Translate the unified ``NVRAdapter`` contract into preprocessor calls
3. Subscribe to ONVIF events from cameras that support them and forward as
   ``MotionEvent`` push notifications

For Epic 3 we ship the contract + lifecycle wiring + a frame buffer protocol.
The actual decode loop lives in the preprocessor (Epic 4), which fulfills frame
window queries against its rolling buffer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import structlog
from kukiihome_shared.adapter import NVRAdapter, PreprocessingMode
from kukiihome_shared.adapter.base import (
    CameraCapability,
    FrameWindow,
    MotionEvent,
)

logger = structlog.get_logger(__name__)


@dataclass
class CameraConfig:
    """Per-camera RTSP + ONVIF configuration."""

    camera_id: str
    rtsp_url: str
    name: str | None = None
    substream_url: str | None = None
    onvif_url: str | None = None
    """Optional ONVIF event subscription endpoint. If None, this adapter
    relies on the preprocessor's own motion detection (see §08)."""
    width: int | None = None
    height: int | None = None
    fps: int = 10
    supports_ptz: bool = False
    audio: bool = False


class FrameBuffer(Protocol):
    """Protocol for the preprocessor's frame buffer.

    The real implementation lives in services/preprocessor (Epic 4). For
    unit tests, supply a fake. The adapter only depends on this interface.
    """

    async def get_window(
        self,
        camera_id: str,
        ts_start: datetime,
        ts_end: datetime,
        *,
        with_metadata: bool,
    ) -> FrameWindow:
        """Return frames + metadata for a time window."""
        ...

    async def subscribe(self, camera_id: str | None) -> AsyncIterator[MotionEvent]:
        """Push notifications when motion / on-camera AI events fire."""
        ...


class RTSPDirectAdapter(NVRAdapter):
    """Adapter for direct-RTSP cameras (no NVR).

    Behavior:
    - ``list_cameras`` returns the configured camera list
    - ``get_frame_window`` delegates to the frame buffer (preprocessor)
    - ``subscribe_motion_events`` merges ONVIF events + preprocessor motion
    - ``get_stream_url`` returns the configured RTSP URL
    - PTZ + profile switching: opt-in per camera via ``supports_ptz``
    """

    def __init__(
        self,
        cameras: list[CameraConfig],
        *,
        frame_buffer: FrameBuffer | None = None,
    ) -> None:
        self._cameras: dict[str, CameraConfig] = {c.camera_id: c for c in cameras}
        self._frame_buffer = frame_buffer
        self._started = False

    @property
    def name(self) -> str:
        return "adapter-rtsp-direct"

    @property
    def mode(self) -> PreprocessingMode:
        return PreprocessingMode.DIRECT

    async def list_cameras(self) -> list[CameraCapability]:
        return [
            CameraCapability(
                camera_id=c.camera_id,
                name=c.name,
                preprocessing_mode=PreprocessingMode.DIRECT,
                has_on_camera_ai=c.onvif_url is not None,
                supported_events=("motion",),
                max_resolution=(c.width, c.height) if (c.width and c.height) else None,
                fps=c.fps,
                ptz=c.supports_ptz,
                audio=c.audio,
                stream_profiles=("main", "substream") if c.substream_url else ("main",),
                rtsp_url=c.rtsp_url,
            )
            for c in self._cameras.values()
        ]

    async def get_frame_window(
        self,
        camera_id: str,
        ts_start: datetime,
        ts_end: datetime,
        *,
        with_metadata: bool = True,
    ) -> FrameWindow:
        self._require_camera(camera_id)
        if self._frame_buffer is None:
            # No preprocessor wired up yet (e.g., during bootstrap).
            return FrameWindow(
                camera_id=camera_id,
                ts_start=ts_start,
                ts_end=ts_end,
                frames=[],
                metadata={
                    "preprocessing_mode": "direct",
                    "preprocessing_latency_ms": 0,
                    "note": "no frame buffer configured",
                },
            )
        return await self._frame_buffer.get_window(
            camera_id,
            ts_start,
            ts_end,
            with_metadata=with_metadata,
        )

    async def subscribe_motion_events(
        self,
        camera_id: str | None = None,
    ) -> AsyncIterator[MotionEvent]:
        if camera_id is not None:
            self._require_camera(camera_id)

        if self._frame_buffer is None:
            return  # nothing to subscribe to yet

        async for event in self._frame_buffer.subscribe(camera_id):
            yield event

    async def get_stream_url(self, camera_id: str, profile: str = "main") -> str:
        config = self._require_camera(camera_id)
        if profile == "substream" and config.substream_url:
            return config.substream_url
        return config.rtsp_url

    async def start(self) -> None:
        if self._started:
            return
        logger.info(
            "rtsp_direct.start",
            cameras=list(self._cameras),
            mode=self.mode.value,
        )
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        logger.info("rtsp_direct.stop")
        self._started = False

    def _require_camera(self, camera_id: str) -> CameraConfig:
        if camera_id not in self._cameras:
            raise KeyError(f"Unknown camera_id: {camera_id}")
        return self._cameras[camera_id]
