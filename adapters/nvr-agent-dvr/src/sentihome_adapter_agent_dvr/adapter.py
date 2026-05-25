"""AgentDVRAdapter — service-mode adapter for Agent DVR.

Translates the unified ``NVRAdapter`` contract into Agent DVR REST API calls
(via :class:`AgentDVRClient`) and webhook event ingestion.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import structlog
from sentihome_shared.adapter import NVRAdapter, PreprocessingMode
from sentihome_shared.adapter.base import (
    CameraCapability,
    FrameWindow,
    MotionEvent,
)

from sentihome_adapter_agent_dvr.client import AgentDVRClient, AgentDVRConfig

logger = structlog.get_logger(__name__)


class AgentDVRAdapter(NVRAdapter):
    """Service-mode adapter for Agent DVR."""

    def __init__(self, config: AgentDVRConfig) -> None:
        self._config = config
        self._client: AgentDVRClient | None = None

    @property
    def name(self) -> str:
        return "adapter-agent-dvr"

    @property
    def mode(self) -> PreprocessingMode:
        return PreprocessingMode.SERVICE

    async def start(self) -> None:
        if self._client is None:
            self._client = AgentDVRClient(self._config)
            await self._client.__aenter__()
        logger.info("agent_dvr.start", base_url=self._config.base_url)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.__aexit__()
            self._client = None
        logger.info("agent_dvr.stop")

    async def list_cameras(self) -> list[CameraCapability]:
        client = self._require_client()
        raw = await client.list_cameras()
        return [_to_capability(cam) for cam in raw]

    async def get_frame_window(
        self,
        camera_id: str,
        ts_start: datetime,
        ts_end: datetime,
        *,
        with_metadata: bool = True,
    ) -> FrameWindow:
        # In service mode, frame retrieval ultimately goes through the
        # preprocessor service, which fetches clips from Agent DVR. Direct
        # AgentDVRClient.get_clip() is the underlying mechanism. The
        # preprocessor wraps + caches + enriches.
        return FrameWindow(
            camera_id=camera_id,
            ts_start=ts_start,
            ts_end=ts_end,
            frames=[],
            metadata={
                "preprocessing_mode": "service",
                "preprocessing_latency_ms": 0,
                "note": "frame retrieval delegated to preprocessor service (Epic 4)",
            },
        )

    async def subscribe_motion_events(
        self,
        camera_id: str | None = None,
    ) -> AsyncIterator[MotionEvent]:
        # Agent DVR motion events arrive via webhook (handled by
        # AgentDVRWebhookReceiver, which feeds into this iterator via
        # an internal queue). Wiring lands in §03 ingress integration.
        return
        yield  # type: ignore[unreachable]

    async def slew_ptz(self, camera_id: str, preset_id: str) -> bool:
        client = self._require_client()
        return await client.slew_ptz(camera_id, preset_id)

    def _require_client(self) -> AgentDVRClient:
        if self._client is None:
            raise RuntimeError("AgentDVRAdapter not started — call start() first")
        return self._client


def _to_capability(raw: dict[str, Any]) -> CameraCapability:
    """Convert Agent DVR's camera JSON to a CameraCapability."""
    return CameraCapability(
        camera_id=str(raw.get("id") or raw.get("oid") or raw.get("name", "unknown")),
        name=raw.get("name"),
        preprocessing_mode=PreprocessingMode.SERVICE,
        has_on_camera_ai=bool(raw.get("aiEnabled")),
        supported_events=("motion",),
        ptz=bool(raw.get("ptz")),
        audio=bool(raw.get("audioEnabled")),
        stream_profiles=("main",),
    )
