"""Face detection + ArcFace embedding + match-against-enrolled.

The first real identity pipeline. Takes a BGR frame + the
ActorCache and emits :class:`~kukiihome_shared.preprocessor.ActorMatch`
records for every face it can confidently match to an enrolled
KnownActor. The existing correlation logic in
:class:`RTSPFrameBuffer` joins these with YOLO's person detections
via spatial IoU + track_id inheritance, then produces
IdentifiedEntities that drive the markup pipeline.

Why this pipeline matters regardless of the markup-efficacy harness
result: the VLM cannot identify specific people. Telling the system
"Alice is at the front door" can only come from face recognition;
even if the harness shows pixel markup is redundant, the JSON-only
output path still needs the recognition step to produce.

We use InsightFace's bundled detection + embedding because they're
the de-facto standard (well-supported, MIT-licensed, regularly
updated) and they ship together — one ``app.get(image)`` call gives
us both face bboxes AND 512-d ArcFace embeddings per face. We pick
the ``buffalo_s`` model pack by default: ~10MB total, ~50-100ms
per call on CPU, plenty for residential per-camera workloads. The
``buffalo_l`` pack (~280MB, slightly higher accuracy) is selectable
via config for the inference box.

Matching is cosine similarity against every enrolled actor's
stored embedding, threshold 0.5 by default (ArcFace embeddings are
L2-normalized to the unit sphere; ~0.5 cosine roughly corresponds
to "the same person in different lighting / angle"). The threshold
is a knob (KnobAdjustment) so the feedback-loop subsystem can tune
it per-camera once that lands.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np
from kukiihome_shared.preprocessor import ActorMatch

if TYPE_CHECKING:
    from insightface.app import FaceAnalysis

logger = logging.getLogger(__name__)


# ─── Configuration ───────────────────────────────────────────────────


# InsightFace's bundled "model packs" — each is a directory containing
# detection + embedding + (optionally) landmark / gender / age. We
# only use detection + embedding; the heavier components in
# buffalo_l are loaded but unused.
_DEFAULT_MODEL_PACK = "buffalo_s"

# Cosine-similarity threshold above which a face embedding is
# considered a match. Tuned empirically on the InsightFace test
# corpus; below 0.4 we'd see lookalikes match, above 0.7 we'd miss
# legitimate matches in poor lighting.
_DEFAULT_MATCH_THRESHOLD = 0.5

# Minimum face detection confidence — below this, even if we found
# something, the alignment is unreliable enough that the embedding
# is noisy. ArcFace can't recover from a badly-aligned crop.
_DEFAULT_DET_CONFIDENCE_MIN = 0.6

# Detector input size. 640 is InsightFace's default; smaller values
# (e.g. 320) speed inference at the cost of missing distant faces.
_DEFAULT_DET_SIZE = 640


@dataclass
class FaceConfig:
    """Runtime tunables for the face recognition pipeline."""

    model_pack: str = _DEFAULT_MODEL_PACK
    """One of ``buffalo_s`` (~10MB, fast) / ``buffalo_l`` (~280MB,
    slightly higher accuracy) / ``antelopev2`` (Asian-face-trained
    variant). Maps to InsightFace's model registry; the bundle is
    downloaded on first use to ``~/.insightface/models/``."""

    match_threshold: float = _DEFAULT_MATCH_THRESHOLD
    """Cosine-similarity threshold for a match. Range [-1, 1] but
    practical values are in [0.3, 0.7]."""

    det_confidence_min: float = _DEFAULT_DET_CONFIDENCE_MIN
    """Below this detection confidence, even successfully-extracted
    embeddings are too noisy to trust."""

    det_size: int = _DEFAULT_DET_SIZE

    providers: tuple[str, ...] = ("CPUExecutionProvider",)
    """ONNX execution providers in priority order. On the 4090 box
    use ('CUDAExecutionProvider', 'CPUExecutionProvider'); on Intel
    iGPU use ('OpenVINOExecutionProvider', 'CPUExecutionProvider').
    Default CPU-only — works everywhere onnxruntime works."""


# ─── Per-face result ─────────────────────────────────────────────────


@dataclass(frozen=True)
class DetectedFace:
    """One face found in a frame + its embedding + match result.

    Produced by :meth:`FaceRecognizer.detect_and_match`. The
    consumer (RTSPFrameBuffer) reads these, joins each with the
    YOLO person detection that contains it (via IoU), then emits an
    ActorMatch on the matched ones.
    """

    bbox: tuple[float, float, float, float]
    """``(x1, y1, x2, y2)`` in normalized [0, 1] image coords."""

    det_confidence: float
    embedding: np.ndarray  # shape (512,), L2-normalized
    matched_actor_id: str | None
    """``None`` when the embedding didn't match any enrolled actor
    above the threshold."""

    match_confidence: float
    """The cosine similarity that produced the match. 0.0 when
    matched_actor_id is None."""


# ─── The recognizer ──────────────────────────────────────────────────


class FaceRecognizer:
    """Async wrapper around InsightFace's :class:`FaceAnalysis`.

    Model load is deferred to first inference so the service comes
    up healthy before any models download. Inference is dispatched
    to the asyncio thread executor; same pattern as YOLODetector.
    """

    def __init__(self, config: FaceConfig | None = None) -> None:
        self._config = config or FaceConfig()
        self._app: FaceAnalysis | None = None
        self._load_lock = asyncio.Lock()
        # Guards the lazy load in _ensure_app, which runs in asyncio's
        # executor THREADS (not the loop) — a burst of frames fires
        # many concurrent first-calls, and without this each would load
        # its own heavy FaceAnalysis → memory blowup / OOM. A threading
        # lock (not the asyncio _load_lock, which only covers warmup)
        # is the right primitive for the executor path.
        self._app_lock = threading.Lock()

    async def warmup(self) -> None:
        """Eagerly load the model. Optional — first inference call
        loads lazily too. Call at startup to make the first real
        request fast."""
        async with self._load_lock:
            await asyncio.get_running_loop().run_in_executor(None, self._ensure_app)

    async def detect_and_match(
        self,
        bgr: np.ndarray,
        enrolled: dict[str, np.ndarray],
    ) -> tuple[DetectedFace, ...]:
        """Find every face in ``bgr``, embed each, match each against
        ``enrolled`` (``actor_id -> 512-d L2-normalized embedding``).
        Returns one :class:`DetectedFace` per face found, regardless of
        whether it matched — the caller can decide what to do with
        unmatched faces (in our pipeline: drop them, since unknown
        faces don't get IdentifiedEntities and don't get drawn)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._detect_and_match_sync, bgr, enrolled)

    # ─── internals (run in executor) ────────────────────────────────

    def _ensure_app(self) -> FaceAnalysis:
        if self._app is not None:
            return self._app
        # Double-checked locking: under a burst of frames, many
        # executor threads reach here at once. Serialize so the heavy
        # FaceAnalysis loads exactly once instead of N times (the
        # concurrent-load OOM that crashed the preprocessor).
        with self._app_lock:
            if self._app is not None:
                return self._app
            # Lazy import — keeps the preprocessor importable in
            # environments without onnxruntime / insightface (CI, unit
            # tests against synthetic backend).
            from insightface.app import FaceAnalysis  # type: ignore[import-not-found]

            logger.info(
                "face.loading model_pack=%s providers=%s",
                self._config.model_pack,
                self._config.providers,
            )
            app = FaceAnalysis(
                name=self._config.model_pack,
                providers=list(self._config.providers),
            )
            app.prepare(
                ctx_id=0,
                det_size=(self._config.det_size, self._config.det_size),
                det_thresh=self._config.det_confidence_min,
            )
            self._app = app
            logger.info("face.loaded")
            return app

    def _detect_and_match_sync(
        self,
        bgr: np.ndarray,
        enrolled: dict[str, np.ndarray],
    ) -> tuple[DetectedFace, ...]:
        app = self._ensure_app()
        # InsightFace expects BGR (matching OpenCV convention).
        faces = app.get(bgr)
        if not faces:
            # Diagnostic: no face found in this (head-crop) input. Lets
            # us tell "no detectable face" apart from "face found but
            # didn't match" when tuning a distant/wide camera.
            logger.info("face.no_face_in_input shape=%s", bgr.shape[:2])
            return ()

        h, w = bgr.shape[:2]
        results: list[DetectedFace] = []
        for f in faces:
            det_conf = float(f.det_score)
            if det_conf < self._config.det_confidence_min:
                continue

            # f.bbox is pixel x1,y1,x2,y2 in original-frame coords.
            x1, y1, x2, y2 = f.bbox.tolist()
            norm_bbox = (
                max(0.0, x1 / w),
                max(0.0, y1 / h),
                min(1.0, x2 / w),
                min(1.0, y2 / h),
            )

            # f.normed_embedding is the L2-normalized 512-d
            # embedding ready for cosine sim. Use that if present;
            # otherwise normalize f.embedding ourselves.
            emb = _normalized_embedding(f)
            actor_id, conf = _match(emb, enrolled, self._config.match_threshold)

            # Diagnostic: surface the best cosine even when it's below
            # the match threshold, so we can see near-misses on hard
            # cameras (distant / off-angle faces).
            if enrolled:
                best_cos = max(float(np.dot(emb, e)) for e in enrolled.values())
                logger.info(
                    "face.candidate det_score=%.2f best_cosine=%.3f matched=%s",
                    det_conf,
                    best_cos,
                    actor_id,
                )

            results.append(
                DetectedFace(
                    bbox=norm_bbox,
                    det_confidence=det_conf,
                    embedding=emb,
                    matched_actor_id=actor_id,
                    match_confidence=conf,
                )
            )
        return tuple(results)


