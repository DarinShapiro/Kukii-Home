"""Service entry point — wires actor-event + camera-config NATS
subscribers, the configured frame-buffer backend, and the FastAPI app,
then runs forever.

Backend selection is config-driven:

* ``backend == "synthetic"`` → :class:`SyntheticFrameBuffer`. No
  external dependencies. CI / unit-test default. Camera-config
  events are still subscribed (uniform shape) but no-op'd via
  :class:`NoOpApplier`.
* ``backend == "rtsp"``      → :class:`RTSPFrameBuffer` reading from
  :class:`RollingBuffer` filled by per-camera
  :class:`CameraCaptureTask` instances. The container acts as the
  NVR — pulls RTSP / HLS streams directly, JPEG-encodes keyframes,
  holds them in a 5-minute rolling buffer. Camera-config events
  dynamically add/remove capture tasks via
  :class:`SupervisorApplier`.

Phase 10.3+ replaces the empty enrichment in RTSPFrameBuffer with
real detection (YOLO11x) + recognition (ArcFace, DINOv2, fastALPR)
pipelines.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import structlog
import uvicorn

from kukiihome_preprocessor.app import AppState, create_app
from kukiihome_preprocessor.camera_config_subscriber import (
    CameraConfigApplier,
    CameraConfigSubscriber,
    NoOpApplier,
    SupervisorApplier,
)
from kukiihome_preprocessor.config import PreprocessorConfig, load_from_env
from kukiihome_preprocessor.nats_subscriber import ActorEnrollmentSubscriber
from kukiihome_preprocessor.pipelines import FrameBufferBackend
from kukiihome_preprocessor.pipelines.rolling_buffer import (
    AnnotationCache,
    RollingBuffer,
)
from kukiihome_preprocessor.pipelines.rtsp_capture import RTSPCaptureSupervisor
from kukiihome_preprocessor.pipelines.rtsp_frame_buffer import RTSPFrameBuffer
from kukiihome_preprocessor.pipelines.synthetic_frames import SyntheticFrameBuffer
from kukiihome_preprocessor.state import ActorCache

logger = structlog.get_logger(__name__)


def _build_backend(
    config: PreprocessorConfig,
) -> tuple[
    FrameBufferBackend,
    RTSPCaptureSupervisor | None,
    CameraConfigApplier,
]:
    """Returns (frame_buffer, optional capture supervisor, camera-config applier).

    Synthetic mode: supervisor=None, applier=NoOpApplier.
    RTSP mode: supervisor + SupervisorApplier wired to it.

    Bootstrap env-var URLs (``KUKIIHOME_PREPROCESSOR_RTSP_<CAMERA>``)
    are preserved as a fallback for operators who want to bring up
    cameras without ha-agent — the supervisor is pre-populated, then
    camera-config events from ha-agent can replace/add to that set.
    """
    if config.backend == "synthetic":
        return (
            SyntheticFrameBuffer(
                configured_cameras=config.cameras,
                node_id=config.node_id,
                frames_per_second=config.synthetic_frames_per_second,
                buffer_horizon_seconds=config.synthetic_buffer_horizon_seconds,
            ),
            None,
            NoOpApplier(),
        )

    if config.backend == "rtsp":
        rolling = RollingBuffer(
            horizon_seconds=config.rtsp_buffer_horizon_seconds,
            max_entries_per_camera=config.rtsp_buffer_max_entries_per_camera,
        )
        annotation_cache = AnnotationCache(
            horizon_seconds=config.rtsp_buffer_horizon_seconds,
        )
        # Motion gate tuned for the deployment: downscale + higher variance
        # threshold so sunlit water shimmer / sparkle doesn't manufacture
        # phantom motion (the pool false-trigger).
        from kukiihome_preprocessor.motion import MotionConfig

        motion_config = MotionConfig(
            var_threshold=config.motion_var_threshold,
            min_object_size_px=config.motion_min_object_size_px,
        )
        supervisor = RTSPCaptureSupervisor(
            buffer=rolling, motion_config=motion_config, motion_source=config.motion_source
        )

        # Build the detector + identity DAG via the shared factory (same
        # construction the offline enrichment worker uses — no drift).
        from kukiihome_preprocessor.pipelines.builders import (
            build_detector,
            build_identity_router,
        )

        identity_router = build_identity_router(config)
        detector = build_detector(config)

        frame_buffer = RTSPFrameBuffer(
            rolling_buffer=rolling,
            configured_cameras=config.cameras,
            node_id=config.node_id,
            external_base_url=config.external_base_url,
            detector=detector,
            identity_router=identity_router,
            annotation_cache=annotation_cache,
        )
        return frame_buffer, supervisor, SupervisorApplier(supervisor)

    raise ValueError(
        f"Unknown KUKIIHOME_PREPROCESSOR_BACKEND={config.backend!r}; expected 'synthetic' or 'rtsp'"
    )


async def _bootstrap_rtsp_from_env(
    config: PreprocessorConfig, supervisor: RTSPCaptureSupervisor
) -> None:
    """Pre-populate the supervisor with any env-var-supplied URLs.

    Camera-config events from ha-agent can subsequently replace
    these (e.g. ha-agent publishes a fresher HLS URL). The env-var
    path exists for operators running without ha-agent.
    """
    for cam in config.cameras:
        url = config.camera_rtsp_urls.get(cam, "")
        if not url:
            continue
        await supervisor.add(camera_id=cam, rtsp_url=url)


async def _run(config: PreprocessorConfig) -> None:
    cache = ActorCache()
    actor_subscriber = ActorEnrollmentSubscriber(config.nats_url, cache)
    await actor_subscriber.connect()

    frame_buffer, capture_supervisor, camera_applier = _build_backend(config)

    camera_subscriber = CameraConfigSubscriber(config.nats_url, camera_applier)
    await camera_subscriber.connect()

    if capture_supervisor is not None:
        await capture_supervisor.start()
        await _bootstrap_rtsp_from_env(config, capture_supervisor)

    # Autonomous motion-event recorder (durable sink). Only for the rtsp
    # backend (synthetic has no rolling buffer / motion), and only when
    # enabled. Runs as a background task; gathered/cancelled on shutdown.
    event_recorder_task: asyncio.Task | None = None
    event_recorder_stop = asyncio.Event()
    if (
        config.events_enabled
        and capture_supervisor is not None
        and hasattr(frame_buffer, "rolling_buffer")
    ):
        from pathlib import Path

        from kukiihome_preprocessor.pipelines.event_recorder import (
            EventRecorder,
            EventRecorderConfig,
        )

        recorder = EventRecorder(
            rolling_buffer=frame_buffer.rolling_buffer,
            frame_buffer=frame_buffer,
            cache=cache,
            cameras=config.cameras,
            node_id=config.node_id,
            config=EventRecorderConfig(
                pre_roll_s=config.event_pre_roll_s,
                post_roll_s=config.event_post_roll_s,
                max_duration_s=config.event_max_duration_s,
                poll_interval_s=config.event_poll_interval_s,
                store_dir=Path(config.event_store_dir),
                enrich=config.event_enrich,
            ),
        )
        event_recorder_task = asyncio.create_task(recorder.run(stop=event_recorder_stop))

    state = AppState(
        config=config,
        cache=cache,
        frame_buffer=frame_buffer,
        started_ts=time.time(),
        capture_supervisor=capture_supervisor,
    )

    app = create_app(state)

    uvicorn_config = uvicorn.Config(
        app,
        host=config.http_host,
        port=config.http_port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(uvicorn_config)

    logger.info(
        "preprocessor.start",
        node_id=config.node_id,
        http=f"{config.http_host}:{config.http_port}",
        external_url=config.external_base_url,
        nats=config.nats_url,
        backend=config.backend,
        cameras=config.cameras,
    )

    try:
        await server.serve()
    finally:
        if event_recorder_task is not None:
            event_recorder_stop.set()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(event_recorder_task, timeout=5.0)
        if capture_supervisor is not None:
            await capture_supervisor.stop()
        await camera_subscriber.close()
        await actor_subscriber.close()
        logger.info("preprocessor.stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_from_env()
    asyncio.run(_run(config))


if __name__ == "__main__":
    main()
