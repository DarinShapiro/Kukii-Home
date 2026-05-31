"""Backward-compat re-export shim for motion detection.

The actual implementation moved to :mod:`kukiihome_shared.motion` in
Epic 10.8.2 so the HA-agent add-on can use motion detection without
pulling the entire preprocessor stack (torch + onnxruntime + insightface)
onto Yellow's ~4GB image budget.

Existing code that imports from ``kukiihome_preprocessor.motion``
continues to work unchanged — these names are re-exported below.

New code should import directly from ``kukiihome_shared.motion``.
"""

from __future__ import annotations

from kukiihome_shared.motion import (
    FrameSource,
    MOG2MotionDetector,
    MotionConfig,
    MotionDecision,
    MotionDetector,
)

__all__ = [
    "FrameSource",
    "MOG2MotionDetector",
    "MotionConfig",
    "MotionDecision",
    "MotionDetector",
]