# ─── helpers ─────────────────────────────────────────────────────────


def _normalized_embedding(face_obj) -> np.ndarray:
    """Pull the L2-normalized 512-d embedding out of an InsightFace
    Face object, normalizing on the fly if the bundled normalization
    isn't already done."""
    if hasattr(face_obj, "normed_embedding") and face_obj.normed_embedding is not None:
        return np.asarray(face_obj.normed_embedding, dtype=np.float32)
    raw = np.asarray(face_obj.embedding, dtype=np.float32)
    norm = np.linalg.norm(raw)
    if norm < 1e-8:
        # Degenerate. Return as-is rather than crashing; the match
        # step will reject it because cosine sim is undefined for
        # zero vectors.
        return raw
    return raw / norm


def _match(
    embedding: np.ndarray,
    enrolled: dict[str, np.ndarray],
    threshold: float,
) -> tuple[str | None, float]:
    """Best-match cosine similarity above threshold. ``embedding`` is
    assumed L2-normalized; enrolled embeddings same.

    Returns ``(actor_id, similarity)`` for the best match above
    threshold, or ``(None, 0.0)`` if nothing meets the bar.
    """
    if not enrolled:
        return None, 0.0
    best_id: str | None = None
    best_sim = -1.0
    for actor_id, enrolled_emb in enrolled.items():
        sim = float(np.dot(embedding, enrolled_emb))
        if sim > best_sim:
            best_sim = sim
            best_id = actor_id
    if best_sim >= threshold:
        return best_id, best_sim
    return None, 0.0


