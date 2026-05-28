"""Identity-pipeline router + Protocol + corpus snapshot.

The router is the single dispatch point that replaces the ad-hoc
``_run_face_recognition`` call in :class:`RTSPFrameBuffer`. With one
pipeline registered (face), behavior is identical. With N
pipelines, the router:

1. **Gates by detection kind** — only invokes pipelines whose
   ``triggers_on`` intersects the kinds present in the frame. A
   driveway frame with just a car never runs face recognition.
2. **Gates by enrollment availability** — pipelines that need an
   enrolled corpus (face/pet/plate) skip when the
   :class:`EnrolledCorpus` slice for their modality is empty. Saves
   the JPEG-decode + model-invocation cost when there's nothing to
   match against anyway.
3. **Dispatches concurrent branches in parallel** — independent
   pipelines (face, pet, plate touch disjoint detection kinds) run
   via :func:`asyncio.gather`. Wall-clock = max(branch_costs)
   instead of sum.

What's deliberately NOT in this minimal version (lands when the
second pipeline arrives):

* Per-backend semaphores (no contention with one pipeline)
* Budget timeout (hard to tune without two backends' real numbers)
* ``depends_on`` short-circuit chains (face → body_id) — needs
  body_id to motivate
* Per-pipeline telemetry / dropped-branch logging

The Protocol + corpus shape are forward-compatible: adding those
later doesn't change pipeline implementations.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    from sentihome_shared.preprocessor import ActorMatch, DetectionTag

    from sentihome_preprocessor.pipelines.rolling_buffer import BufferedFrame
    from sentihome_preprocessor.state import ActorCache


# ─── EnrolledCorpus: projected snapshot of the ActorCache ───────────


@dataclass(frozen=True)
class EnrolledCorpus:
    """Snapshot of :class:`ActorCache` projected by identity modality.

    The router builds one per ``identify()`` call so the N
    pipelines don't each re-walk the cache and rebuild numpy arrays.
    Each modality's slice is a dict ``actor_id -> typed embedding``;
    pipelines read only their own slice.

    Future modalities (pet DINOv2, plate text, body re-ID embedding)
    slot in as additional fields. ``actor_names`` is shared across
    all pipelines for the downstream IdentifiedEntity construction.
    """

    faces: dict[str, np.ndarray] = field(default_factory=dict)
    """``actor_id -> 512-d L2-normalized ArcFace embedding``."""

    pets: dict[str, np.ndarray] = field(default_factory=dict)
    """``actor_id -> DINOv2 centroid`` (Phase 10.5)."""

    plates: dict[str, str] = field(default_factory=dict)
    """``actor_id -> canonical plate text`` (Phase 10.6)."""

    actor_names: dict[str, str] = field(default_factory=dict)
    """``actor_id -> friendly name`` for downstream IdentifiedEntity
    rendering. Populated for any actor with a non-None ``name``."""

    @classmethod
    async def from_cache(cls, cache: ActorCache) -> EnrolledCorpus:
        faces: dict[str, np.ndarray] = {}
        pets: dict[str, np.ndarray] = {}
        plates: dict[str, str] = {}
        names: dict[str, str] = {}
        for actor in await cache.snapshot():
            if actor.face_embedding:
                faces[actor.actor_id] = np.asarray(actor.face_embedding, dtype=np.float32)
            if actor.pet_dinov2_centroid:
                pets[actor.actor_id] = np.asarray(actor.pet_dinov2_centroid, dtype=np.float32)
            if actor.plate_text:
                plates[actor.actor_id] = actor.plate_text
            if actor.name:
                names[actor.actor_id] = actor.name
        return cls(faces=faces, pets=pets, plates=plates, actor_names=names)


# ─── The Pipeline Protocol ──────────────────────────────────────────


class IdentityPipeline(Protocol):
    """Plugin contract for a per-modality identity pipeline.

    Implementations live in sibling modules (``face_pipeline.py``,
    later ``body_id_pipeline.py`` etc.). The router holds a list of
    these and dispatches by ``triggers_on``.
    """

    name: str
    """Stable identifier, also stamped on ActorMatch.match_method
    (``face_arcface``, ``body_id_osnet``, ``pet_dinov2``,
    ``plate_alpr``). Used for telemetry + skip-chain bookkeeping
    when the full router lands."""

    triggers_on: frozenset[str]
    """YOLO detection kinds that activate this pipeline. Face fires
    on ``{"person"}``; pet on ``{"dog", "cat"}``; plate on
    ``{"vehicle"}``."""

    def has_enrollments(self, corpus: EnrolledCorpus) -> bool:
        """``True`` if the corpus has at least one enrolled target
        for this modality. Returning ``False`` lets the router skip
        the JPEG-decode + model-invocation entirely."""
        ...

    async def run(
        self,
        *,
        frame: BufferedFrame,
        detections: tuple[DetectionTag, ...],
        corpus: EnrolledCorpus,
    ) -> tuple[ActorMatch, ...]:
        """Find and match every identity in this single frame.

        ``detections`` is pre-filtered to only the kinds in
        ``triggers_on`` for this frame — the pipeline can assume
        relevance. Returns ActorMatches keyed back to track_ids
        when correlation is possible; unmatched candidates are
        dropped (the contract has no concept of a 'phantom' match).
        """
        ...


# ─── The Router ─────────────────────────────────────────────────────


class IdentityRouter:
    """Dispatches identity pipelines over a window of frames.

    Holds the registered pipelines as a flat list. With one pipeline
    today (face), :meth:`identify` collapses to a single per-frame
    invocation. With N pipelines, asyncio.gather parallelizes the
    independent branches inside each frame.
    """

    def __init__(self, pipelines: Sequence[IdentityPipeline]) -> None:
        self._pipelines = list(pipelines)

    @property
    def pipeline_names(self) -> tuple[str, ...]:
        """For startup logging + ``/status``. Stable order."""
        return tuple(p.name for p in self._pipelines)

    async def identify(
        self,
        *,
        buffered: Sequence[BufferedFrame],
        detections: tuple[DetectionTag, ...],
        cache: ActorCache,
    ) -> tuple[ActorMatch, ...]:
        """Run every triggered pipeline against every frame in the
        window; merge the results.

        Skips silently when:
        * no pipelines registered (router built without any)
        * no detections (nothing to trigger on)
        * no enrolled actors at all (corpus empty across the board)

        Per-frame: bucket detections by frame_ts, look up the
        matching :class:`BufferedFrame`, run every triggered pipeline
        concurrently (``asyncio.gather``).
        """
        if not self._pipelines or not detections:
            return ()
        corpus = await EnrolledCorpus.from_cache(cache)

        frames_by_ts: dict[float, BufferedFrame] = {f.ts: f for f in buffered}
        dets_by_ts: dict[float, list[DetectionTag]] = defaultdict(list)
        for d in detections:
            dets_by_ts[d.frame_ts].append(d)

        tasks: list[Awaitable[tuple[ActorMatch, ...]]] = []
        for ts, frame_dets in dets_by_ts.items():
            frame = frames_by_ts.get(ts)
            if frame is None:
                continue
            kinds = {d.kind for d in frame_dets}
            for pipeline in self._pipelines:
                triggered = pipeline.triggers_on & kinds
                if not triggered:
                    continue
                if not pipeline.has_enrollments(corpus):
                    continue
                # Pre-filter detections to just the triggering kinds.
                relevant_dets = tuple(d for d in frame_dets if d.kind in triggered)
                tasks.append(
                    pipeline.run(
                        frame=frame,
                        detections=relevant_dets,
                        corpus=corpus,
                    )
                )

        if not tasks:
            return ()

        per_pipeline_results = await asyncio.gather(*tasks)
        return tuple(m for batch in per_pipeline_results for m in batch)
