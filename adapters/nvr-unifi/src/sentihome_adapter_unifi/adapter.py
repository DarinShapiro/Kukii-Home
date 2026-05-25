"""UnifiAdapter — service-mode adapter for UniFi Protect.

Uses the official UniFi Protect API (released by Ubiquiti Feb 2026). Full
client implementation deferred to v1.x.

Integration reference: https://www.home-assistant.io/integrations/unifiprotect/
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
class UnifiConfig:
    base_url: str = "https://unifi.local"
    api_key: str = ""
    verify_ssl: bool = True


class UnifiAdapter(NVRAdapter):
    def __init__(self, config: UnifiConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "adapter-unifi"

    @property
    def mode(self) -> PreprocessingMode:
        return PreprocessingMode.SERVICE

    async def list_cameras(self) -> list[CameraCapability]:
        raise AdapterError(
            "UnifiAdapter is a v1.x skeleton — full Protect API client deferred. "
            "See planning/epics/03-nvr-adapters.md (issue #42)."
        )

    async def get_frame_window(
        self,
        camera_id: str,
        ts_start: datetime,
        ts_end: datetime,
        *,
        with_metadata: bool = True,
    ) -> FrameWindow:
        raise AdapterError("UniFi Protect frame retrieval not yet implemented")

    async def subscribe_motion_events(
        self,
        camera_id: str | None = None,
    ) -> AsyncIterator[MotionEvent]:
        return
        yield  # type: ignore[unreachable]
