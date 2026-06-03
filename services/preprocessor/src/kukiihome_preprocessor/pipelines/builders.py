"""Factory functions that build the detector + identity DAG from config.

Extracted from __main__ so BOTH the live service and the offline enrichment
worker construct the exact same pipelines (no duplication / drift). Imports
are lazy inside each branch — heavyweight deps (torch, insightface,
onnxruntime, ultralytics-seg) are only paid for the pipelines actually
enabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from kukiihome_preprocessor.config import PreprocessorConfig
    from kukiihome_preprocessor.pipelines.detection import YOLODetector
    from kukiihome_preprocessor.pipelines.identity import IdentityPipeline, IdentityRouter

logger = structlog.get_logger(__name__)


def build_identity_pipelines(config: PreprocessorConfig) -> list[IdentityPipeline]:
    """Construct the enabled identity pipelines (face / body-ID / CC-ReID /
    pet / gait) as a flat list, in registration order.

    The single construction point for the identity DAG's nodes. Both
    :func:`build_identity_router` (live per-frame match dispatch) and the
    offline enrichment worker's always-embed pass (which calls
    :func:`~kukiihome_preprocessor.pipelines.identity.collect_embeddings` over
    these directly) build from this, so the set of models — and their config
    — never drifts between matching and embedding.
    """
    identity_pipelines: list[IdentityPipeline] = []

    if config.face_recognition_enabled:
        from kukiihome_preprocessor.pipelines.face import FaceConfig, FaceRecognizer
        from kukiihome_preprocessor.pipelines.identity import FacePipeline

        identity_pipelines.append(FacePipeline(FaceRecognizer(FaceConfig(
            model_pack=config.face_model_pack,
            match_threshold=config.face_match_threshold,
            det_confidence_min=config.face_det_confidence_min,
            det_size=config.face_det_size,
            providers=tuple(config.face_providers),
        ))))

    if config.body_id_enabled:
        from kukiihome_preprocessor.pipelines.body_id import BodyIdConfig, BodyIdRecognizer
        from kukiihome_preprocessor.pipelines.identity import BodyIdPipeline

        identity_pipelines.append(BodyIdPipeline(BodyIdRecognizer(BodyIdConfig(
            model_path=config.body_id_model_path,
            match_threshold=config.body_id_match_threshold,
            providers=tuple(config.body_id_providers),
        ))))

    if config.ccreid_enabled:
        from kukiihome_preprocessor.pipelines.body_id import BodyIdConfig, BodyIdRecognizer
        from kukiihome_preprocessor.pipelines.identity import CCReIDPipeline

        identity_pipelines.append(CCReIDPipeline(BodyIdRecognizer(BodyIdConfig(
            model_path=config.ccreid_model_path,
            match_threshold=config.ccreid_match_threshold,
            input_height=config.ccreid_input_height,
            input_width=config.ccreid_input_width,
            providers=tuple(config.ccreid_providers),
        ))))

    if config.pet_enabled:
        from kukiihome_preprocessor.pipelines.identity import PetPipeline
        from kukiihome_preprocessor.pipelines.pet import PetConfig, PetRecognizer

        identity_pipelines.append(PetPipeline(PetRecognizer(PetConfig(
            model_path=config.pet_model_path,
            match_threshold=config.pet_match_threshold,
            providers=tuple(config.pet_providers),
        ))))

    if config.gait_enabled:
        from kukiihome_preprocessor.pipelines.gait import GaitConfig, GaitRecognizer
        from kukiihome_preprocessor.pipelines.identity import GaitPipeline

        identity_pipelines.append(GaitPipeline(GaitRecognizer(GaitConfig(
            model_path=config.gait_model_path,
            seg_weights=config.gait_seg_weights,
            match_threshold=config.gait_match_threshold,
            min_frames=config.gait_min_frames,
            seg_device=config.gait_seg_device,
            providers=tuple(config.gait_providers),
        ))))

    return identity_pipelines


def build_identity_router(config: PreprocessorConfig) -> IdentityRouter | None:
    """Build the IdentityRouter from the enabled identity pipelines
    (face / body-ID / CC-ReID / pet / gait → fusion). None if none enabled."""
    identity_pipelines = build_identity_pipelines(config)
    if not identity_pipelines:
        return None
    from kukiihome_preprocessor.pipelines.identity import IdentityRouter

    router = IdentityRouter(identity_pipelines)
    logger.info("identity_router.built", pipelines=router.pipeline_names)
    return router


def build_detector(config: PreprocessorConfig) -> YOLODetector | None:
    """Build the YOLO detector from config. None if detection disabled."""
    if not config.detection_enabled:
        return None
    from kukiihome_preprocessor.pipelines.detection import DetectionConfig, YOLODetector

    return YOLODetector(DetectionConfig(
        backend=config.detection_backend,  # type: ignore[arg-type]
        weights=config.detection_weights,
        confidence_min=config.detection_confidence_min,
        per_class_confidence=config.detection_per_class_confidence,
        image_size=config.detection_image_size,
        device=config.detection_device,
    ))
