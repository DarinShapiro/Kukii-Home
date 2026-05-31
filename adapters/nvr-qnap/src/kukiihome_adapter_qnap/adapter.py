"""QnapAdapter — service-mode adapter for QNAP QVR Pro.

Uses the QVR Pro OpenAPI. Full client implementation deferred to v1.x.

API reference: https://www.qnap.com/solution/qvr-developer/en-us/
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from kukiihome_shared.adapter import NVRAdapter, PreprocessingMode
from kukiihome_shared.adapter.base import (
    AdapterError,
    CameraCapability,
    FrameWindow,
    MotionEvent,
)


@dataclass
class QnapConfig:
    base_url: str = "http://qnap.local:8080"
    username: str = ""
    password: str = ""


class QnapAdapter(NVRAdapter):
    def __init__(self, config: QnapConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "adapter-qnap"

    @property
    def mode(self) -> PreprocessingMode:
        return PreprocessingMode.SERVICE

    async def list_cameras(self) -> list[CameraCapability]:
        raise AdapterError(
            "QnapAdapter is a v1.x skeleton — full QVR Pro client deferred. "
            "See planning/epics/03-nvr-adapters.md (issue #41)."
        )

    async def get_frame_window(
        self,
        camera_id: str,
        ts_start: datetime,
        ts_end: datetime,
        *,
        with_metadata: bool = True,
    ) -> FrameWindow:
        raise AdapterError("QNAP frame retrieval not yet implemented")

    async def subscribe_motion_events(
        self,
        camera_id: str | None = None,
    ) -> AsyncIterator[MotionEvent]:
        return
        yield  # type: ignore[unreachable]
