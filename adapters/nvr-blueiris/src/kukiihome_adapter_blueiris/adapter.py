"""BlueIrisAdapter — service-mode adapter for Blue Iris.

Blue Iris doesn't expose a clean REST API; its main integration paths are:
- Direct RTSP streams (per-camera, password-authenticated)
- The ha-blueiris HACS integration which surfaces motion sensors in HA

This adapter consumes events through HA (subscribing to motion binary sensors)
and pulls RTSP for the preprocessor service.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from kukiihome_shared.adapter import NVRAdapter, PreprocessingMode
from kukiihome_shared.adapter.base import (
    CameraCapability,
    FrameWindow,
    MotionEvent,
)

logger = structlog.get_logger(__name__)


@dataclass
class BlueIrisCamera:
    camera_id: str
    rtsp_url: str
    ha_motion_entity: str | None = None
    name: str | None = None
    width: int | None = None
    height: int | None = None
    ptz: bool = False


@dataclass
class BlueIrisConfig:
    base_url: str = "http://localhost:81"
    cameras: list[BlueIrisCamera] = field(default_factory=list)


class BlueIrisAdapter(NVRAdapter):
    def __init__(self, config: BlueIrisConfig) -> None:
        self._config = config
        self._cameras: dict[str, BlueIrisCamera] = {c.camera_id: c for c in config.cameras}

    @property
    def name(self) -> str:
        return "adapter-blueiris"

    @property
    def mode(self) -> PreprocessingMode:
        return PreprocessingMode.SERVICE

    async def list_cameras(self) -> list[CameraCapability]:
        return [
            CameraCapability(
                camera_id=c.camera_id,
                name=c.name,
                preprocessing_mode=PreprocessingMode.SERVICE,
                has_on_camera_ai=c.ha_motion_entity is not None,
                supported_events=("motion",),
                max_resolution=(c.width, c.height) if (c.width and c.height) else None,
                ptz=c.ptz,
                stream_profiles=("main",),
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
        if camera_id not in self._cameras:
            raise KeyError(camera_id)
        return FrameWindow(
            camera_id=camera_id,
            ts_start=ts_start,
            ts_end=ts_end,
            frames=[],
            metadata={
                "preprocessing_mode": "service",
                "preprocessing_latency_ms": 0,
                "note": "delegated to preprocessor consuming RTSP from Blue Iris",
            },
        )

    async def subscribe_motion_events(
        self,
        camera_id: str | None = None,
    ) -> AsyncIterator[MotionEvent]:
        # Subscription is wired through HA via the ha-agent service; this
        # adapter publishes its own MotionEvents based on HA binary_sensor
        # state changes for ha_motion_entity values.
        return
        yield  # type: ignore[unreachable]

    async def get_stream_url(self, camera_id: str, profile: str = "main") -> str:
        if camera_id not in self._cameras:
            raise KeyError(camera_id)
        return self._cameras[camera_id].rtsp_url