# ─── Spatial correlation: face-to-person association ────────────────


def associate_face_to_person(
    face_bbox: tuple[float, float, float, float],
    person_bboxes: list[tuple[str, tuple[float, float, float, float]]],
    *,
    min_overlap: float = 0.5,
) -> str | None:
    """Find which YOLO-detected person bbox a face belongs to.

    Returns the ``track_id`` of the best-matching person whose bbox
    contains a sufficient portion of the face, or ``None`` if no
    person bbox covers it. Used by RTSPFrameBuffer to inherit
    track_ids from the person detector onto face-derived
    ActorMatches, so the correlation pipeline can join them by
    track_id downstream.

    ``min_overlap`` is the fraction of the face's area that must
    fall inside the person's bbox for a match — 0.5 is conservative
    (handles partial occlusion + bbox tightness variance).
    """
    if not person_bboxes:
        return None
    face_area = max(
        1e-9,
        (face_bbox[2] - face_bbox[0]) * (face_bbox[3] - face_bbox[1]),
    )
    best_track_id: str | None = None
    best_overlap = 0.0
    for track_id, p_bbox in person_bboxes:
        ix1 = max(face_bbox[0], p_bbox[0])
        iy1 = max(face_bbox[1], p_bbox[1])
        ix2 = min(face_bbox[2], p_bbox[2])
        iy2 = min(face_bbox[3], p_bbox[3])
        if ix2 <= ix1 or iy2 <= iy1:
            continue
        intersection = (ix2 - ix1) * (iy2 - iy1)
        coverage = intersection / face_area
        if coverage > best_overlap:
            best_overlap = coverage
            best_track_id = track_id
    if best_overlap >= min_overlap:
        return best_track_id
    return None


# ─── Public helper for building ActorMatch from DetectedFace ────────


def detected_face_to_actor_match(
    face: DetectedFace,
    *,
    frame_ts: float,
    track_id: str | None,
) -> ActorMatch | None:
    """Convert a matched :class:`DetectedFace` into an
    :class:`ActorMatch` ready for the correlation pipeline. Returns
    ``None`` for unmatched faces (the contract has no concept of
    'face from no actor' — the VLM sees the raw frame, doesn't need
    a phantom ActorMatch)."""
    if face.matched_actor_id is None:
        return None
    return ActorMatch(
        actor_id=face.matched_actor_id,
        confidence=face.match_confidence,
        match_method="face_arcface",
        frame_ts=frame_ts,
        track_id=track_id,
    )


# ─── Decode helper ──────────────────────────────────────────────────


def jpeg_to_bgr(jpeg_bytes: bytes) -> np.ndarray | None:
    """JPEG -> BGR ndarray. Returns ``None`` on decode failure
    (single corrupt frame doesn't kill the face pipeline). Shared
    with the YOLO detector via importing the helper there would be
    cleanest, but duplicating the 5-line function keeps face.py
    independent of detection.py."""
    if not jpeg_bytes:
        return None
    try:
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except cv2.error:
        return None
    if img is None or img.size == 0:
        return None
    return img
