"""Pet recognition as an :class:`IdentityPipeline`.

Thin adapter over :class:`PetRecognizer`. Independent branch — no
``depends_on`` (pets don't relate to person matching), so the router
runs it in parallel with the face/body chain and the plate branch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kukiihome_preprocessor.pipelines.face import jpeg_to_bgr
from kukiihome_preprocessor.pipelines.pet import detected_pet_to_actor_match

if TYPE_CHECKING:
    from kukiihome_shared.preprocessor import ActorMatch, DetectionTag

    from kukiihome_preprocessor.pipelines.identity.router import EnrolledCorpus
    from kukiihome_preprocessor.pipelines.pet import PetRecognizer
    from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame


_PET_KINDS = frozenset({"dog", "cat"})


class PetPipeline:
    """Pet-recognition branch of the identity router.

    Reads ``corpus.pets``; receives dog/cat detections from the
    router. Each tracked animal is cropped, embedded (DINOv2), and
    matched against enrolled pets. Emits ActorMatches stamped
    ``match_method='pet_dinov2'`` with the animal's own track_id
    (no IoU sub-association — the detection IS the animal).
    """

    name = "pet_dinov2"
    modality = "pet"
    triggers_on = frozenset({"dog", "cat"})
    depends_on: tuple[str, ...] = ()
    """Independent — pets don't depend on person/face matching."""

    skip_when_upstream_matched_above: float | None = None

    # Capability descriptors (Epic 10.11.2) — scheduling/placement hints.
    resource_class = "gpu"
    batchable = True  # DINOv2 stacks N animal crops into one inference call
    temporal = False
    est_cost_ms = 80  # DINOv2 CLS embed, amortized per animal in a batch
    placement_hint: str | None = None

    def __init__(self, recognizer: PetRecognizer) -> None:
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
        bgr = jpeg_to_bgr(frame.jpeg_bytes)
        if bgr is None:
            return ()

        # Tracked dog/cat detections only — untracked can't be
        # correlated downstream into an IdentifiedEntity.
        pets = [
            (d.track_id, d.kind, d.bbox)
            for d in detections
            if d.kind in _PET_KINDS and d.track_id is not None
        ]
        if not pets:
            return ()

        detected = await self._recognizer.identify_pets(bgr, pets, corpus.slice(self.modality))

        out: list[ActorMatch] = []
        for pet in detected:
            match = detected_pet_to_actor_match(pet, frame_ts=frame.ts)
            if match is not None:
                out.append(match)
        return tuple(out)
