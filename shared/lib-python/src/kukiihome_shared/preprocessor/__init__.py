"""Wire contracts + client for the recognition preprocessor service.

The preprocessor is a separate-process service running on the
inference box (4090 GPU). HA-side services (memory, vlm-router,
dispatcher, ha-agent) talk to it ONLY through:

* :mod:`kukiihome_shared.preprocessor.contracts` — Pydantic schemas
  for every payload that crosses the process boundary.
* :mod:`kukiihome_shared.preprocessor.nats_subjects` — canonical NATS
  topic names for the (small) NATS surface — broadcast-only config
  state from memory to preprocessor.
* :mod:`kukiihome_shared.preprocessor.client` — thin httpx-based
  client wrapping the preprocessor's REST surface (health, status,
  frame-window pull, knob tuning, actor enrollment).

The preprocessor implementation lives in ``services/preprocessor/``.
HA-side code must never import from there — only from this package.
This is enforced by the decoupling-guard test at
``services/preprocessor/tests/test_no_ha_side_imports.py``.

Architectural decisions: ``planning/epics/10-identity-recognition.md``.
"""

from kukiihome_shared.preprocessor.contracts import (
    ActorEnrollmentEvent,
    ActorMatch,
    CameraConfigEvent,
    DetectionTag,
    FrameRef,
    FrameWindow,
    IdentifiedEntity,
    KnobAdjustment,
    PreprocessorStatus,
    TrackEmbedding,
)
from kukiihome_shared.preprocessor.nats_subjects import (
    ALL_ACTOR_SUBJECTS,
    ALL_CAMERA_SUBJECTS,
    SUBJECT_ACTOR_DEACTIVATED,
    SUBJECT_ACTOR_ENROLLED,
    SUBJECT_ACTOR_UPDATED,
    SUBJECT_CAMERA_CONFIGURED,
    SUBJECT_CAMERA_REMOVED,
)

__all__ = [
    "ALL_ACTOR_SUBJECTS",
    "ALL_CAMERA_SUBJECTS",
    "SUBJECT_ACTOR_DEACTIVATED",
    "SUBJECT_ACTOR_ENROLLED",
    "SUBJECT_ACTOR_UPDATED",
    "SUBJECT_CAMERA_CONFIGURED",
    "SUBJECT_CAMERA_REMOVED",
    "ActorEnrollmentEvent",
    "ActorMatch",
    "CameraConfigEvent",
    "DetectionTag",
    "FrameRef",
    "FrameWindow",
    "IdentifiedEntity",
    "KnobAdjustment",
    "PreprocessorStatus",
    "TrackEmbedding",
]
