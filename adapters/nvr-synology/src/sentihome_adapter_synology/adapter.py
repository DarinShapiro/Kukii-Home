"""SynologyAdapter — service-mode adapter for Synology Surveillance Station.

Uses the Surveillance Station Web API v3.11. Full client implementation
deferred to v1.x; this skeleton conforms to the NVRAdapter contract so the
auto-detection bootstrap can register the adapter and surface a clean
"not yet implemented" message to users.

API reference:
  https://global.download.synology.com/download/Document/Software/DeveloperGuide/Package/SurveillanceStation/All/enu/Surveillance_Station_Web_API.pdf
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from sentihome_shared.adapter import NVRAdapter, PreprocessingMode
from sentihome_shared.adapter.base import (
    AdapterError,
    CameraCapability,
    FrameWindow,
    MotionEvent,
)


@dataclass
class SynologyConfig:
    """Synology DSM + Surveillance Station credentials."""

    base_url: str = "http://synology.local:5000"
    username: str = ""
    password: str = ""
    session_id: str | None = None


class SynologyAdapter(NVRAdapter):
    def __init__(self, config: SynologyConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "adapter-synology"

    @property
    def mode(self) -> PreprocessingMode:
        return PreprocessingMode.SERVICE

    async def list_cameras(self) -> list[CameraCapability]:
        # Real implementation calls SYNO.SurveillanceStation.Camera List API.
        raise AdapterError(
            "SynologyAdapter is a v1.x skeleton — full client deferred. "
            "See planning/epics/03-nvr-adapters.md (issue #40)."
        )

    async def get_frame_window(
        self,
        camera_id: str,
        ts_start: datetime,
        ts_end: datetime,
        *,
        with_metadata: bool = True,
    ) -> FrameWindow:
        raise AdapterError("Synology frame retrieval not yet implemented")

    async def subscribe_motion_events(
        self,
        camera_id: str | None = None,
    ) -> AsyncIterator[MotionEvent]:
        return
        yield  # type: ignore[unreachable]
