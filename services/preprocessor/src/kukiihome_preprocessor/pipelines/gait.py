"""Gait recognition (OpenGait GaitBase) — identity from walking dynamics.

Gait is the durable, distance-robust, clothing- and face-independent
biometric: it keys on *how* someone walks, so it survives the exact
conditions that defeat face (back-of-head, distance, steep top-down) and
body-ID (outfit change). On cameras where face routinely fails (the pool
cam), an enrolled gait template can be a real long-term anchor.

Unlike face / body-ID (one embedding per frame), gait is a property of a
*sequence* — you compare CLIPS, not frames. So this recognizer consumes a
per-track frame sequence and emits ONE embedding per track:

    per frame: crop to the person bbox -> YOLO-seg -> largest-person mask
               -> OpenGait-style 64x44 centered binary silhouette
    stack the clip -> [S, 64, 44] -> GaitBase ONNX (temporal-pools the
    whole clip) -> one 4096-d L2-normalized gait embedding -> cosine match

A coherent gait cycle needs a reasonably dense walk, so tracks with fewer
than ``min_frames`` usable silhouettes produce no match (a glance-by isn't
gait). The model file is produced by ``scripts/dev/export_gait_onnx.py``
and the full offline chain is proven in ``scripts/dev/gait_probe.py``;
at runtime this module only consumes the ONNX + the YOLO-seg weights.

Lazy model load + fail-safe: a missing model / seg weights logs an error
and yields zero matches rather than crashing the preprocessor — same
pattern as :class:`~...body_id.BodyIdRecognizer`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from kukiihome_preprocessor.pipelines.face import jpeg_to_bgr

if TYPE_CHECKING:
    import onnxruntime as ort

    from kukiihome_preprocessor.pipelines.identity.router import TrackSequence

logger = logging.getLogger(__name__)


# OpenGait silhouette geometry (GaitBase trained on 64x44 binaries).
_GAIT_H = 64
_GAIT_W = 44

# A clip shorter than this has no coherent stride — skip rather than
# emit a low-quality gait embedding. ~15 frames of a dense sub-stream
# walk ≈ most of one gait cycle.
_DEFAULT_MIN_FRAMES = 15

# Gait cosines live lower than face/body: genuine pairs cluster ~0.4-0.7,
# imposters ~0.1-0.3 on GaitBase. 0.35 is a conservative starting point;
# tune per camera via KnobAdjustment once FP/FN data lands.
_DEFAULT_MATCH_THRESHOLD = 0.35

# Padding around the person bbox before segmentation, so a tight YOLO box
# doesn't clip limbs out of the silhouette.
_CROP_PAD = 0.08


@dataclass
class GaitConfig:
    """Runtime tunables for the gait pipeline."""

    model_path: str
    """Filesystem path to the GaitBase ONNX (input ``sils`` [N,S,64,44],
    output ``embedding`` [N,4096]). Produced by
    ``scripts/dev/export_gait_onnx.py``."""

    seg_weights: str = "yolo11x-seg.pt"
    """Ultralytics segmentation weights for silhouette extraction. The
    -seg variant of the same YOLO family detection uses."""

    match_threshold: float = _DEFAULT_MATCH_THRESHOLD
    min_frames: int = _DEFAULT_MIN_FRAMES

    seg_device: str | None = None
    """``"cuda:0"`` / ``"cpu"`` / None (ultralytics auto-pick) for the
    segmentation model."""

    providers: tuple[str, ...] = ("CPUExecutionProvider",)
    """ONNX execution providers for the GaitBase session."""


@dataclass(frozen=True)
class DetectedGait:
    """One track's gait embedding + match result.

    Produced by :meth:`GaitRecognizer.identify_tracks`. The pipeline
    adapter emits an :class:`ActorMatch` for every record with a
    non-None ``matched_actor_id``.
    """

    track_id: str
    embedding: np.ndarray  # (4096,), L2-normalized
    matched_actor_id: str | None
    match_confidence: float
    frame_ts: float
    """Representative frame for the clip — the last frame of the track's
    sequence (the freshest observation)."""

    n_silhouettes: int


class GaitRecognizer:
    """Async wrapper around YOLO-seg + a GaitBase ONNX session.

    Both models load lazily on first inference and fail safe: a missing
    file logs + causes subsequent calls to return empty, so the
    preprocessor stays healthy even when gait is misconfigured.
    """

    def __init__(self, config: GaitConfig) -> None:
        self._config = config
        self._session: ort.InferenceSession | None = None
        self._seg: object | None = None  # ultralytics YOLO
        self._load_failed = False
        self._load_lock = asyncio.Lock()

    async def warmup(self) -> None:
        async with self._load_lock:
            await asyncio.get_running_loop().run_in_executor(None, self._ensure_models)

    async def identify_tracks(
        self,
        tracks: dict[str, TrackSequence],
        enrolled: dict[str, np.ndarray],
    ) -> tuple[DetectedGait, ...]:
        """Embed + match every track's gait from its frame sequence.

        ``tracks`` maps ``track_id -> ((BufferedFrame, bbox), ...)``
        chronological. ``enrolled`` is ``actor_id -> 4096-d gait
        template``. Returns one :class:`DetectedGait` per track that
        yielded enough silhouettes; tracks below ``min_frames`` are
        dropped silently.
        """
        if not tracks:
            return ()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._identify_sync, tracks, enrolled)

    # ─── internals (run in executor) ────────────────────────────────

    def _ensure_models(self) -> bool:
        """Load both models once. Returns True when ready, False if a
        load failed (caller then returns no matches)."""
        if self._load_failed:
            return False
        if self._session is not None and self._seg is not None:
            return True
        try:
            import onnxruntime as ort  # type: ignore[import-not-found]
            from ultralytics import YOLO  # type: ignore[import-not-found]
        except ImportError as e:
            logger.error("gait.deps_missing — install onnxruntime + ultralytics: %s", e)
            self._load_failed = True
            return False
        try:
            logger.info(
                "gait.loading gait_model=%s seg=%s providers=%s",
                self._config.model_path,
                self._config.seg_weights,
                self._config.providers,
            )
            self._session = ort.InferenceSession(
                self._config.model_path,
                providers=list(self._config.providers),
            )
            self._seg = YOLO(self._config.seg_weights)
            logger.info("gait.loaded")
        except Exception as e:
            logger.error("gait.load_failed error=%s", e)
            self._load_failed = True
            return False
        return True

    def _identify_sync(
        self,
        tracks: dict[str, TrackSequence],
        enrolled: dict[str, np.ndarray],
    ) -> tuple[DetectedGait, ...]:
        if not self._ensure_models():
            return ()
        results: list[DetectedGait] = []
        for track_id, sequence in tracks.items():
            if not sequence:
                continue
            sils = self._clip_silhouettes(sequence)
            if len(sils) < self._config.min_frames:
                logger.debug(
                    "gait.short_clip track=%s silhouettes=%d < %d",
                    track_id,
                    len(sils),
                    self._config.min_frames,
                )
                continue
            emb = self._embed(sils)
            if emb is None:
                continue
            actor_id, conf = _match(emb, enrolled, self._config.match_threshold)
            # Representative ts: the last (freshest) frame in the clip.
            frame_ts = sequence[-1][0].ts
            results.append(
                DetectedGait(
                    track_id=track_id,
                    embedding=emb,
                    matched_actor_id=actor_id,
                    match_confidence=conf,
                    frame_ts=frame_ts,
                    n_silhouettes=len(sils),
                )
            )
        return tuple(results)

    def _clip_silhouettes(self, sequence: TrackSequence) -> np.ndarray:
        """Ordered ``[S, 64, 44]`` uint8 silhouette stack for one track.

        Crops each frame to the (padded) person bbox, segments the crop,
        keeps the largest person mask, and centers it OpenGait-style.
        Frames where segmentation finds no person are dropped.
        """
        assert self._seg is not None
        sils: list[np.ndarray] = []
        for frame, bbox in sequence:
            bgr = jpeg_to_bgr(frame.jpeg_bytes)
            if bgr is None:
                continue
            crop = _crop_padded(bgr, bbox, _CROP_PAD)
            if crop is None:
                continue
            mask = _largest_person_mask(self._seg, crop)
            if mask is None:
                continue
            sils.append(_center_silhouette(mask))
        return (
            np.asarray(sils, dtype=np.uint8) if sils else np.empty((0, _GAIT_H, _GAIT_W), np.uint8)
        )

    def _embed(self, sils: np.ndarray) -> np.ndarray | None:
        """``[S,64,44]`` uint8 -> ``[4096]`` L2-normalized gait embedding."""
        assert self._session is not None
        x = (sils.astype(np.float32) / 255.0)[None, ...]  # [1, S, 64, 44]
        try:
            emb = self._session.run(None, {"sils": x})[0][0]  # [4096]
        except Exception as e:
            logger.warning("gait.inference_failed error=%s", e)
            return None
        n = float(np.linalg.norm(emb))
        return emb / n if n > 1e-8 else emb


# ─── silhouette + crop helpers ──────────────────────────────────────


def _crop_padded(
    bgr: np.ndarray,
    bbox: tuple[float, float, float, float],
    pad: float,
) -> np.ndarray | None:
    """Pixel-space crop from a normalized bbox, padded so a tight box
    doesn't clip limbs. Returns None for degenerate regions."""
    h, w = bgr.shape[:2]
    x1 = max(0, int((bbox[0] - pad) * w))
    y1 = max(0, int((bbox[1] - pad) * h))
    x2 = min(w, int((bbox[2] + pad) * w))
    y2 = min(h, int((bbox[3] + pad) * h))
    if x2 <= x1 or y2 <= y1:
        return None
    return bgr[y1:y2, x1:x2]


