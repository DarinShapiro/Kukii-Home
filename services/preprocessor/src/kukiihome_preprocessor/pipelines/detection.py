"""YOLO11x object detection — first real model in the pipeline.

Wraps an Ultralytics YOLO model behind a clean async interface that
returns a tuple of :class:`DetectionTag` per input frame. Plugs into
:class:`RTSPFrameBuffer.get_window`'s enrichment path so the
``detections`` field of a returned :class:`FrameWindow` is populated
from real frames.

Phase 10.3 scope:

* YOLO11x (or YOLO11n in dev for speed) as the detector
* Batched inference across the frames in one ``get_window`` call
* COCO class names mapped to our DetectionTag.kind strings
* CUDA used automatically when torch sees a GPU (~30ms/frame on
  4090); CPU works as a fallback (~1-2s/frame)
* JPEG-encoded keyframes decoded on-the-fly for inference; the
  results go back into the FrameWindow without modifying the
  rolling buffer

Out of scope (later phases):

* Face / pet / plate enrichment — these layer on top once a person
  / dog / vehicle detection lands. Phase 10.4+ branches on
  DetectionTag.kind to dispatch to those pipelines.
* Multi-camera batching — currently we batch within one camera's
  window; cross-camera batching is a Phase 10.6 optimization.
* Track association — YOLO's built-in track mode could populate
  DetectionTag.track_id; we leave that for Phase 10.3.1.

The ultralytics import is lazy — modules that don't run inference
(synthetic-mode tests, contract-only callers) shouldn't pay the
~500MB torch + ultralytics import cost.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import cv2
import numpy as np
from kukiihome_shared.preprocessor import DetectionTag

if TYPE_CHECKING:
    from ultralytics import YOLO

logger = logging.getLogger(__name__)


# Backends the detector can dispatch through. Ranked by typical
# speed on the matching hardware:
#
#   pytorch  - default; works everywhere ultralytics works.
#              Slow on Intel CPUs (~1-2s/frame for yolo11x).
#   openvino - Intel-native runtime. 2-3x faster than pytorch on
#              Intel CPUs; 5-10x faster on Intel iGPUs (Iris Plus,
#              Arc, etc.). Requires a pre-exported model directory
#              (see scripts/dev/export_yolo_openvino.py).
#   tensorrt - NVIDIA-only, reserved for the inference box. Not
#              wired here yet; ultralytics supports the export.
InferenceBackend = Literal["pytorch", "openvino"]


# Default model. yolo11x is the production target (~109 MB weights,
# ~30ms on a 4090, ~500-1000ms on CPU, much higher mAP than nano).
# yolo11n (~5 MB) hallucinates "car" on textured surfaces like pool
# water — verified empirically. Use yolo11n explicitly only when
# you've accepted that tradeoff (unit tests do, for speed).
_DEFAULT_WEIGHTS = "yolo11x.pt"

# COCO class names that the dispatcher actually cares about. Anything
# YOLO detects with a class outside this set gets a generic
# ``unknown`` kind — keeps the tag_set surface small + predictable
# downstream while the long-tail of COCO classes (toaster, kite, …)
# doesn't pollute the alert pipeline.
_INTERESTING_COCO_CLASSES: dict[str, str] = {
    "person": "person",
    "car": "vehicle",
    "motorcycle": "vehicle",
    "bus": "vehicle",
    "truck": "vehicle",
    "bicycle": "vehicle",
    "dog": "dog",
    "cat": "cat",
    "bird": "animal",
    "horse": "animal",
    "sheep": "animal",
    "cow": "animal",
    "bear": "animal",
    "deer": "animal",
}

# Default confidence floor. 0.5 chosen after seeing yolo11n produce
# false-positive "car" detections at 0.59 on a pool-surface frame —
# even moderate-confidence YOLO outputs in out-of-distribution scenes
# are unreliable. The preprocessor's KnobAdjustment surface
# (POST /tune yolo.confidence_min) can override this at runtime
# once the feedback-loop subsystem lands.
_DEFAULT_CONFIDENCE_MIN = 0.5

# Per-class confidence floors. Animals read much lower than people on
# steep/distant cameras (a top-down dog scores ~0.34 where a standing
# person reads 0.74-0.86 on the same frame), so a single 0.5 floor tuned
# for people makes pets *invisible* — the motion gate drops the dog before
# recognition ever sees a crop, and S16 (dog in yard / escaped pet) fails
# outright. These floors are applied per detection AFTER inference, so the
# model still runs at the lowest floor and we filter up per class.
# Mapped-kind keyed (see _INTERESTING_COCO_CLASSES values), not COCO names.
_DEFAULT_PER_CLASS_CONFIDENCE: dict[str, float] = {
    "dog": 0.25,
    "cat": 0.25,
    "animal": 0.25,
}


@dataclass
class DetectionConfig:
    """Runtime tunables for the detector."""

    weights: str = _DEFAULT_WEIGHTS
    """Either a model name (e.g. ``yolo11n.pt``, downloaded on first
    use to the ultralytics cache), a path to a .pt file on disk, OR a
    path to an OpenVINO IR directory (e.g. ``yolo11x_openvino_model/``)
    when ``backend == "openvino"``."""

    confidence_min: float = _DEFAULT_CONFIDENCE_MIN
    """Default confidence floor for any class without a per-class override
    in :attr:`per_class_confidence`. Also the floor handed to the model at
    inference time (the minimum across all classes), so a lower per-class
    floor still receives candidate boxes to filter."""

    per_class_confidence: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_PER_CLASS_CONFIDENCE)
    )
    """Mapped-kind → confidence floor overrides (e.g. ``{"dog": 0.25}``).
    Keyed by the DetectionTag.kind we emit, not COCO names. A class absent
    here uses :attr:`confidence_min`."""

    iou_min: float = 0.45
    """NMS IoU threshold. 0.45 is YOLO's default."""

    image_size: int = 1280
    """Square input size YOLO letterboxes to before detecting — the
    detail floor. 640 (COCO standard) on a 4K feed is a ~6x downsample
    that loses small/distant objects; 1280 recovers most of it. The
    full-res answer is tiled detection (deferred — needs track-id merge
    validation). See config.PreprocessorConfig.detection_image_size."""

    device: str | None = None
    """For pytorch backend: ``"cuda:0"`` / ``"cpu"`` / ``None`` (auto).
    For openvino backend: ``"GPU"`` / ``"GPU.0"`` / ``"CPU"`` /
    ``"NPU"`` / ``"AUTO"`` — OpenVINO device names, which
    :meth:`YOLODetector._resolve_device` maps onto ultralytics'
    ``"intel:<dev>"`` form (a bare ``"GPU"`` would otherwise hit
    ultralytics' CUDA parser and raise). ``"intel:gpu"`` etc. are also
    accepted verbatim. ``None`` / ``"AUTO"`` default to the iGPU."""

    backend: InferenceBackend = "pytorch"
    """Which inference runtime to use. See :data:`InferenceBackend`
    for the trade-offs. Default is pytorch because it works
    everywhere; switch to openvino on Intel hardware after exporting
    weights (see ``scripts/dev/export_yolo_openvino.py``)."""


