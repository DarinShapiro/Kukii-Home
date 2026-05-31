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
        supervisor = RTSPCaptureSupervisor(buffer=rolling)

        # Build identity pipelines + router. Face is the only one
        # today; body-ID / pet / plate slot in by registering more
        # IdentityPipeline implementations here.
        identity_pipelines = []
        if config.face_recognition_enabled:
            # Lazy import: insightface + onnxruntime are heavyweight.
            # Pay the cost only when face recognition is on.
            from kukiihome_preprocessor.pipelines.face import (
                FaceConfig,
                FaceRecognizer,
            )
            from kukiihome_preprocessor.pipelines.identity import FacePipeline

            face_recognizer = FaceRecognizer(
                FaceConfig(
                    model_pack=config.face_model_pack,
                    match_threshold=config.face_match_threshold,
                    det_confidence_min=config.face_det_confidence_min,
                    det_size=config.face_det_size,
                    providers=tuple(config.face_providers),
                )
            )
            identity_pipelines.append(FacePipeline(face_recognizer))

        if config.body_id_enabled:
            # Lazy import: onnxruntime is heavy, body_id only loaded
            # when opted in.
            from kukiihome_preprocessor.pipelines.body_id import (
                BodyIdConfig,
                BodyIdRecognizer,
            )
            from kukiihome_preprocessor.pipelines.identity import BodyIdPipeline

            body_id_recognizer = BodyIdRecognizer(
                BodyIdConfig(
                    model_path=config.body_id_model_path,
                    match_threshold=config.body_id_match_threshold,
                    providers=tuple(config.body_id_providers),
                )
            )
            identity_pipelines.append(BodyIdPipeline(body_id_recognizer))

        if config.ccreid_enabled:
            # CC-ReID reuses BodyIdRecognizer (a generic person-crop
            # embedder), configured for the CAL/AIM input size. Lazy
            # import for the same reason as body_id.
            from kukiihome_preprocessor.pipelines.body_id import (
                BodyIdConfig,
                BodyIdRecognizer,
            )
            from kukiihome_preprocessor.pipelines.identity import CCReIDPipeline

            ccreid_recognizer = BodyIdRecognizer(
                BodyIdConfig(
                    model_path=config.ccreid_model_path,
                    match_threshold=config.ccreid_match_threshold,
                    input_height=config.ccreid_input_height,
                    input_width=config.ccreid_input_width,
                    providers=tuple(config.ccreid_providers),
                )
            )
            identity_pipelines.append(CCReIDPipeline(ccreid_recognizer))

        if config.pet_enabled:
            from kukiihome_preprocessor.pipelines.identity import PetPipeline
            from kukiihome_preprocessor.pipelines.pet import (
                PetConfig,
                PetRecognizer,
            )

            pet_recognizer = PetRecognizer(
                PetConfig(
                    model_path=config.pet_model_path,
                    match_threshold=config.pet_match_threshold,
                    providers=tuple(config.pet_providers),
                )
            )
            identity_pipelines.append(PetPipeline(pet_recognizer))

        if config.gait_enabled:
            # Gait is a TEMPORAL pipeline (per-track sequence). Heavy:
            # pulls in ultralytics (YOLO-seg) + onnxruntime — lazy import.
            from kukiihome_preprocessor.pipelines.gait import (
                GaitConfig,
                GaitRecognizer,
            )
            from kukiihome_preprocessor.pipelines.identity import GaitPipeline

            gait_recognizer = GaitRecognizer(
                GaitConfig(
                    model_path=config.gait_model_path,
                    seg_weights=config.gait_seg_weights,
                    match_threshold=config.gait_match_threshold,
                    min_frames=config.gait_min_frames,
                    seg_device=config.gait_seg_device,
                    providers=tuple(config.gait_providers),
                )
            )
            identity_pipelines.append(GaitPipeline(gait_recognizer))

        identity_router = None
        if identity_pipelines:
            from kukiihome_preprocessor.pipelines.identity import IdentityRouter

            identity_router = IdentityRouter(identity_pipelines)
            logger.info("identity_router.built", pipelines=identity_router.pipeline_names)

        detector = None
        if config.detection_enabled:
            # Lazy import: only pay the ultralytics + torch cost when
            # actually running detection. Synthetic-mode runs and
            # detection-off RTSP runs skip this entirely.
            from kukiihome_preprocessor.pipelines.detection import (
                DetectionConfig,
                YOLODetector,
            )

            detector = YOLODetector(
                DetectionConfig(
                    backend=config.detection_backend,  # type: ignore[arg-type]
                    weights=config.detection_weights,
                    confidence_min=config.detection_confidence_min,
                    image_size=config.detection_image_size,
                    device=config.detection_device,
                )
            )

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
        await camera_subscriber.close()
        await actor_subscriber.close()
        logger.info("preprocessor.stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_from_env()
    asyncio.run(_run(config))


if __name__ == "__main__":
    main()