def _largest_person_mask(seg: object, bgr: np.ndarray) -> np.ndarray | None:
    """Run YOLO-seg, return the largest person instance mask (uint8 0/255)
    at the input resolution, or None if no person was segmented."""
    r = seg.predict(bgr, imgsz=640, conf=0.5, verbose=False, device=None)[0]  # type: ignore[attr-defined]
    if r.masks is None or len(r.masks) == 0:
        return None
    h, w = bgr.shape[:2]
    best_area, best_mask = 0.0, None
    for i, box in enumerate(r.boxes):
        if int(box.cls) != 0:  # class 0 = person
            continue
        m = r.masks.data[i].cpu().numpy()
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
        area = float(m.sum())
        if area > best_area:
            best_area, best_mask = area, m
    if best_mask is None:
        return None
    return (best_mask > 0).astype(np.uint8) * 255


def _center_silhouette(mask: np.ndarray) -> np.ndarray:
    """OpenGait-style normalize: crop to the silhouette's vertical extent,
    scale to height 64, then center horizontally in a 44-wide frame by the
    mask's centroid. Ported from ``scripts/dev/extract_silhouettes.py``."""
    ys, _ = np.where(mask > 0)
    if len(ys) == 0:
        return np.zeros((_GAIT_H, _GAIT_W), np.uint8)
    top, bot = ys.min(), ys.max()
    cropped = mask[top : bot + 1, :]
    scale = _GAIT_H / cropped.shape[0]
    new_w = max(1, int(cropped.shape[1] * scale))
    resized = cv2.resize(cropped, (new_w, _GAIT_H), interpolation=cv2.INTER_NEAREST)
    xs2 = np.where(resized > 0)[1]
    cx = int(xs2.mean()) if len(xs2) else new_w // 2
    canvas = np.zeros((_GAIT_H, _GAIT_W), np.uint8)
    left = _GAIT_W // 2 - cx
    for x in range(resized.shape[1]):
        tx = x + left
        if 0 <= tx < _GAIT_W:
            canvas[:, tx] = np.maximum(canvas[:, tx], resized[:, x])
    return canvas


def _match(
    embedding: np.ndarray,
    enrolled: dict[str, np.ndarray],
    threshold: float,
) -> tuple[str | None, float]:
    """Best-match cosine similarity above threshold. Both sides assumed
    L2-normalized. Kept local so the gait threshold tunes independently."""
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


def detected_gait_to_actor_match(gait: DetectedGait):
    """Convert a matched :class:`DetectedGait` into an :class:`ActorMatch`
    (``match_method="gait_opengait"``). Returns None for unmatched."""
    from kukiihome_shared.preprocessor import ActorMatch

    if gait.matched_actor_id is None:
        return None
    return ActorMatch(
        actor_id=gait.matched_actor_id,
        confidence=gait.match_confidence,
        match_method="gait_opengait",
        frame_ts=gait.frame_ts,
        track_id=gait.track_id,
    )
