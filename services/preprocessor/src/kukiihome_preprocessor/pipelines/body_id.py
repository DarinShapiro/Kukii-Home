"""Body re-identification (OSNet) — the fallback when face isn't visible.

ArcFace can't help when the subject is turned away, partially
occluded, or too far for the face detector. Body re-ID matches a
person by their full-body appearance (clothing, build, gait posture
in the frame) instead. Trained on MSMT17 / Market-1501 / etc.,
OSNet-x1_0 produces a 512-d L2-normalized embedding that's robust
to camera angle and lighting changes within a short time window
(minutes to hours, less so across days as clothing changes).

Why ONNX over torch: same reason face uses InsightFace's ONNX
runtime. Keeps the preprocessor's inference stack uniform
(onnxruntime everywhere), and the OSNet ONNX file is ~5MB so the
cost is just one shipping artifact. The model file is produced by
``scripts/dev/export_osnet_onnx.py`` (separate commit) — at runtime
this module only consumes the .onnx, not the training framework.

Pipeline shape:

* Input: BGR frame + list of (track_id, person_bbox) from YOLO
* For each person: crop the bbox, resize to OSNet's expected 256x128,
  HWC->CHW + normalize, run inference, L2-normalize the 512-d output
* Match each embedding against the enrolled corpus (cosine sim,
  threshold 0.6 default — OSNet embeddings are looser than ArcFace's
  so the threshold's lower)
* Returns one :class:`DetectedBody` per matched person; unmatched
  drop out (caller surfaces only confident matches)

Cost gating: the body_id pipeline declares
``depends_on=("face_arcface",)`` + ``skip_when_upstream_matched_above=0.85``
so the router skips OSNet inference for any track_id face already
nailed. Without that, we'd pay ~50-80ms per person every frame
twice — once for face, once for body. The chain makes face-confident
hits free.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    import onnxruntime as ort

logger = logging.getLogger(__name__)


# ─── Configuration ───────────────────────────────────────────────────


# OSNet's expected input. The torchreid export ships at 256x128 (HxW)
# which is the standard ReID crop ratio (taller than wide; people).
_DEFAULT_INPUT_HEIGHT = 256
_DEFAULT_INPUT_WIDTH = 128

# ImageNet mean/std — OSNet was trained with the standard
# torchvision preprocessing pipeline.
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# Cosine threshold for "same person". OSNet embeddings live in a
# looser similarity space than ArcFace — 0.6 cosine is roughly the
# OSNet sweet spot for cross-view matching on MSMT17 evaluation.
# The feedback loop can retune per-camera later via KnobAdjustment.
_DEFAULT_MATCH_THRESHOLD = 0.6


@dataclass
class BodyIdConfig:
    """Runtime tunables for the body re-ID pipeline."""

    model_path: str
    """Filesystem path to an OSNet ONNX model. Produced by
    ``scripts/dev/export_osnet_onnx.py``. Required — if the file is
    absent at runtime the recognizer logs an error and never matches
    anything (rather than crashing the preprocessor)."""

    match_threshold: float = _DEFAULT_MATCH_THRESHOLD
    input_height: int = _DEFAULT_INPUT_HEIGHT
    input_width: int = _DEFAULT_INPUT_WIDTH

    providers: tuple[str, ...] = ("CPUExecutionProvider",)
    """ONNX execution providers in priority order. CPU default;
    CUDAExecutionProvider on the 4090; OpenVINOExecutionProvider on
    Intel iGPU. Independent of face's provider choice — body_id is
    cheaper than face per-person but runs on more candidates."""


# ─── Per-person result ──────────────────────────────────────────────


@dataclass(frozen=True)
class DetectedBody:
    """One person crop + its embedding + match result.

    Produced by :meth:`BodyIdRecognizer.identify_persons`. The
    pipeline adapter (:class:`BodyIdPipeline`) emits an
    :class:`ActorMatch` for every record with a non-None
    ``matched_actor_id``.
    """

    track_id: str
    """Inherited from the YOLO person detection — the join key for
    downstream correlation. Always set; the recognizer skips
    untracked person dets (no way to surface them downstream)."""

    embedding: np.ndarray  # (512,), L2-normalized
    matched_actor_id: str | None
    match_confidence: float
    """Cosine sim above threshold; 0.0 when unmatched."""


# ─── The recognizer ──────────────────────────────────────────────────


class BodyIdRecognizer:
    """Async wrapper around an OSNet ONNX session.

    Model load is deferred to first inference — the preprocessor
    starts healthy even if the ONNX file is missing or corrupt
    (failed loads log + cause subsequent calls to return empty).
    Same pattern as :class:`FaceRecognizer`.
    """

    def __init__(self, config: BodyIdConfig) -> None:
        self._config = config
        self._session: ort.InferenceSession | None = None
        self._load_failed = False
        self._load_lock = asyncio.Lock()

    async def warmup(self) -> None:
        """Eagerly load the model. Optional — first inference call
        loads lazily too. Call at startup to make the first real
        request fast."""
        async with self._load_lock:
            await asyncio.get_running_loop().run_in_executor(None, self._ensure_session)

    async def identify_persons(
        self,
        bgr: np.ndarray,
        persons: list[tuple[str, tuple[float, float, float, float]]],
        enrolled: dict[str, np.ndarray],
    ) -> tuple[DetectedBody, ...]:
        """Crop, embed, and match each person in ``persons``.

        ``persons`` is a list of ``(track_id, normalized_bbox)``
        pairs from YOLO. ``enrolled`` is ``actor_id -> 512-d
        L2-normalized embedding``. Returns one :class:`DetectedBody`
        per person — matched or not — so the caller can decide what
        to surface.
        """
        if not persons:
            return ()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._identify_sync, bgr, persons, enrolled)

    # ─── internals (run in executor) ────────────────────────────────

    def _ensure_session(self) -> ort.InferenceSession | None:
        if self._session is not None or self._load_failed:
            return self._session
        # Lazy import — keeps the preprocessor importable when
        # body_id isn't wired (ONNX runtime cost only when used).
        try:
            import onnxruntime as ort  # type: ignore[import-not-found]
        except ImportError:
            logger.error("body_id.onnxruntime_missing — install onnxruntime to use body_id")
            self._load_failed = True
            return None

        try:
            logger.info(
                "body_id.loading model=%s providers=%s",
                self._config.model_path,
                self._config.providers,
            )
            self._session = ort.InferenceSession(
                self._config.model_path,
                providers=list(self._config.providers),
            )
            logger.info("body_id.loaded")
        except Exception as e:
            logger.error(
                "body_id.load_failed model=%s error=%s",
                self._config.model_path,
                e,
            )
            self._load_failed = True
            return None
        return self._session

    def _identify_sync(
        self,
        bgr: np.ndarray,
        persons: list[tuple[str, tuple[float, float, float, float]]],
        enrolled: dict[str, np.ndarray],
    ) -> tuple[DetectedBody, ...]:
        session = self._ensure_session()
        if session is None:
            return ()

        h, w = bgr.shape[:2]
        crops: list[np.ndarray] = []
        kept: list[str] = []  # track_ids matching the crop order
        for track_id, bbox in persons:
            crop = _crop_person(bgr, bbox, w, h)
            if crop is None:
                continue
            crops.append(_preprocess(crop, self._config.input_height, self._config.input_width))
            kept.append(track_id)
        if not crops:
            return ()

        # Batch the crops — OSNet handles arbitrary N along axis 0.
        batch = np.stack(crops, axis=0).astype(np.float32)
        input_name = session.get_inputs()[0].name
        try:
            outputs = session.run(None, {input_name: batch})
        except Exception as e:
            logger.warning("body_id.inference_failed error=%s", e)
            return ()
        raw_embeddings = outputs[0]  # shape (N, 512)
        normed = _l2_normalize_rows(raw_embeddings)

        results: list[DetectedBody] = []
        for track_id, emb in zip(kept, normed, strict=True):
            actor_id, conf = _match(emb, enrolled, self._config.match_threshold)
            results.append(
                DetectedBody(
                    track_id=track_id,
                    embedding=emb,
                    matched_actor_id=actor_id,
                    match_confidence=conf,
                )
            )
        return tuple(results)


# ─── crop + preprocess helpers ──────────────────────────────────────


def _crop_person(
    bgr: np.ndarray,
    bbox: tuple[float, float, float, float],
    w: int,
    h: int,
) -> np.ndarray | None:
    """Pixel-space crop from a normalized bbox. Returns ``None`` for
    degenerate (zero-area) bboxes — the inference batch drops them
    rather than crashing on a 0x0 slice."""
    x1 = max(0, int(bbox[0] * w))
    y1 = max(0, int(bbox[1] * h))
    x2 = min(w, int(bbox[2] * w))
    y2 = min(h, int(bbox[3] * h))
    if x2 <= x1 or y2 <= y1:
        return None
    return bgr[y1:y2, x1:x2]


def _preprocess(crop_bgr: np.ndarray, height: int, width: int) -> np.ndarray:
    """OSNet's exact input preprocessing: resize -> BGR2RGB ->
    [0,1] -> ImageNet normalize -> CHW float32."""
    resized = cv2.resize(crop_bgr, (width, height), interpolation=cv2.INTER_CUBIC)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    normed = (rgb.astype(np.float32) / 255.0 - _IMAGENET_MEAN) / _IMAGENET_STD
    # HWC -> CHW
    return np.transpose(normed, (2, 0, 1))


def _l2_normalize_rows(arr: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalize. Treats zero-norm rows (degenerate
    crops) as zero vectors — the match step will reject them since
    cosine sim is 0 there."""
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    safe = np.where(norms < 1e-8, 1.0, norms)
    return arr / safe


def _match(
    embedding: np.ndarray,
    enrolled: dict[str, np.ndarray],
    threshold: float,
) -> tuple[str | None, float]:
    """Best-match cosine similarity above threshold.

    Same shape as :func:`face._match` but kept separate so the body
    threshold tunes independently. ``embedding`` and ``enrolled``
    values are both assumed L2-normalized.
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


# ─── Public helper for ActorMatch building ──────────────────────────


def detected_body_to_actor_match(body: DetectedBody, *, frame_ts: float):
    """Convert a matched :class:`DetectedBody` into an
    :class:`ActorMatch`. Returns ``None`` for unmatched. The
    body_id pipeline produces match_method='body_id_osnet'."""
    from kukiihome_shared.preprocessor import ActorMatch

    if body.matched_actor_id is None:
        return None
    return ActorMatch(
        actor_id=body.matched_actor_id,
        confidence=body.match_confidence,
        match_method="body_id_osnet",
        frame_ts=frame_ts,
        track_id=body.track_id,
    )
