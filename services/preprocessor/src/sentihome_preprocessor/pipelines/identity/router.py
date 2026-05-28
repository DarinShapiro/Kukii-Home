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

What's NOT yet in (lands when motivated):

* Per-backend semaphores (no contention worth managing)
* Budget timeout (hard to tune without two backends' real numbers)
* Per-pipeline telemetry / dropped-branch logging

Phase 10.5.1 added ``depends_on`` + ``skip_when_upstream_matched_above``
to support the face → body-ID chain: body-ID only fires for tracks
the face pipeline didn't already nail. Without this, every person
detection would pay body-ID cost on top of face cost — defeating
the whole point of body-ID-as-fallback.
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

    bodies: dict[str, np.ndarray] = field(default_factory=dict)
    """``actor_id -> 512-d L2-normalized OSNet body embedding``.
    Powers Phase 10.5 body re-ID — the fallback when face isn't
    visible."""

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
        bodies: dict[str, np.ndarray] = {}
        pets: dict[str, np.ndarray] = {}
        plates: dict[str, str] = {}
        names: dict[str, str] = {}
        for actor in await cache.snapshot():
            if actor.face_embedding:
                faces[actor.actor_id] = np.asarray(actor.face_embedding, dtype=np.float32)
            if actor.body_embedding:
                bodies[actor.actor_id] = np.asarray(actor.body_embedding, dtype=np.float32)
            if actor.pet_dinov2_centroid:
                pets[actor.actor_id] = np.asarray(actor.pet_dinov2_centroid, dtype=np.float32)
            if actor.plate_text:
                plates[actor.actor_id] = actor.plate_text
            if actor.name:
                names[actor.actor_id] = actor.name
        return cls(faces=faces, bodies=bodies, pets=pets, plates=plates, actor_names=names)


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

    depends_on: tuple[str, ...]
    """Pipeline ``name``s that must run *before* this one in the same
    branch. Used for fallback chains: body-ID declares
    ``depends_on=("face_arcface",)`` so the router runs face first
    and lets body-ID see the matches face produced. Default ``()``
    — independent pipeline, free to run in parallel with any branch
    that doesn't list it as a dep."""

    skip_when_upstream_matched_above: float | None
    """If set: when this pipeline runs, drop any detection whose
    track_id already has a match (from an upstream pipeline in the
    same branch) with confidence >= this threshold. The short-circuit
    that makes face → body-ID worthwhile — if face nailed Alice at
    0.91, don't pay body-ID cost for that same person. ``None``
    means 'always run for every triggered track'."""

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

    Holds the registered pipelines and topo-sorts them into
    **branches** at construction time. A branch is a sequence of
    pipelines linked by ``depends_on``; independent branches run in
    parallel (``asyncio.gather``), pipelines within a branch run
    sequentially with short-circuit filtering.

    Example pipeline set ``[face, body_id, pet, plate]`` with
    ``body_id.depends_on = ("face_arcface",)`` produces three
    branches:

    * ``[face, body_id]`` — sequential, body_id sees face's matches
    * ``[pet]``           — independent
    * ``[plate]``         — independent

    All three branches dispatch concurrently per frame; within the
    face-body chain, body_id skips track_ids face already matched
    above ``skip_when_upstream_matched_above``.
    """

    def __init__(self, pipelines: Sequence[IdentityPipeline]) -> None:
        self._pipelines = list(pipelines)
        self._branches = _build_branches(self._pipelines)

    @property
    def pipeline_names(self) -> tuple[str, ...]:
        """For startup logging + ``/status``. Stable order."""
        return tuple(p.name for p in self._pipelines)

    @property
    def branch_summary(self) -> tuple[tuple[str, ...], ...]:
        """For debugging + telemetry. Each inner tuple is one branch
        in execution order. ``(("face_arcface", "body_id_osnet"),
        ("pet_dinov2",), ("plate_lpr",))``."""
        return tuple(tuple(p.name for p in branch) for branch in self._branches)

    async def identify(
        self,
        *,
        buffered: Sequence[BufferedFrame],
        detections: tuple[DetectionTag, ...],
        cache: ActorCache,
    ) -> tuple[ActorMatch, ...]:
        """Run every triggered pipeline against every frame in the
        window; merge the results.

        Per frame: spawn one task per branch (concurrent). Each task
        walks its chain sequentially, with each downstream pipeline
        getting detections pre-filtered to drop track_ids upstream
        already matched above their ``skip_when_upstream_matched_above``
        threshold.

        Skips silently when there are no pipelines registered, no
        detections, or no enrolled actors across any modality.
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
            for branch in self._branches:
                # Skip a branch when no pipeline in it triggers on
                # any kind in this frame.
                if not any(p.triggers_on & kinds for p in branch):
                    continue
                tasks.append(_run_branch(branch, frame, tuple(frame_dets), corpus))

        if not tasks:
            return ()

        per_branch_results = await asyncio.gather(*tasks)
        return tuple(m for batch in per_branch_results for m in batch)


# ─── branch building + per-branch execution ─────────────────────────


def _build_branches(
    pipelines: Sequence[IdentityPipeline],
) -> list[list[IdentityPipeline]]:
    """Topo-sort pipelines into dep-linked chains.

    A branch is a maximal sequence ``[p1, p2, ...]`` where each
    ``p_i+1`` lists some pipeline in the same branch in its
    ``depends_on``. Pipelines with no deps + no dependents form
    singleton branches.

    For the minimal multi-pipeline case (face + body_id + pet +
    plate, where only body_id depends on face), this produces
    ``[[face, body_id], [pet], [plate]]``. The router runs the
    three branches concurrently per frame.

    Missing-dep behavior: if a pipeline declares ``depends_on=("X",)``
    but no pipeline named X is registered, that dep is silently
    treated as already-satisfied (the pipeline becomes a singleton
    branch). Lets us register a subset (face only, for tests) without
    body_id refusing to load.
    """
    by_name = {p.name: p for p in pipelines}

    # Build adjacency: each pipeline -> the set of pipelines that
    # depend on it (downstream). Only edges where both endpoints are
    # registered count.
    downstream: dict[str, list[IdentityPipeline]] = defaultdict(list)
    for p in pipelines:
        for dep_name in p.depends_on:
            if dep_name in by_name:
                downstream[dep_name].append(p)

    # Roots are pipelines that nobody else's depends_on names.
    has_upstream = {p.name for p in pipelines if any(d in by_name for d in p.depends_on)}
    roots = [p for p in pipelines if p.name not in has_upstream]

    # Each root grows into a branch by walking its downstreams.
    # Simple DFS; assumes no cycles (and no cycle could be valid —
    # depends_on is anti-symmetric by intent).
    branches: list[list[IdentityPipeline]] = []
    seen: set[str] = set()
    for root in roots:
        branch: list[IdentityPipeline] = []
        stack = [root]
        while stack:
            p = stack.pop(0)
            if p.name in seen:
                continue
            seen.add(p.name)
            branch.append(p)
            # Append children in registration order for determinism.
            stack.extend(downstream.get(p.name, []))
        branches.append(branch)
    return branches


async def _run_branch(
    branch: Sequence[IdentityPipeline],
    frame: BufferedFrame,
    detections: tuple[DetectionTag, ...],
    corpus: EnrolledCorpus,
) -> tuple[ActorMatch, ...]:
    """Execute one branch on one frame, sequentially.

    Threads the cumulative matches through each pipeline so
    downstream pipelines can short-circuit. The
    per-pipeline detection slice is filtered three ways:

    1. By ``triggers_on`` (kind gate)
    2. By ``has_enrollments`` (no enrolled corpus -> skip)
    3. By ``skip_when_upstream_matched_above`` (drop track_ids the
       upstream already matched confidently)
    """
    branch_matches: list[ActorMatch] = []
    for pipeline in branch:
        kinds_present = {d.kind for d in detections}
        triggered_kinds = pipeline.triggers_on & kinds_present
        if not triggered_kinds:
            continue
        if not pipeline.has_enrollments(corpus):
            continue

        # Track_ids that an upstream already matched above the
        # short-circuit threshold are dropped from this pipeline's
        # input.
        skip_above = pipeline.skip_when_upstream_matched_above
        if skip_above is not None and branch_matches:
            covered = {
                m.track_id
                for m in branch_matches
                if m.track_id is not None and m.confidence >= skip_above
            }
        else:
            covered = set()

        relevant = tuple(
            d
            for d in detections
            if d.kind in triggered_kinds and (d.track_id is None or d.track_id not in covered)
        )
        if not relevant:
            continue

        matches = await pipeline.run(frame=frame, detections=relevant, corpus=corpus)
        branch_matches.extend(matches)

    return tuple(branch_matches)
