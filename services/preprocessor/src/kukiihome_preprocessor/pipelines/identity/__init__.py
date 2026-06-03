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

from kukiihome_preprocessor.pipelines.identity.body_id_pipeline import (
    BodyIdPipeline,
)
from kukiihome_preprocessor.pipelines.identity.ccreid_pipeline import CCReIDPipeline
from kukiihome_preprocessor.pipelines.identity.face_pipeline import FacePipeline
from kukiihome_preprocessor.pipelines.identity.gait_pipeline import GaitPipeline
from kukiihome_preprocessor.pipelines.identity.pet_pipeline import PetPipeline
from kukiihome_preprocessor.pipelines.identity.resolve import (
    DEFAULT_RESOLVE_THRESHOLDS,
    resolve_event,
)
from kukiihome_preprocessor.pipelines.identity.router import (
    EmbeddingPipeline,
    EnrolledCorpus,
    IdentityPipeline,
    IdentityRouter,
    TemporalEmbeddingPipeline,
    TemporalIdentityPipeline,
    collect_embeddings,
    collect_track_embeddings,
)

__all__ = [
    "DEFAULT_RESOLVE_THRESHOLDS",
    "BodyIdPipeline",
    "CCReIDPipeline",
    "EmbeddingPipeline",
    "EnrolledCorpus",
    "FacePipeline",
    "GaitPipeline",
    "IdentityPipeline",
    "IdentityRouter",
    "PetPipeline",
    "TemporalEmbeddingPipeline",
    "TemporalIdentityPipeline",
    "collect_embeddings",
    "collect_track_embeddings",
    "resolve_event",
]
