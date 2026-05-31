"""kukiihome_detector — fast-detector models.

YOLO/RT-DETR (objects), SCRFD/RetinaFace (face detection), ArcFace/AdaFace
(face recognition), OSNet (body re-ID), pose estimation, plate OCR, pet
recognition, stillness/drowning classifiers.

See: docs/architecture/08-detection-pipeline.md
"""

from __future__ import annotations

__version__ = "0.1.0"

from kukiihome_detector.facade import (
    Detection,
    Detector,
    DetectorConfig,
    EnrichmentResult,
    FaceMatch,
    Pose,
)

__all__ = [
    "Detection",
    "Detector",
    "DetectorConfig",
    "EnrichmentResult",
    "FaceMatch",
    "Pose",
    "__version__",
]
