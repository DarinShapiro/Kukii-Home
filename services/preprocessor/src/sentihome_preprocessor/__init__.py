"""sentihome_preprocessor — motion-gated 24/7 frame preprocessing.

For service-mode NVR adapters. Consumes RTSP, runs motion detection as a
gating function, enriches motion-flagged frames with the detector, caches
results for the unified ``nvr.get_frame_window`` contract.

See: docs/architecture/03.5-nvr-adapter-layer.md, docs/architecture/08-detection-pipeline.md
"""

from __future__ import annotations

__version__ = "0.1.0"

from sentihome_preprocessor.cache import InMemoryMetadataCache, MetadataCache
from sentihome_preprocessor.corroboration import CorroboratedSignal, corroborate
from sentihome_preprocessor.motion import (
    MOG2MotionDetector,
    MotionConfig,
    MotionDecision,
    MotionDetector,
)

__all__ = [
    "CorroboratedSignal",
    "InMemoryMetadataCache",
    "MOG2MotionDetector",
    "MetadataCache",
    "MotionConfig",
    "MotionDecision",
    "MotionDetector",
    "__version__",
    "corroborate",
]
