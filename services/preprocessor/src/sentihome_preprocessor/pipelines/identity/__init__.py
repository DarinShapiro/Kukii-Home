"""Identity pipelines + the router that dispatches them.

Each modality (face, body re-ID, pet, plate) implements
:class:`IdentityPipeline` and is registered with the
:class:`IdentityRouter`. The router walks the detections in each
frame, dispatches to triggered pipelines concurrently
(:func:`asyncio.gather`), and merges their ActorMatches.

See ``router.py`` for the design notes — short-circuit semantics,
parallelism axes, and what's intentionally left out of the minimal
shape.
"""

from sentihome_preprocessor.pipelines.identity.face_pipeline import FacePipeline
from sentihome_preprocessor.pipelines.identity.router import (
    EnrolledCorpus,
    IdentityPipeline,
    IdentityRouter,
)

__all__ = [
    "EnrolledCorpus",
    "FacePipeline",
    "IdentityPipeline",
    "IdentityRouter",
]
