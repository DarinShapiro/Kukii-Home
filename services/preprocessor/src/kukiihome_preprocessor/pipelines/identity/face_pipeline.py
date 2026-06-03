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

import numpy as np

from kukiihome_preprocessor.pipelines.face import (
    detected_face_to_actor_match,
    jpeg_to_bgr,
)

if TYPE_CHECKING:
    from kukiihome_shared.preprocessor import ActorMatch, DetectionTag, TrackEmbedding

    from kukiihome_preprocessor.pipelines.face import FaceRecognizer
    from kukiihome_preprocessor.pipelines.identity.router import EnrolledCorpus
    from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame


class FacePipeline:
    """Face-recognition branch of the identity router.

    Reads the ``faces`` slice of :class:`EnrolledCorpus`. For each
    tracked person detection, runs the face detector on that person's
    head region (top slice of the box, native resolution) so distant
    faces survive InsightFace's det_size downscale, then matches the
    aligned face against the enrolled embeddings. Emits ActorMatches
    stamped with ``match_method="face_arcface"`` and the person's
    ``track_id`` (the region IS the person — no IoU association).

    Unmatched faces (no enrolled embedding within threshold) are
    dropped — the wire contract has no concept of a 'phantom' face.
    """

    name = "face_arcface"
    modality = "face"
    triggers_on = frozenset({"person"})
    depends_on: tuple[str, ...] = ()
    """Face has no upstream — runs first in any chain that
    includes it (body_id_osnet declares depends_on=('face_arcface',))."""

    skip_when_upstream_matched_above: float | None = None
    """Face is always the cheapest signal we have for people, so
    even with an upstream it would run anyway. Kept None for
    forward-compat (e.g. if a future pipeline detects "person not in
    frame" cheaply and we want face to skip)."""

    # Capability descriptors (Epic 10.11.2) — scheduling/placement hints.
    resource_class = "gpu"
    batchable = False  # detect+align run per head region, not one batched call
    temporal = False
    est_cost_ms = 200  # SCRFD detect + ArcFace embed per head crop
    placement_hint: str | None = None

    def __init__(self, recognizer: FaceRecognizer) -> None:
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

        # Run the face detector on the HEAD REGION of each tracked
        # person (the top slice of the YOLO person box) at native
        # resolution — not the whole frame, not the whole body. Why:
        # InsightFace resizes its input to det_size (640) before
        # detecting, so a small face in a big frame is downscaled into
        # oblivion. Scoping to the head region keeps a distant face a
        # large fraction of a small image, so it survives the resize.
        # InsightFace then aligns + crops the actual face to 112x112 —
        # the body never reaches the ArcFace recognizer. The region IS
        # this person, so the match inherits their track_id directly
        # (no IoU face->person association needed).
        h, w = bgr.shape[:2]
        out: list[ActorMatch] = []
        for d in detections:
            if d.kind != "person" or d.track_id is None:
                continue
            head = _head_region(bgr, d.bbox, w, h)
            if head is None:
                continue
            faces = await self._recognizer.detect_and_match(head, corpus.slice(self.modality))
            matched = [f for f in faces if f.matched_actor_id is not None]
            if not matched:
                continue
            # One identity per person — take the highest-confidence
            # matched face in the head region.
            best = max(matched, key=lambda f: f.match_confidence)
            match = detected_face_to_actor_match(best, frame_ts=frame.ts, track_id=d.track_id)
            if match is not None:
                out.append(match)
        return tuple(out)

    async def embed(
        self,
        *,
        frame: BufferedFrame,
        detections: tuple[DetectionTag, ...],
    ) -> tuple[TrackEmbedding, ...]:
        """Always-embed: one face :class:`TrackEmbedding` per tracked person
        whose face the detector finds, with no corpus and no matching.

        Same per-frame pattern as :meth:`BodyIdPipeline.embed`, but face is the
        most durable + discriminative signal (and the strongest argument for
        always-embedding it): a person seen face-on today is recognizable
        across days, outfits, and lighting. Detection is scoped to the head
        region (as in :meth:`run`), and the face inherits the person's
        ``track_id`` directly. When >1 face lands in the region (a background
        head peeking into the box) we keep the **largest** — the foreground
        person whose box this is. No face found / unusable embedding → nothing
        for that track this frame (face is simply absent, body still embeds)."""
        from kukiihome_shared.preprocessor import TrackEmbedding

        bgr = jpeg_to_bgr(frame.jpeg_bytes)
        if bgr is None:
            return ()
        h, w = bgr.shape[:2]
        out: list[TrackEmbedding] = []
        for d in detections:
            if d.kind != "person" or d.track_id is None:
                continue
            head = _head_region(bgr, d.bbox, w, h)
            if head is None:
                continue
            faces = [
                f
                for f in await self._recognizer.detect_and_match(head, {})
                if f.embedding is not None and f.embedding.any()
            ]
            if not faces:
                continue
            best = max(
                faces,
                key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
            )
            out.append(
                TrackEmbedding(
                    modality=self.modality,
                    match_method=self.name,
                    track_id=d.track_id,
                    frame_ts=frame.ts,
                    embedding=tuple(best.embedding.astype(float).tolist()),
                )
            )
        return tuple(out)


def _head_region(
    bgr: np.ndarray,
    person_bbox: tuple[float, float, float, float],
    w: int,
    h: int,
    *,
    top_fraction: float = 0.4,
    pad: float = 0.04,
) -> np.ndarray | None:
    """Crop the top slice of a (normalized) person bbox — where the
    face is — at native resolution.

    Takes the top ``top_fraction`` of the person box (head + shoulders)
    with a little padding, so the face detector gets a tight,
    high-resolution region rather than the whole body. Returns a
    contiguous BGR array, or None if the region is degenerate.
    """
    x1n, y1n, x2n, y2n = person_bbox
    box_h = y2n - y1n
    x1 = max(0, int((x1n - pad) * w))
    y1 = max(0, int((y1n - pad) * h))
    x2 = min(w, int((x2n + pad) * w))
    y2 = min(h, int((y1n + box_h * top_fraction + pad) * h))
    if x2 <= x1 or y2 <= y1:
        return None
    return np.ascontiguousarray(bgr[y1:y2, x1:x2])
