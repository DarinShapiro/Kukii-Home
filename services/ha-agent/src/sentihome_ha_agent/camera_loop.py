"""Camera loops — read RTSP, run motion detection, log alerts.

This is the first end-to-end runtime in the add-on: an RTSP frame source
piped through the preprocessor's existing :class:`MOG2MotionDetector`,
producing :class:`MotionEvent`s that land in :class:`AlertLog`. The Web
UI status page renders them in the "Recent alerts" card.

No NATS, no separate services — everything in-process inside the
ha-agent container. Multiple RTSP cameras configured in the topology
each get their own :class:`CameraLoop` running as an asyncio task.

When the bus + preprocessor + core services wire up under s6 in Epic 10+,
this in-process loop retires; the path becomes
  rtsp-adapter → preprocessor (publishes trigger.*) → triage (NATS) → ...
For now it's the proof-of-loop demo: "camera in → alert out."
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from sentihome_ha_agent.http_api import AlertLog

logger = structlog.get_logger(__name__)


# Sample one frame per N read. RTSP usually delivers at the stream's
# native fps (15-30); we don't need MOG2 on every frame.
DEFAULT_SAMPLE_EVERY_N_FRAMES = 5

# Cooldown after a motion event before we'll log another from the same
# camera. Prevents the alert log from filling with one continuous motion
# train.
DEFAULT_MOTION_COOLDOWN_SECONDS = 30.0

# How long to wait between failed open attempts. Most RTSP failures are
# transient (camera reboot, transient network blip).
DEFAULT_RECONNECT_DELAY_SECONDS = 15.0


@dataclass
class CameraStreamStatus:
    """What the status page renders per camera."""

    camera_id: str
    rtsp_url: str
    state: str = "starting"
    """One of: starting, opening, running, error, stopped."""
    last_error: str = ""
    last_frame_at: datetime | None = None
    frames_read: int = 0
    motion_events: int = 0
    last_motion_at: datetime | None = None


@dataclass
class CameraLoopRegistry:
    """Holds the live status of every running camera loop."""

    by_camera_id: dict[str, CameraStreamStatus] = field(default_factory=dict)

    def register(self, status: CameraStreamStatus) -> None:
        self.by_camera_id[status.camera_id] = status

    def all(self) -> list[CameraStreamStatus]:
        return list(self.by_camera_id.values())


class CameraLoop:
    """One RTSP stream → motion detection → AlertLog pipeline."""

    def __init__(
        self,
        *,
        camera_id: str,
        rtsp_url: str,
        camera_name: str | None,
        alert_log: AlertLog,
        registry: CameraLoopRegistry,
        sample_every_n: int = DEFAULT_SAMPLE_EVERY_N_FRAMES,
        cooldown_seconds: float = DEFAULT_MOTION_COOLDOWN_SECONDS,
    ) -> None:
        self._camera_id = camera_id
        self._rtsp_url = rtsp_url
        self._camera_name = camera_name or camera_id
        self._alert_log = alert_log
        self._sample_every_n = sample_every_n
        self._cooldown_seconds = cooldown_seconds
        self._status = CameraStreamStatus(camera_id=camera_id, rtsp_url=rtsp_url)
        registry.register(self._status)
        self._stop_event = asyncio.Event()
        self._last_motion_at = 0.0
        self._detector = None

    async def run(self) -> None:
        """Top-level loop: open stream, sample frames, detect motion. Self-heals."""
        from sentihome_preprocessor.motion import MOG2MotionDetector

        self._detector = MOG2MotionDetector()
        while not self._stop_event.is_set():
            self._status.state = "opening"
            try:
                await self._run_one_stream_session()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._status.state = "error"
                self._status.last_error = str(e)
                logger.warning(
                    "camera_loop.error",
                    camera_id=self._camera_id,
                    error=str(e),
                )
            if self._stop_event.is_set():
                break
            await asyncio.sleep(DEFAULT_RECONNECT_DELAY_SECONDS)

    async def stop(self) -> None:
        self._stop_event.set()
        self._status.state = "stopped"

    async def _run_one_stream_session(self) -> None:
        """Open the RTSP stream once; loop until it closes or errors."""
        # cv2.VideoCapture is sync — run open + reads off the event loop
        # to keep the aiohttp server responsive.
        import cv2  # local import: opencv is heavy

        loop = asyncio.get_running_loop()
        cap = await loop.run_in_executor(None, cv2.VideoCapture, self._rtsp_url)
        try:
            if not cap.isOpened():
                self._status.state = "error"
                self._status.last_error = "VideoCapture failed to open the RTSP URL"
                logger.warning("camera_loop.open_failed", camera_id=self._camera_id)
                return
            self._status.state = "running"
            self._status.last_error = ""
            logger.info("camera_loop.open_ok", camera_id=self._camera_id, url=self._rtsp_url)

            frame_index = 0
            while not self._stop_event.is_set():
                ok, frame = await loop.run_in_executor(None, cap.read)
                if not ok or frame is None:
                    self._status.state = "error"
                    self._status.last_error = "read returned no frame; stream closed"
                    return
                self._status.frames_read += 1
                self._status.last_frame_at = datetime.now(UTC)

                frame_index += 1
                if frame_index % self._sample_every_n != 0:
                    # Yield control so aiohttp can serve requests.
                    await asyncio.sleep(0)
                    continue

                decision = self._detector.process(frame, timestamp=time.monotonic())
                if decision.has_motion:
                    self._maybe_record_motion(decision)
                await asyncio.sleep(0)
        finally:
            await loop.run_in_executor(None, cap.release)

    def _maybe_record_motion(self, decision) -> None:
        now = time.monotonic()
        if now - self._last_motion_at < self._cooldown_seconds:
            return
        self._last_motion_at = now
        self._status.motion_events += 1
        self._status.last_motion_at = datetime.now(UTC)
        self._alert_log.record(
            {
                "alert_id": f"motion_{self._camera_id}_{uuid.uuid4().hex[:8]}",
                "headline": f"Motion at {self._camera_name}",
                "tier": "tier_1_in_app",
                "confidence": float(decision.confidence),
                "rules_fired": [],
                "evidence_ref": None,
                "camera_id": self._camera_id,
                "source": "in_process_motion",
            }
        )
        logger.info(
            "camera_loop.motion_recorded",
            camera_id=self._camera_id,
            confidence=decision.confidence,
            regions=len(decision.regions),
        )


def build_camera_loops_from_topology(
    topology, *, alert_log: AlertLog, registry: CameraLoopRegistry
) -> list[CameraLoop]:
    """Scan topology.adapters for `rtsp-direct` entries → :class:`CameraLoop`s.

    Each adapter entry may define multiple streams; one loop per stream.
    Returns an empty list if no rtsp-direct adapters are configured —
    that's fine, the rest of the add-on still runs.
    """
    loops: list[CameraLoop] = []
    for adapter in getattr(topology, "adapters", []) or []:
        if adapter.kind != "rtsp-direct":
            continue
        for stream in adapter.streams:
            camera_id = stream.get("id")
            rtsp_url = stream.get("rtsp_url")
            if not camera_id or not rtsp_url:
                logger.warning(
                    "camera_loop.skipping_invalid_stream",
                    adapter=adapter.name,
                    stream=stream,
                )
                continue
            loops.append(
                CameraLoop(
                    camera_id=camera_id,
                    rtsp_url=rtsp_url,
                    camera_name=stream.get("name"),
                    alert_log=alert_log,
                    registry=registry,
                )
            )
    return loops
