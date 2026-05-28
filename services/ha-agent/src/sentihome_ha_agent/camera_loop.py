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
    from sentihome_ha_agent.client import HAClient, HAState
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
        from sentihome_shared.motion import MOG2MotionDetector

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


# ─────────────────────────────────────────────────────────────────────
# HACameraLoop — ride on HA's camera integration
# ─────────────────────────────────────────────────────────────────────


class HACameraLoop:
    """One HA camera entity → motion sensor subscribed → snapshot on event.

    No RTSP, no MOG2, no per-frame CPU. We let HA's existing camera
    integration handle the stream, then subscribe to the motion / AI
    binary sensors that camera exposes. On off→on transition:

      1. Call ``camera.snapshot`` HA service to capture the frame
      2. Record an alert in :class:`AlertLog` with snapshot URL +
         the sensor that fired (so the alert headline can say
         "Person detected at pool cam" — using HA's onboard AI's
         classification, not just generic "motion")
      3. Debounce per the configured ``snapshot_cooldown_seconds``

    Designed to be the preferred adapter for any camera HA already
    manages. :class:`CameraLoop` (RTSP-direct) remains for cameras HA
    doesn't see.
    """

    def __init__(
        self,
        *,
        camera_id: str,
        camera_entity: str,
        motion_entities: list[str],
        camera_name: str | None,
        client: HAClient,
        alert_log: AlertLog,
        registry: CameraLoopRegistry,
        cooldown_seconds: float = 30.0,
        snapshot_dir: str = "/data/sentihome/snapshots",
    ) -> None:
        self._camera_id = camera_id
        self._camera_entity = camera_entity
        self._motion_entities = set(motion_entities)
        self._camera_name = camera_name or camera_entity
        self._client = client
        self._alert_log = alert_log
        self._cooldown_seconds = cooldown_seconds
        self._snapshot_dir = snapshot_dir
        self._status = CameraStreamStatus(camera_id=camera_id, rtsp_url=camera_entity)
        self._status.state = "subscribed"
        registry.register(self._status)
        self._last_snapshot_at = 0.0
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Subscribe to motion-sensor state changes for the lifetime of the
        add-on. Self-heals via :class:`HAClient`'s own reconnect logic."""
        from pathlib import Path

        # mkdir is sync but extremely fast; not worth pulling in anyio.path
        Path(self._snapshot_dir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        self._client.on_state_change(self._on_state_change)
        logger.info(
            "ha_camera_loop.subscribed",
            camera=self._camera_entity,
            motion_entities=sorted(self._motion_entities),
        )
        # Hold the task alive — HAClient's WebSocket loop does the work.
        await self._stop_event.wait()
        self._status.state = "stopped"

    async def stop(self) -> None:
        self._stop_event.set()

    async def _on_state_change(self, new: HAState, old: HAState | None) -> None:
        if new.entity_id not in self._motion_entities:
            return
        # Only care about off → on (or unknown → on) transitions.
        if new.state != "on":
            return
        if old is not None and old.state == "on":
            return
        # v0.3.19: capture wall-clock as soon as the handler runs so
        # we can measure the HA → SentiHome lag downstream.
        received_at = datetime.now(UTC)
        # Debounce.
        now = time.monotonic()
        if now - self._last_snapshot_at < self._cooldown_seconds:
            logger.debug(
                "ha_camera_loop.debounced",
                camera=self._camera_entity,
                sensor=new.entity_id,
            )
            return
        self._last_snapshot_at = now
        await self._capture_and_alert(
            triggering_sensor=new.entity_id,
            sensor_state=new,
            received_at=received_at,
        )

    async def _capture_and_alert(
        self,
        *,
        triggering_sensor: str,
        sensor_state: HAState,
        received_at: datetime,
    ) -> None:
        from pathlib import Path

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        snapshot_filename = f"{self._camera_id}_{ts}_{uuid.uuid4().hex[:6]}.jpg"
        snapshot_path: str | None = f"{self._snapshot_dir}/{snapshot_filename}"

        # Fetch the current frame from HA via /api/camera_proxy and write
        # it to OUR filesystem. Don't use the camera.snapshot service —
        # that runs HA-Core-side, writes to HA Core's filesystem, and
        # SentiHome's /data is a different mountpoint from HA Core's.
        # v0.3.19: wrap the fetch in timing so we can report
        # snapshot-fetch latency on each alert.
        snapshot_started_at = datetime.now(UTC)
        snapshot_bytes: bytes | None = None
        try:
            snapshot_bytes = await self._client.fetch_camera_snapshot(self._camera_entity)
            self._status.last_error = ""
        except Exception as e:
            err_msg = str(e)
            logger.warning(
                "ha_camera_loop.snapshot_fetch_failed",
                camera=self._camera_entity,
                error=err_msg,
            )
            # Surface the error on the Cameras card so the user sees the
            # diagnosis directly without checking logs.
            self._status.last_error = f"snapshot fetch failed: {err_msg[:300]}"
        snapshot_completed_at = datetime.now(UTC)

        if snapshot_bytes is not None:
            try:
                Path(self._snapshot_dir).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
                Path(snapshot_path).write_bytes(snapshot_bytes)  # noqa: ASYNC240
            except Exception as e:
                logger.warning(
                    "ha_camera_loop.snapshot_write_failed",
                    path=snapshot_path,
                    error=str(e),
                )
                snapshot_path = None
        else:
            snapshot_path = None

        self._status.motion_events += 1
        self._status.last_motion_at = datetime.now(UTC)
        self._status.frames_read += 1
        self._status.last_frame_at = self._status.last_motion_at

        sensor_kind = _kind_from_sensor(triggering_sensor)
        headline = (
            f"{sensor_kind.capitalize()} at {self._camera_name}"
            if sensor_kind
            else f"Motion at {self._camera_name}"
        )

        # v0.3.19: per-alert timing waypoints. We can measure
        # everything from HA's last_changed onward; the camera ↔ HA
        # integration delay (T0 → last_changed) isn't visible to us
        # without camera-side instrumentation.
        timings = _compute_timings(
            ha_last_changed=sensor_state.last_changed,
            received_at=received_at,
            snapshot_started_at=snapshot_started_at,
            snapshot_completed_at=snapshot_completed_at,
        )

        self._alert_log.record(
            {
                "alert_id": f"ha_motion_{self._camera_id}_{uuid.uuid4().hex[:8]}",
                "headline": headline,
                "tier": "tier_1_in_app",
                "confidence": 0.85,  # HA AI is reasonably reliable
                "rules_fired": [],
                "evidence_ref": snapshot_path,
                "camera_id": self._camera_id,
                "camera_entity": self._camera_entity,
                # v0.3.15: stamp the friendly name on the alert so the
                # notifier renders readable messages ("Person at Front
                # South Camera") without having to re-derive the name
                # from the camera_id slug.
                "camera_name": self._camera_name,
                "triggering_sensor": triggering_sensor,
                "sensor_classification": sensor_kind,
                "ha_sensor_attributes": sensor_state.attributes,
                "ha_last_changed": sensor_state.last_changed,
                "ha_last_updated": sensor_state.last_updated,
                "timings": timings,
                "source": "ha_camera_event",
            }
        )
        logger.info(
            "ha_camera_loop.motion_recorded",
            camera=self._camera_entity,
            sensor=triggering_sensor,
            kind=sensor_kind,
            snapshot=snapshot_path,
        )


def _compute_timings(
    *,
    ha_last_changed: str | None,
    received_at: datetime,
    snapshot_started_at: datetime,
    snapshot_completed_at: datetime,
) -> dict[str, float | None]:
    """Compute per-alert latency waypoints in milliseconds.

    We can measure everything from HA's view of the state change
    onward; the **camera → HA integration** delay (real-world motion
    → HA seeing the binary_sensor flip) is invisible to us without
    camera-side instrumentation. The fields:

    - ``ha_to_received_ms`` — HA Core saw the sensor change to our
      WebSocket handler woke up. Should be a few ms on LAN; spikes
      suggest WebSocket lag / HA Core overload.
    - ``handler_to_snapshot_start_ms`` — time we spent in our handler
      before issuing the snapshot fetch. Should be sub-ms.
    - ``snapshot_duration_ms`` — HTTP fetch through ``camera_proxy``.
      This is "how long the camera + HA integration took to give us a
      frame." Heavy snapshots, slow cameras, and integration overhead
      all land here.
    - ``ha_to_snapshot_complete_ms`` — total "time to have a frame in
      hand" from HA's view of motion. Most useful single number for
      judging "is the snapshot likely to still reflect the alert?"

    All values are floats in milliseconds. Any that can't be computed
    (e.g. HA didn't send ``last_changed``) are ``None``.
    """
    out: dict[str, float | None] = {
        "ha_to_received_ms": None,
        "handler_to_snapshot_start_ms": (snapshot_started_at - received_at).total_seconds()
        * 1000.0,
        "snapshot_duration_ms": (snapshot_completed_at - snapshot_started_at).total_seconds()
        * 1000.0,
        "ha_to_snapshot_complete_ms": None,
    }
    if ha_last_changed:
        try:
            ha_dt = datetime.fromisoformat(ha_last_changed)
            out["ha_to_received_ms"] = (received_at - ha_dt).total_seconds() * 1000.0
            out["ha_to_snapshot_complete_ms"] = (
                snapshot_completed_at - ha_dt
            ).total_seconds() * 1000.0
        except (ValueError, TypeError):
            pass
    return out


def _kind_from_sensor(entity_id: str) -> str | None:
    """Heuristically extract the AI classification from a motion sensor's id.

    ``binary_sensor.pool_cam_person`` → ``person``
    ``binary_sensor.pool_cam_motion`` → ``motion``
    ``binary_sensor.front_yard_vehicle`` → ``vehicle``
    """
    eid = entity_id.lower()
    for kw in ("person", "vehicle", "animal", "package", "pet"):
        if kw in eid:
            return kw
    if "motion" in eid:
        return "motion"
    return None


def build_ha_camera_loops_from_topology(
    topology, *, client: HAClient, alert_log: AlertLog, registry: CameraLoopRegistry
) -> list[HACameraLoop]:
    """Scan topology.adapters for `ha-camera` entries → :class:`HACameraLoop`s."""
    loops: list[HACameraLoop] = []
    for adapter in getattr(topology, "adapters", []) or []:
        if adapter.kind != "ha-camera":
            continue
        if not adapter.camera_entity:
            logger.warning("ha_camera_loop.skipping_no_camera_entity", adapter=adapter.name)
            continue
        if not adapter.motion_entities:
            logger.warning(
                "ha_camera_loop.skipping_no_motion_entities",
                adapter=adapter.name,
                camera=adapter.camera_entity,
            )
            continue
        loops.append(
            HACameraLoop(
                camera_id=adapter.name,
                camera_entity=adapter.camera_entity,
                motion_entities=adapter.motion_entities,
                camera_name=adapter.name,
                client=client,
                alert_log=alert_log,
                registry=registry,
                cooldown_seconds=adapter.snapshot_cooldown_seconds,
            )
        )
    return loops
