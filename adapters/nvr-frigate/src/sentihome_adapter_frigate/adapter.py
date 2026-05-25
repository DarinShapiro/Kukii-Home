"""FrigateAdapter — built-in mode adapter for Frigate NVR.

Frigate provides motion detection + YOLO out of the box. This adapter:
- Subscribes to Frigate's MQTT topics for events
- Uses Frigate's REST API for clip + snapshot retrieval
- Maps Frigate detections to SentiHome's enrichment schema
- Skips SentiHome's own preprocessing (Frigate already did it)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sentihome_shared.adapter import NVRAdapter, PreprocessingMode
from sentihome_shared.adapter.base import (
    CameraCapability,
    FramePointer,
    FrameWindow,
    MotionEvent,
)

logger = structlog.get_logger(__name__)


@dataclass
class FrigateConfig:
    """Frigate connection settings."""

    rest_url: str = "http://localhost:5000"
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str = "frigate"
    timeout_seconds: float = 10.0


class FrigateAdapter(NVRAdapter):
    """Built-in mode adapter for Frigate."""

    def __init__(self, config: FrigateConfig) -> None:
        self._config = config
        self._http: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "adapter-frigate"

    @property
    def mode(self) -> PreprocessingMode:
        return PreprocessingMode.BUILT_IN

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._config.rest_url,
                timeout=self._config.timeout_seconds,
            )

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def list_cameras(self) -> list[CameraCapability]:
        http = self._require_http()
        try:
            r = await http.get("/api/config")
            r.raise_for_status()
            config = r.json()
        except httpx.HTTPError as e:
            logger.error("frigate.list_cameras_failed", error=str(e))
            return []

        cameras: list[CameraCapability] = []
        for name, _spec in (config.get("cameras") or {}).items():
            cameras.append(
                CameraCapability(
                    camera_id=name,
                    name=name,
                    preprocessing_mode=PreprocessingMode.BUILT_IN,
                    has_on_camera_ai=True,  # Frigate IS the on-camera AI
                    supported_events=("motion", "person", "vehicle", "animal"),
                    stream_profiles=("main",),
                )
            )
        return cameras

    async def get_frame_window(
        self,
        camera_id: str,
        ts_start: datetime,
        ts_end: datetime,
        *,
        with_metadata: bool = True,
    ) -> FrameWindow:
        http = self._require_http()
        start_epoch = int(ts_start.timestamp())
        end_epoch = int(ts_end.timestamp())

        # Fetch snapshots from Frigate's events endpoint for this window.
        try:
            r = await http.get(
                "/api/events",
                params={
                    "camera": camera_id,
                    "after": start_epoch,
                    "before": end_epoch,
                    "limit": 10,
                },
            )
            r.raise_for_status()
            events = r.json() if isinstance(r.json(), list) else []
        except httpx.HTTPError as e:
            logger.error("frigate.frame_window_failed", camera=camera_id, error=str(e))
            events = []

        frames: list[FramePointer] = []
        detections: list[dict[str, Any]] = []
        for event in events:
            event_id = event.get("id")
            if event_id:
                snapshot_uri = f"{self._config.rest_url}/api/events/{event_id}/snapshot.jpg"
                frames.append(
                    FramePointer(
                        uri=snapshot_uri,
                        timestamp=datetime.fromtimestamp(
                            event.get("start_time", start_epoch), tz=UTC
                        ),
                    )
                )
            detections.append(
                {
                    "class": event.get("label"),
                    "confidence": event.get("top_score"),
                    "bbox": event.get("box"),
                }
            )

        return FrameWindow(
            camera_id=camera_id,
            ts_start=ts_start,
            ts_end=ts_end,
            frames=frames,
            metadata={
                "detections": detections,
                "preprocessing_mode": "built-in",
                "preprocessing_latency_ms": 0,
                "source": "frigate",
            },
        )

    async def subscribe_motion_events(
        self,
        camera_id: str | None = None,
    ) -> AsyncIterator[MotionEvent]:
        """Subscribe to Frigate's MQTT events.

        Frigate publishes to topics like ``frigate/events`` and
        ``frigate/<camera>/<label>``. We subscribe to the catch-all events
        topic and filter by camera_id if requested.

        Note: Full MQTT subscription via paho-mqtt or aiomqtt is wired up
        when the adapter is started inside a service runtime. For unit tests
        we expose ``_handle_mqtt_payload`` which can be invoked directly.
        """
        # Async generator placeholder; real MQTT loop hooks into this queue.
        # The actual MQTT loop is started by the service runtime, not the
        # adapter constructor (allows unit testing without a broker).
        import asyncio

        self._motion_queue: asyncio.Queue[MotionEvent] = getattr(
            self, "_motion_queue", asyncio.Queue()
        )
        while True:
            event = await self._motion_queue.get()
            if camera_id is None or event.camera_id == camera_id:
                yield event

    def _handle_mqtt_payload(self, payload: bytes) -> MotionEvent | None:
        """Convert a Frigate MQTT events payload to a MotionEvent.

        Public for testing; called internally by the MQTT loop.
        """
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if data.get("type") != "new":
            return None  # ignore "update" / "end" notifications for now
        after = data.get("after") or {}
        return MotionEvent(
            camera_id=after.get("camera", "unknown"),
            timestamp=datetime.fromtimestamp(after.get("start_time", 0), tz=UTC),
            event_type=after.get("label", "motion"),
            confidence=after.get("top_score"),
            bbox=tuple(after["box"]) if "box" in after else None,
            raw=data,
        )

    def _require_http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("FrigateAdapter not started — call start() first")
        return self._http
