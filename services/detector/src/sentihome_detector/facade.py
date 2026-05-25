"""Detector facade — the single interface to all detection models.

Models are loaded lazily (and only when their feature flag is enabled in
``DetectorConfig``). This keeps the test surface fast and avoids forcing every
deployment to ship every model.

Implementations live under :mod:`sentihome_detector.models` and are imported
on demand. For Epic 4 we ship the facade + a stub model registry; real ONNX
runtime integrations land in subsequent issues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Output types
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Detection:
    """One YOLO-style detection."""

    class_name: str
    confidence: float
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    track_id: str | None = None


@dataclass(frozen=True)
class FaceMatch:
    """One face recognition match against the gallery."""

    bbox: tuple[float, float, float, float]
    embedding: tuple[float, ...]
    """The 512-dim ArcFace embedding. Truncated representation; real impl uses np.array."""
    matched_actor_id: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class Pose:
    """A pose estimation keypoint set."""

    bbox: tuple[float, float, float, float]
    keypoints: tuple[tuple[float, float, float], ...]
    """Tuple of (x, y, visibility) per keypoint."""


@dataclass
class EnrichmentResult:
    """Bundled output of running the detector on one frame."""

    detections: list[Detection] = field(default_factory=list)
    faces: list[FaceMatch] = field(default_factory=list)
    poses: list[Pose] = field(default_factory=list)
    embeddings: dict[str, tuple[float, ...]] = field(default_factory=dict)
    """Per-track re-ID embeddings keyed by track_id."""
    quality_score: float = 1.0
    quality_flags: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────


@dataclass
class DetectorConfig:
    """Detector feature flags + model paths.

    Each model is opt-in. Default config enables YOLO + face detection but
    leaves heavier models off until explicitly enabled.
    """

    yolo: bool = True
    yolo_model_path: str | None = None
    face_detection: bool = True
    face_detection_model_path: str | None = None
    face_recognition: bool = False
    face_recognition_model_path: str | None = None
    body_reid: bool = False
    body_reid_model_path: str | None = None
    pose: bool = False
    pose_model_path: str | None = None
    plate_ocr: bool = False
    plate_ocr_model_path: str | None = None
    pet_recognition: bool = False
    stillness_classifier: bool = False
    drowning_classifier: bool = False
    use_gpu: bool = False


# ─────────────────────────────────────────────────────────────────────
# Per-model loader stubs (real impls land in subsequent commits/issues)
# ─────────────────────────────────────────────────────────────────────


class _BaseModel:
    """Base class for individual detection models."""

    name = "base"

    def __init__(self, model_path: str | None, use_gpu: bool = False) -> None:
        self._model_path = model_path
        self._use_gpu = use_gpu
        self._loaded = False

    def load(self) -> None:
        """Load the model. Subclasses override."""
        if self._loaded:
            return
        logger.info(
            f"detector.{self.name}.load",
            model_path=self._model_path,
            use_gpu=self._use_gpu,
        )
        self._loaded = True

    def is_loaded(self) -> bool:
        return self._loaded


class _YOLOModel(_BaseModel):
    name = "yolo"

    def detect(self, frame: NDArray[np.uint8]) -> list[Detection]:
        """Run YOLO on a frame. Real impl uses ONNX runtime; stub returns [].

        Real implementation (gated behind issue follow-up):
            session = onnxruntime.InferenceSession(self._model_path)
            ... pre-process, infer, post-process to Detection objects ...
        """
        if not self._loaded:
            self.load()
        return []  # stub


class _FaceDetector(_BaseModel):
    name = "face_detector"

    def detect_faces(self, frame: NDArray[np.uint8]) -> list[tuple[float, float, float, float]]:
        """Return face bboxes. SCRFD/RetinaFace via ONNX. Stub returns []."""
        if not self._loaded:
            self.load()
        return []


class _FaceRecognizer(_BaseModel):
    name = "face_recognizer"

    def embed(
        self, frame: NDArray[np.uint8], bbox: tuple[float, float, float, float]
    ) -> tuple[float, ...]:
        """Return a 512-dim ArcFace embedding for a face crop. Stub returns zeros."""
        if not self._loaded:
            self.load()
        return tuple([0.0] * 512)


class _ReIDModel(_BaseModel):
    name = "body_reid"

    def embed(
        self, frame: NDArray[np.uint8], bbox: tuple[float, float, float, float]
    ) -> tuple[float, ...]:
        """Return a 512-dim OSNet body embedding. Stub returns zeros."""
        if not self._loaded:
            self.load()
        return tuple([0.0] * 512)


class _PoseEstimator(_BaseModel):
    name = "pose"

    def estimate(self, frame: NDArray[np.uint8]) -> list[Pose]:
        if not self._loaded:
            self.load()
        return []


class _PlateOCR(_BaseModel):
    name = "plate_ocr"

    def read(self, frame: NDArray[np.uint8], bbox: tuple[float, float, float, float]) -> str | None:
        if not self._loaded:
            self.load()
        return None


# ─────────────────────────────────────────────────────────────────────
# Facade
# ─────────────────────────────────────────────────────────────────────


class Detector:
    """The unified detector facade.

    Owns one instance of each enabled model. Routes ``enrich(frame)`` calls
    through the right models based on ``DetectorConfig``.

    Example::

        detector = Detector(DetectorConfig(yolo=True, face_recognition=True))
        result = detector.enrich(frame)
    """

    def __init__(self, config: DetectorConfig | None = None) -> None:
        self._config = config or DetectorConfig()
        self._yolo = (
            _YOLOModel(self._config.yolo_model_path, self._config.use_gpu)
            if self._config.yolo
            else None
        )
        self._face_detector = (
            _FaceDetector(self._config.face_detection_model_path, self._config.use_gpu)
            if self._config.face_detection
            else None
        )
        self._face_recognizer = (
            _FaceRecognizer(self._config.face_recognition_model_path, self._config.use_gpu)
            if self._config.face_recognition
            else None
        )
        self._reid = (
            _ReIDModel(self._config.body_reid_model_path, self._config.use_gpu)
            if self._config.body_reid
            else None
        )
        self._pose = (
            _PoseEstimator(self._config.pose_model_path, self._config.use_gpu)
            if self._config.pose
            else None
        )
        self._plate = (
            _PlateOCR(self._config.plate_ocr_model_path, self._config.use_gpu)
            if self._config.plate_ocr
            else None
        )

    @property
    def config(self) -> DetectorConfig:
        return self._config

    @property
    def enabled_models(self) -> list[str]:
        names: list[str] = []
        for model in [
            self._yolo,
            self._face_detector,
            self._face_recognizer,
            self._reid,
            self._pose,
            self._plate,
        ]:
            if model is not None:
                names.append(model.name)
        return names

    def enrich(self, frame: NDArray[np.uint8]) -> EnrichmentResult:
        """Run all enabled models on the frame and return their combined output."""
        result = EnrichmentResult()

        if self._yolo:
            result.detections = self._yolo.detect(frame)

        if self._face_detector:
            face_bboxes = self._face_detector.detect_faces(frame)
            for bbox in face_bboxes:
                embedding: tuple[float, ...] = ()
                if self._face_recognizer:
                    embedding = self._face_recognizer.embed(frame, bbox)
                result.faces.append(FaceMatch(bbox=bbox, embedding=embedding))

        if self._reid and result.detections:
            for det in result.detections:
                if det.class_name == "person" and det.track_id:
                    result.embeddings[det.track_id] = self._reid.embed(frame, det.bbox)

        if self._pose:
            result.poses = self._pose.estimate(frame)

        return result

    def select_best_frames(
        self,
        frames: list[NDArray[np.uint8]],
        *,
        target_count: int = 8,
    ) -> list[int]:
        """Detector-guided frame selection per §08.

        Picks the indices of ``target_count`` frames where the primary subject
        is most visible, central, and unoccluded. Real implementation uses
        per-frame detection scores; stub returns the first N indices.
        """
        # TODO: real selection logic when detectors are wired up.
        return list(range(min(target_count, len(frames))))
