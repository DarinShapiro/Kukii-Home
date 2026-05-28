"""Face recognition as an :class:`IdentityPipeline`.

Thin adapter over the existing :class:`FaceRecognizer` model
wrapper. The split:

* ``pipelines/face.py`` owns the model contract (InsightFace +
  cosine match + helpers). Stays modality-agnostic of "router" /
  "corpus" / "actor cache" concepts.
* This module owns the *pipeline* contract — how face fits into
  the router's dispatch + the shared :class:`EnrolledCorpus` shape.

Lets us bring in body-ID / pet / plate later by writing a new
``*_pipeline.py`` adapter, without touching the existing
``FaceRecognizer``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sentihome_preprocessor.pipelines.face import (
    associate_face_to_person,
    detected_face_to_actor_match,
    jpeg_to_bgr,
)

if TYPE_CHECKING:
    from sentihome_shared.preprocessor import ActorMatch, DetectionTag

    from sentihome_preprocessor.pipelines.face import FaceRecognizer
    from sentihome_preprocessor.pipelines.identity.router import EnrolledCorpus
    from sentihome_preprocessor.pipelines.rolling_buffer import BufferedFrame


class FacePipeline:
    """Face-recognition branch of the identity router.

    Reads the ``faces`` slice of :class:`EnrolledCorpus`, runs
    :meth:`FaceRecognizer.detect_and_match` on the frame, joins each
    matched face to the spatially-containing person detection via
    IoU overlap, and emits ActorMatches stamped with
    ``match_method="face_arcface"`` and the inherited ``track_id``.

    Unmatched faces (no enrolled embedding within threshold) are
    dropped — the wire contract has no concept of a 'phantom' face.
    """

    name = "face_arcface"
    triggers_on = frozenset({"person"})
    depends_on: tuple[str, ...] = ()
    """Face has no upstream — runs first in any chain that
    includes it (body_id_osnet declares depends_on=('face_arcface',))."""

    skip_when_upstream_matched_above: float | None = None
    """Face is always the cheapest signal we have for people, so
    even with an upstream it would run anyway. Kept None for
    forward-compat (e.g. if a future pipeline detects "person not in
    frame" cheaply and we want face to skip)."""

    def __init__(self, recognizer: FaceRecognizer) -> None:
        self._recognizer = recognizer

    def has_enrollments(self, corpus: EnrolledCorpus) -> bool:
        return bool(corpus.faces)

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

        # Build the person-bbox list for face-to-person association.
        # Untracked person dets are dropped: without a track_id we
        # can't correlate identity to detection downstream.
        person_bboxes = [
            (d.track_id, d.bbox)
            for d in detections
            if d.kind == "person" and d.track_id is not None
        ]
        if not person_bboxes:
            return ()

        faces = await self._recognizer.detect_and_match(bgr, corpus.faces)

        out: list[ActorMatch] = []
        for face in faces:
            if face.matched_actor_id is None:
                continue
            track_id = associate_face_to_person(face.bbox, person_bboxes)
            match = detected_face_to_actor_match(face, frame_ts=frame.ts, track_id=track_id)
            if match is not None:
                out.append(match)
        return tuple(out)
