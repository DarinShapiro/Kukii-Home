"""CC-ReID (cloth-changing re-ID) as an :class:`IdentityPipeline`.

OSNet body-ID (``body_id_osnet``) keys heavily on clothing colour /
texture, so it's a *transient* same-visit signal — it stops matching
the moment someone changes outfit. CAL/AIM-family CC-ReID models are
trained with a clothes-adversarial loss to extract clothes-*irrelevant*
features (body shape, build, structure), so the embedding survives
outfit changes. That makes it a **durable** body anchor — on cameras
where face routinely fails (steep top-down, distance, back-of-head, the
pool cam) it can partially substitute for face as a long-term template.

Mechanically CC-ReID is OSNet's twin — crop the person, run a CNN, get
an L2-normalized embedding, cosine-match against the enrolled corpus —
so this pipeline reuses :class:`BodyIdRecognizer` (a generic
person-crop embedder) configured for the CC-ReID input size (384x192).
The only differences from body-ID are:

* a distinct modality (``body_shape``) so its enrollment templates live
  in their own corpus slice,
* a distinct ``match_method`` (``ccreid_cal``) so fusion can weight the
  durable clothes-invariant signal above transient OSNet (Epic 10.10.3),
* a higher default match threshold isn't assumed here — the recognizer
  carries its own (see :class:`~...config.PreprocessorConfig`).

Cost gating mirrors body-ID: ``depends_on=("face_arcface",)`` +
``skip_when_upstream_matched_above=0.85`` so CC-ReID inference is skipped
for any track face already nailed. When face fails, CC-ReID runs and
becomes the anchor — which is the whole point.
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


# Same short-circuit rationale as body-ID: a face match >= 0.85 is
# strong enough to skip the body fallback for that track.
_DEFAULT_SKIP_THRESHOLD = 0.85


class CCReIDPipeline:
    """Cloth-changing body re-ID branch of the identity router.

    Reads the ``body_shape`` corpus slice; receives person detections
    pre-filtered by the router to drop track_ids face already covered.
    Emits ActorMatches stamped ``match_method='ccreid_cal'`` with the
    inherited track_id.
    """

    name = "ccreid_cal"
    modality = "body_shape"
    triggers_on = frozenset({"person"})
    depends_on: tuple[str, ...] = ("face_arcface",)
    """Force-sequence after face so the router can apply the
    short-circuit. Missing face_arcface from the router's pipelines is
    fine — the router treats unsatisfied deps as 'no upstream', so
    CC-ReID runs on every person detection (face-free deployments /
    tests)."""

    skip_when_upstream_matched_above: float | None = _DEFAULT_SKIP_THRESHOLD

    # Capability descriptors (Epic 10.11.2) — scheduling/placement hints.
    resource_class = "gpu"
    batchable = True  # ResNet50 stacks N person crops into one inference call
    temporal = False
    est_cost_ms = 75  # ResNet50 @ 384x192, amortized per person in a batch
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
        # IdentifiedEntity (correlation joins on track_id).
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
            match = detected_body_to_actor_match(body, frame_ts=frame.ts, match_method="ccreid_cal")
            if match is not None:
                out.append(match)
        return tuple(out)