class YOLODetector:
    """Async wrapper around an Ultralytics YOLO model.

    Model load is deferred to first inference call (cold-start
    isolation — the service comes up healthy before any model
    downloads / weights load). Subsequent calls reuse the same
    loaded model.
    """

    def __init__(self, config: DetectionConfig | None = None) -> None:
        self._config = config or DetectionConfig()
        self._model: YOLO | None = None
        self._load_lock = asyncio.Lock()

    def _inference_floor(self) -> float:
        """The conf handed to the model: the *minimum* across the default
        floor and every per-class override, so lower-floor classes (dog at
        0.25) still receive candidate boxes. Per-class filtering then
        happens in :func:`_results_to_tags`."""
        floors = [self._config.confidence_min, *self._config.per_class_confidence.values()]
        return min(floors)

    async def detect(self, jpeg_bytes: bytes, frame_ts: float) -> tuple[DetectionTag, ...]:
        """Run detection on one JPEG-encoded frame.

        Returns DetectionTags whose ``frame_ts`` matches the frame
        the detection came from. Decoding is done with OpenCV
        (already a hard dep for the JPEG-encode side of the pipeline)
        so we don't add another image library.

        Heavy: model load (first call only) + torch inference. Both
        run in a thread via ``run_in_executor`` so the asyncio loop
        stays responsive.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._detect_sync, jpeg_bytes, frame_ts)

    async def detect_batch(self, frames: list[tuple[bytes, float]]) -> tuple[DetectionTag, ...]:
        """Run detection on a batch of frames in one inference call.

        Ultralytics batches automatically when handed a list of
        images; this is meaningfully faster than serial per-frame
        calls (less torch overhead per frame).

        Returns the union of all detections across the batch, each
        tagged with its frame_ts.
        """
        if not frames:
            return ()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._detect_batch_sync, frames)

    async def warmup(self) -> None:
        """Ensure the model is loaded. Optional — first ``detect()``
        will load lazily. Calling at startup makes the first real
        request fast."""
        async with self._load_lock:
            await asyncio.get_running_loop().run_in_executor(None, self._ensure_model)

    # ─── internals (run in executor) ───────────────────────────────

    def _ensure_model(self) -> YOLO:
        if self._model is not None:
            return self._model
        # Lazy import — keep startup snappy for callers that never
        # actually invoke detection (synthetic-mode tests).
        from ultralytics import YOLO  # type: ignore[import-not-found]

        # Validate the backend's runtime is importable before loading;
        # produces a clearer error than the failure that'd otherwise
        # surface from ultralytics' internal path.
        if self._config.backend == "openvino":
            try:
                import openvino  # noqa: F401  # type: ignore[import-not-found]
            except ImportError as e:
                raise RuntimeError(
                    "Detection backend='openvino' but the 'openvino' "
                    "package is not installed. Install with "
                    "`pip install openvino` and export weights via "
                    "scripts/dev/export_yolo_openvino.py."
                ) from e

        logger.info(
            "yolo.loading weights=%s backend=%s",
            self._config.weights,
            self._config.backend,
        )
        # Ultralytics' YOLO() constructor auto-detects the format from
        # the path suffix: ``.pt`` -> PyTorch, ``*_openvino_model/``
        # -> OpenVINO. We pass the same path either way; the backend
        # config field controls how WE describe what we're doing in
        # logs + how we route the device string at predict time.
        self._model = YOLO(self._config.weights)
        logger.info("yolo.loaded")
        return self._model

    def _resolve_device(self) -> str | None:
        """Translate the configured device into what ultralytics expects.

        The pytorch backend takes torch device strings as-is
        (``None`` / ``"cpu"`` / ``"cuda:0"``). The openvino backend is
        different: ultralytics routes a bare ``"GPU"`` / ``"AUTO"``
        through its *CUDA* device parser, which raises
        ``ValueError: Invalid CUDA 'device=gpu'``. The OpenVINO runtime
        is reached via ultralytics' own ``"intel:<dev>"`` convention
        (``intel:cpu`` / ``intel:gpu`` / ``intel:npu``). Map the
        OpenVINO device names onto it so the iGPU is actually used.
        """
        dev = self._config.device
        if self._config.backend != "openvino":
            return dev
        # openvino backend → ultralytics 'intel:<dev>' form.
        if not dev or dev.upper() == "AUTO":
            # The openvino backend exists to use Intel acceleration;
            # default to the iGPU (this project's target hardware).
            return "intel:gpu"
        low = dev.lower()
        if low.startswith("intel:"):
            return low
        base = low.split(".", 1)[0]  # "gpu.0" → "gpu"
        if base in ("gpu", "cpu", "npu"):
            return f"intel:{base}"
        return dev

    def _detect_sync(self, jpeg_bytes: bytes, frame_ts: float) -> tuple[DetectionTag, ...]:
        img = _jpeg_to_bgr(jpeg_bytes)
        if img is None:
            return ()
        model = self._ensure_model()
        results = model.predict(
            img,
            conf=self._inference_floor(),
            iou=self._config.iou_min,
            imgsz=self._config.image_size,
            device=self._resolve_device(),
            verbose=False,
        )
        return _results_to_tags(results, img.shape, frame_ts, self._config)

    def _detect_batch_sync(self, frames: list[tuple[bytes, float]]) -> tuple[DetectionTag, ...]:
        images: list[np.ndarray] = []
        timestamps: list[float] = []
        for jpeg_bytes, ts in frames:
            img = _jpeg_to_bgr(jpeg_bytes)
            if img is not None:
                images.append(img)
                timestamps.append(ts)
        if not images:
            return ()
        model = self._ensure_model()
        # Track mode (not predict) so detections carry persistent
        # track_ids — the identity router/correlation key. Without
        # them the face/body pipelines drop every detection
        # ("no track_id → can't correlate") and never run. The frames
        # are passed in chronological order so ByteTrack maintains
        # consistent ids across the window; persist=False gives each
        # window a fresh tracker (windows are independent queries).
        results = model.track(
            images,
            conf=self._inference_floor(),
            iou=self._config.iou_min,
            imgsz=self._config.image_size,
            device=self._resolve_device(),
            persist=False,
            verbose=False,
        )
        out: list[DetectionTag] = []
        for result, ts in zip(results, timestamps, strict=False):
            out.extend(_results_to_tags([result], images[0].shape, ts, self._config))
        return tuple(out)


# ─── helpers ─────────────────────────────────────────────────────────


def _jpeg_to_bgr(jpeg_bytes: bytes) -> np.ndarray | None:
    """Decode a JPEG into a BGR uint8 numpy array.

    Returns ``None`` on decode failure rather than raising — a single
    corrupt frame shouldn't kill the inference loop. Empty bytes,
    truncated headers, etc. all funnel to None.
    """
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


def _results_to_tags(
    results: list,
    frame_shape: tuple[int, ...],
    frame_ts: float,
    config: DetectionConfig | None = None,
) -> tuple[DetectionTag, ...]:
    """Map Ultralytics ``Results`` → :class:`DetectionTag` tuple.

    Bounding boxes come back in pixel coordinates; we normalize to
    [0, 1] using the frame's height + width so the DetectionTag's
    bbox field matches the rest of the contract (which is
    consistently normalized — see contracts.py).

    Class names outside :data:`_INTERESTING_COCO_CLASSES` are filtered
    out entirely. We could pass them through under an "unknown" kind
    but that pollutes downstream tag_sets without adding signal at
    this phase.

    Per-class confidence floors (``config.per_class_confidence``) are
    applied here: each detection's mapped kind looks up its floor
    (falling back to ``config.confidence_min``) and is dropped if below.
    The model already ran at the *minimum* floor, so low-floor classes
    (dog/cat) survive inference and are kept here while a noisy
    moderate-confidence ``person`` is still held to 0.5.
    """
    if not results:
        return ()
    cfg = config or DetectionConfig()
    default_floor = cfg.confidence_min
    per_class = cfg.per_class_confidence
    h, w = frame_shape[:2]
    out: list[DetectionTag] = []
    for res in results:
        boxes = getattr(res, "boxes", None)
        names = getattr(res, "names", None)
        if boxes is None or names is None:
            continue
        # boxes.xyxy: (N, 4) tensor; boxes.cls: (N,); boxes.conf: (N,).
        # Convert via .cpu().numpy() because tensors might be on GPU.
        xyxy = _to_numpy(boxes.xyxy)
        confs = _to_numpy(boxes.conf)
        clses = _to_numpy(boxes.cls).astype(int)
        # boxes.id is present in track mode; None in predict mode or for
        # tracks the tracker hasn't confirmed yet.
        ids = _to_numpy(boxes.id) if getattr(boxes, "id", None) is not None else None
        for i in range(len(clses)):
            class_idx = int(clses[i])
            class_name = names.get(class_idx) if isinstance(names, dict) else names[class_idx]
            mapped = _INTERESTING_COCO_CLASSES.get(class_name)
            if mapped is None:
                continue
            conf_i = float(confs[i])
            if conf_i < per_class.get(mapped, default_floor):
                continue
            x1, y1, x2, y2 = xyxy[i].tolist()
            if ids is not None:
                track_id = str(int(ids[i]))
            else:
                # Tracker assigned no id (predict mode / unconfirmed
                # track). Synthesize a per-frame id so identity
                # pipelines can still associate within the frame —
                # identity must never be silently blocked by a missing
                # track_id (the bug this guards against).
                track_id = f"{round(frame_ts * 1000)}-{i}"
            out.append(
                DetectionTag(
                    kind=mapped,
                    confidence=round(conf_i, 3),
                    bbox=(
                        round(x1 / w, 4),
                        round(y1 / h, 4),
                        round(x2 / w, 4),
                        round(y2 / h, 4),
                    ),
                    frame_ts=frame_ts,
                    track_id=track_id,
                )
            )
    return tuple(out)


def _to_numpy(t: object) -> np.ndarray:
    """Best-effort tensor → numpy. Handles torch.Tensor (typical)
    and already-numpy inputs (test mocks)."""
    if hasattr(t, "cpu"):
        t = t.cpu()  # type: ignore[attr-defined]
    if hasattr(t, "numpy"):
        return t.numpy()  # type: ignore[attr-defined]
    return np.asarray(t)


_ = io  # silence unused-import warning (io kept for potential PIL fallback)
