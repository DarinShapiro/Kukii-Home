"""Pet recognition (DINOv2) — is this our dog/cat, or a stray/neighbor's?

The third identity pipeline, after face (ArcFace) and body re-ID
(OSNet). YOLO already classifies ``dog`` / ``cat``; this pipeline
answers *which* dog/cat — distinguishing the household pet from a
neighbor's lookalike or a passing stray.

Why DINOv2 rather than a pet-specific model: there's no
widely-available, well-supported "pet re-ID" model the way OSNet
exists for people. DINOv2 is a strong general-purpose self-
supervised visual backbone — its image embedding captures fur
pattern, body shape, coloring well enough to separate individual
animals by cosine similarity, with zero task-specific training.
We take the CLS-token embedding of the cropped animal, L2-normalize
it, and match against enrolled pets (same shape as body-id).

The ``buffalo``/OSNet pattern repeats here deliberately: an ONNX
file consumed via onnxruntime (uniform inference stack), lazy load,
error-tolerant. The model is produced by
``scripts/dev/export_dinov2_onnx.py``.

Unlike face (which had to associate a face crop back to a person
bbox via IoU), the pet detection IS the animal — the ActorMatch
inherits the dog/cat detection's own track_id directly. No sub-
association step.
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


# DINOv2 patch size is 14; input must be a multiple of 14. 224 (=16
# patches) is the standard cheap input — plenty for distinguishing a
# household pet from a stray. Larger (518) buys fine-grained detail
# at much higher cost; not worth it for residential pet ID.
_DEFAULT_INPUT_SIZE = 224

_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# Cosine threshold for "same animal". DINOv2 embeddings are
# discriminative; 0.6 is a reasonable starting point (tune per the
# feedback loop). A neighbor's same-breed dog is the hard case — if
# false matches appear, raise toward 0.7.
_DEFAULT_MATCH_THRESHOLD = 0.6

# Kinds this pipeline recognizes. YOLO emits these COCO classes.
_PET_KINDS: frozenset[str] = frozenset({"dog", "cat"})


@dataclass
class PetConfig:
    """Runtime tunables for the pet recognition pipeline."""

    model_path: str
    """Filesystem path to a DINOv2 ONNX model. Produced by
    ``scripts/dev/export_dinov2_onnx.py``. Required — a missing file
    logs an error and makes every match a no-match (rather than
    crashing the preprocessor)."""

    match_threshold: float = _DEFAULT_MATCH_THRESHOLD
    input_size: int = _DEFAULT_INPUT_SIZE
    providers: tuple[str, ...] = ("CPUExecutionProvider",)


# ─── Per-animal result ──────────────────────────────────────────────


@dataclass(frozen=True)
class DetectedPet:
    """One animal crop + its embedding + match result.

    Produced by :meth:`PetRecognizer.identify_pets`. The pipeline
    adapter emits an :class:`ActorMatch` for every record with a
    non-None ``matched_actor_id``.
    """

    track_id: str
    """Inherited directly from the YOLO dog/cat detection — the join
    key for downstream correlation. (No IoU sub-association: the
    detection IS the animal.)"""

    kind: str  # "dog" | "cat"
    embedding: np.ndarray  # L2-normalized DINOv2 CLS embedding
    matched_actor_id: str | None
    match_confidence: float


# ─── The recognizer ──────────────────────────────────────────────────


class PetRecognizer:
    """Async wrapper around a DINOv2 ONNX session.

    Lazy load, error-tolerant — identical lifecycle to
    :class:`BodyIdRecognizer`. The preprocessor stays up even if the
    ONNX file is missing/corrupt (failed loads log + return empty).
    """

    def __init__(self, config: PetConfig) -> None:
        self._config = config
        self._session: ort.InferenceSession | None = None
        self._load_failed = False
        self._load_lock = asyncio.Lock()

    async def warmup(self) -> None:
        async with self._load_lock:
            await asyncio.get_running_loop().run_in_executor(None, self._ensure_session)

    async def identify_pets(
        self,
        bgr: np.ndarray,
        pets: list[tuple[str, str, tuple[float, float, float, float]]],
        enrolled: dict[str, np.ndarray],
    ) -> tuple[DetectedPet, ...]:
        """Crop, embed, and match each animal in ``pets``.

        ``pets`` is a list of ``(track_id, kind, normalized_bbox)``
        from YOLO (kind ∈ {dog, cat}). ``enrolled`` is
        ``actor_id -> L2-normalized DINOv2 embedding``. Returns one
        :class:`DetectedPet` per animal, matched or not.
        """
        if not pets:
            return ()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._identify_sync, bgr, pets, enrolled)

    # ─── internals (run in executor) ────────────────────────────────

    def _ensure_session(self) -> ort.InferenceSession | None:
        if self._session is not None or self._load_failed:
            return self._session
        try:
            import onnxruntime as ort  # type: ignore[import-not-found]
        except ImportError:
            logger.error("pet.onnxruntime_missing — install onnxruntime to use pet recognition")
            self._load_failed = True
            return None
        try:
            logger.info(
                "pet.loading model=%s providers=%s",
                self._config.model_path,
                self._config.providers,
            )
            self._session = ort.InferenceSession(
                self._config.model_path,
                providers=list(self._config.providers),
            )
            logger.info("pet.loaded")
        except Exception as e:
            logger.error("pet.load_failed model=%s error=%s", self._config.model_path, e)
            self._load_failed = True
            return None
        return self._session

    def _identify_sync(
        self,
        bgr: np.ndarray,
        pets: list[tuple[str, str, tuple[float, float, float, float]]],
        enrolled: dict[str, np.ndarray],
    ) -> tuple[DetectedPet, ...]:
        session = self._ensure_session()
        if session is None:
            return ()

        h, w = bgr.shape[:2]
        crops: list[np.ndarray] = []
        kept: list[tuple[str, str]] = []  # (track_id, kind) per crop
        for track_id, kind, bbox in pets:
            crop = _crop(bgr, bbox, w, h)
            if crop is None:
                continue
            crops.append(_preprocess(crop, self._config.input_size))
            kept.append((track_id, kind))
        if not crops:
            return ()

        batch = np.stack(crops, axis=0).astype(np.float32)
        input_name = session.get_inputs()[0].name
        try:
            outputs = session.run(None, {input_name: batch})
        except Exception as e:
            logger.warning("pet.inference_failed error=%s", e)
            return ()
        normed = _l2_normalize_rows(outputs[0])

        results: list[DetectedPet] = []
        for (track_id, kind), emb in zip(kept, normed, strict=True):
            actor_id, conf = _match(emb, enrolled, self._config.match_threshold)
            results.append(
                DetectedPet(
                    track_id=track_id,
                    kind=kind,
                    embedding=emb,
                    matched_actor_id=actor_id,
                    match_confidence=conf,
                )
            )
        return tuple(results)


# ─── crop + preprocess helpers ──────────────────────────────────────


def _crop(
    bgr: np.ndarray,
    bbox: tuple[float, float, float, float],
    w: int,
    h: int,
) -> np.ndarray | None:
    x1 = max(0, int(bbox[0] * w))
    y1 = max(0, int(bbox[1] * h))
    x2 = min(w, int(bbox[2] * w))
    y2 = min(h, int(bbox[3] * h))
    if x2 <= x1 or y2 <= y1:
        return None
    return bgr[y1:y2, x1:x2]


def _preprocess(crop_bgr: np.ndarray, size: int) -> np.ndarray:
    """DINOv2 input: square resize -> BGR2RGB -> [0,1] -> ImageNet
    normalize -> CHW float32."""
    resized = cv2.resize(crop_bgr, (size, size), interpolation=cv2.INTER_CUBIC)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    normed = (rgb.astype(np.float32) / 255.0 - _IMAGENET_MEAN) / _IMAGENET_STD
    return np.transpose(normed, (2, 0, 1))


def _l2_normalize_rows(arr: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    safe = np.where(norms < 1e-8, 1.0, norms)
    return arr / safe


def _match(
    embedding: np.ndarray,
    enrolled: dict[str, np.ndarray],
    threshold: float,
) -> tuple[str | None, float]:
    """Best-match cosine similarity above threshold. Both sides
    assumed L2-normalized. Kept separate from face/body so pet
    threshold tunes independently."""
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


def detected_pet_to_actor_match(pet: DetectedPet, *, frame_ts: float):
    """Convert a matched :class:`DetectedPet` into an
    :class:`ActorMatch` (``match_method='pet_dinov2'``). Returns
    ``None`` for unmatched."""
    from kukiihome_shared.preprocessor import ActorMatch

    if pet.matched_actor_id is None:
        return None
    return ActorMatch(
        actor_id=pet.matched_actor_id,
        confidence=pet.match_confidence,
        match_method="pet_dinov2",
        frame_ts=frame_ts,
        track_id=pet.track_id,
    )
