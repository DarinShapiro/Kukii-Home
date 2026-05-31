"""Body re-ID as an :class:`IdentityPipeline`.

Thin adapter over :class:`BodyIdRecognizer`. Declares the dep on
face_arcface + the short-circuit threshold that makes the chain
worthwhile — body re-ID only runs for track_ids face didn't already
nail above 0.85 cosine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kukiihome_preprocessor.pipelines.body_id import (
    detected_body_to_actor_match,
)
from kukiihome_preprocessor.pipelines.face import jpeg_to_bgr

if TYPE_CHECKING:
    from kukiihome_shared.preprocessor import ActorMatch, DetectionTag

    from kukiihome_preprocessor.pipelines.body_id import BodyIdRecognizer
    from kukiihome_preprocessor.pipelines.identity.router import EnrolledCorpus
    from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame


# Default short-circuit: if face already matched this track_id at
# >= 0.85 cosine, don't pay body_id cost — face is more accurate.
# 0.85 was chosen because that's also the markup threshold for
# "solid green box" — a face match strong enough to draw confidently
# is strong enough to skip the fallback.
_DEFAULT_SKIP_THRESHOLD = 0.85


class BodyIdPipeline:
    """Body re-ID branch of the identity router.

    Reads ``corpus.bodies``; receives person detections pre-filtered
    by the router to drop track_ids face already covered. Emits
    ActorMatches stamped ``match_method='body_id_osnet'`` with the
    inherited track_id.
    """

    name = "body_id_osnet"
    modality = "body"
    triggers_on = frozenset({"person"})
    depends_on: tuple[str, ...] = ("face_arcface",)
    """Force-sequence after face so the router can apply the
    short-circuit. Missing face_arcface from the router's pipelines
    is fine — the router treats unsatisfied deps as 'no upstream',
    so body_id runs on every person detection in that case (useful
    for body-only deployments / tests)."""

    skip_when_upstream_matched_above: float | None = _DEFAULT_SKIP_THRESHOLD

    # Capability descriptors (Epic 10.11.2) — scheduling/placement hints.
    resource_class = "gpu"
    batchable = True  # OSNet stacks N person crops into one inference call
    temporal = False
    est_cost_ms = 60  # OSNet embed, amortized per person in a batch
    placement_hint: str | None = None

    def __init__(self, recognizer: BodyIdRecognizer) -> None:
        self._recognizer = recognizer

    def has_enrollments(self, corpus: EnrolledCorpus) -> bool:
        return bool(corpus.slice(self.modality))

    async def run(
        self,
        *,
        frame: BufferedFrame,
        detections: tuple[DetectionTag, ...],
        corpus: EnrolledCorpus,
    ) -> tuple[ActorMatch, ...]:
        # Decode JPEG -> BGR. Corrupt frame: skip silently.
        bgr = jpeg_to_bgr(frame.jpeg_bytes)
        if bgr is None:
            return ()

        # Only tracked person dets can produce a downstream
        # IdentifiedEntity (correlation joins on track_id). Drop
        # untracked dets here rather than carrying them through.
        persons = [
            (d.track_id, d.bbox)
            for d in detections
            if d.kind == "person" and d.track_id is not None
        ]
        if not persons:
            return ()

        bodies = await self._recognizer.identify_persons(bgr, persons, corpus.slice(self.modality))

        out: list[ActorMatch] = []
        for body in bodies:
            match = detected_body_to_actor_match(body, frame_ts=frame.ts)
            if match is not None:
                out.append(match)
        return tuple(out)
