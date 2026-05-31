"""Gait recognition as a temporal :class:`IdentityPipeline` (Epic 10.11.6).

Gait keys on walking dynamics across a frame *sequence*, not a single
frame, so it implements :meth:`run_sequence` (the temporal entry the
router dispatches ``temporal=True`` pipelines through) rather than the
per-frame :meth:`run`. The router builds the per-track frame sequence,
drops tracks an upstream pipeline (face) already nailed, and hands the
rest here.

Reads the ``gait`` corpus slice. Emits ActorMatches stamped
``match_method='gait_opengait'`` with the track_id and the freshest
frame's ts. Durable, clothing-/face-independent — the anchor where face
fails (Epic 10.10 / 10.11).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kukiihome_preprocessor.pipelines.gait import detected_gait_to_actor_match

if TYPE_CHECKING:
    from kukiihome_shared.preprocessor import ActorMatch, DetectionTag

    from kukiihome_preprocessor.pipelines.gait import GaitRecognizer
    from kukiihome_preprocessor.pipelines.identity.router import (
        EnrolledCorpus,
        TrackSequence,
    )
    from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame


# Gait is the heaviest identity signal; skip tracks face already nailed
# above this confidence (the router applies it before building sequences).
_DEFAULT_SKIP_THRESHOLD = 0.85


class GaitPipeline:
    """Gait branch of the identity router — a temporal pipeline.

    The per-frame :meth:`run` is a no-op (gait needs a sequence); the
    router invokes :meth:`run_sequence` once per window.
    """

    name = "gait_opengait"
    modality = "gait"
    triggers_on = frozenset({"person"})
    depends_on: tuple[str, ...] = ()
    """Gait is an independent anchor — it doesn't sequence after face in a
    branch (it's not a per-frame branch at all). The router still gives
    it face's matches for the short-circuit via
    ``skip_when_upstream_matched_above``."""

    skip_when_upstream_matched_above: float | None = _DEFAULT_SKIP_THRESHOLD

    # Capability descriptors (Epic 10.11.2).
    resource_class = "gpu"
    batchable = False  # one clip -> one embedding, per track
    temporal = True
    est_cost_ms = 400  # YOLO-seg over the clip + one GaitBase inference
    placement_hint: str | None = None

    def __init__(self, recognizer: GaitRecognizer) -> None:
        self._recognizer = recognizer

    def has_enrollments(self, corpus: EnrolledCorpus) -> bool:
        return bool(corpus.slice(self.modality))

    async def run(
        self,
        *,
        frame: BufferedFrame,  # noqa: ARG002
        detections: tuple[DetectionTag, ...],  # noqa: ARG002
        corpus: EnrolledCorpus,  # noqa: ARG002
    ) -> tuple[ActorMatch, ...]:
        # Temporal pipeline: the per-frame entry is unused (the router
        # routes gait through run_sequence). Implemented as a defensive
        # no-op so GaitPipeline still satisfies the IdentityPipeline
        # Protocol's per-frame surface.
        return ()

    async def run_sequence(
        self,
        *,
        tracks: dict[str, TrackSequence],
        corpus: EnrolledCorpus,
    ) -> tuple[ActorMatch, ...]:
        gaits = await self._recognizer.identify_tracks(tracks, corpus.slice(self.modality))
        out: list[ActorMatch] = []
        for gait in gaits:
            match = detected_gait_to_actor_match(gait)
            if match is not None:
                out.append(match)
        return tuple(out)
