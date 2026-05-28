"""Service entry point — wires the actor-event subscriber, the
configured frame-buffer backend, and the FastAPI app, then runs
forever.

Backend selection is config-driven:

* ``backend == "synthetic"`` → :class:`SyntheticFrameBuffer`. No
  external dependencies. CI / unit-test default.
* ``backend == "rtsp"``      → :class:`RTSPFrameBuffer` reading from
  :class:`RollingBuffer` filled by per-camera
  :class:`CameraCaptureTask` instances. The container acts as the
  NVR — pulls RTSP sub-streams directly from cameras, JPEG-encodes
  keyframes, holds them in a 5-minute rolling buffer.

Phase 10.3+ replaces the empty enrichment in RTSPFrameBuffer with
real detection (YOLO11x) + recognition (ArcFace, DINOv2, fastALPR)
pipelines.
"""

from __future__ import annotations

import asyncio
import logging
import time

import structlog
import uvicorn

from sentihome_preprocessor.app import AppState, create_app
from sentihome_preprocessor.config import PreprocessorConfig, load_from_env
from sentihome_preprocessor.nats_subscriber import ActorEnrollmentSubscriber
from sentihome_preprocessor.pipelines import FrameBufferBackend
from sentihome_preprocessor.pipelines.rolling_buffer import RollingBuffer
from sentihome_preprocessor.pipelines.rtsp_capture import RTSPCaptureSupervisor
from sentihome_preprocessor.pipelines.rtsp_frame_buffer import RTSPFrameBuffer
from sentihome_preprocessor.pipelines.synthetic_frames import SyntheticFrameBuffer
from sentihome_preprocessor.state import ActorCache

logger = structlog.get_logger(__name__)


def _build_backend(
    config: PreprocessorConfig,
) -> tuple[FrameBufferBackend, RTSPCaptureSupervisor | None]:
    """Returns (frame_buffer, optional capture supervisor).

    The supervisor exists only in ``rtsp`` mode; ``synthetic`` mode
    returns ``None`` for it. Caller is responsible for starting +
    stopping the supervisor.
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
        )

    if config.backend == "rtsp":
        rolling = RollingBuffer(
            horizon_seconds=config.rtsp_buffer_horizon_seconds,
            max_entries_per_camera=config.rtsp_buffer_max_entries_per_camera,
        )
        # Only spin tasks for cameras with a URL configured. Missing
        # URLs surface as a startup log warning so the operator
        # spots the misconfig.
        configured: dict[str, str] = {}
        for cam in config.cameras:
            url = config.camera_rtsp_urls.get(cam, "")
            if not url:
                logger.warning(
                    "preprocessor.rtsp.no_url_configured",
                    camera_id=cam,
                    hint=f"set SENTIHOME_PREPROCESSOR_RTSP_{cam.upper()}=rtsp://...",
                )
                continue
            configured[cam] = url

        supervisor = RTSPCaptureSupervisor(
            camera_urls=configured, buffer=rolling
        )
        frame_buffer = RTSPFrameBuffer(
            rolling_buffer=rolling,
            configured_cameras=config.cameras,
            node_id=config.node_id,
            external_base_url=config.external_base_url,
        )
        return frame_buffer, supervisor

    raise ValueError(
        f"Unknown SENTIHOME_PREPROCESSOR_BACKEND={config.backend!r}; "
        f"expected 'synthetic' or 'rtsp'"
    )


async def _run(config: PreprocessorConfig) -> None:
    cache = ActorCache()
    subscriber = ActorEnrollmentSubscriber(config.nats_url, cache)

    # Connect the inbound NATS subscription first.
    await subscriber.connect()

    frame_buffer, capture_supervisor = _build_backend(config)

    if capture_supervisor is not None:
        await capture_supervisor.start()

    state = AppState(
        config=config,
        cache=cache,
        frame_buffer=frame_buffer,
        started_ts=time.time(),
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
        if capture_supervisor is not None:
            await capture_supervisor.stop()
        await subscriber.close()
        logger.info("preprocessor.stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_from_env()
    asyncio.run(_run(config))


if __name__ == "__main__":
    main()
