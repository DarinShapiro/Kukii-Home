#!/usr/bin/env python
"""Ensemble identity bench — measure & fuse multiple body-embedding models.

Turns "which model is best, and does fusing them beat any single one?" from
opinion into measurement, over the manifested corpus (face_debug/corpus).

What it does:
  1. Crops the (largest) person from each manifested clip's frames, ONCE,
     and caches the crops to disk (crops are model-independent — extract
     them once, score every model against the same crops).
  2. Runs EVERY registered model over the same crops, labelling each
     embedding with the clip's ground-truth ``subject_id``.
  3. Reports per-model separability (AUC / EER / d-prime) via
     ``eval_metrics.separability``.
  4. Runs a FUSION SWEEP: combines per-model cosine scores with weighted
     noisy-OR (the production combiner) across a weight grid, and reports
     which ensemble separates best — i.e. whether fusion beats the best
     single model, and at what weights.

Adding a model = one line in ``MODELS``. The bench is model-agnostic:
each entry is ``(name, onnx_path, in_h, in_w, preprocess)``.

IMPORTANT — separability needs ≥2 subjects. With one subject the corpus
is genuine-only; the per-model report honestly shows AUC/EER/d'=None and
the fusion sweep is skipped (nothing to separate). Capture a second
person and re-run; the bench then produces real rankings with no code
change. Until then it still validates the *plumbing* (crops, embeddings,
shapes) and prints genuine-similarity stats.

Usage:
    uv run --project services/preprocessor python scripts/dev/ensemble_bench.py \\
        --corpus C:/Users/darin_jwxgczt/Kukii-Home/face_debug/corpus \\
        --yolo yolo11x.pt --device cpu --max-frames-per-clip 30
"""

from __future__ import annotations

import argparse
import itertools
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from kukiihome_preprocessor.pipelines.body_id import (
    _crop_person,
    _l2_normalize_rows,
    _preprocess,
)
from ultralytics import YOLO

sys.path.insert(0, str(Path(__file__).parent))
from eval_corpus import ClipManifest, discover_manifests
from eval_metrics import separability

# ─── Model registry — add a model with ONE line ────────────────────────
# (name, onnx_path, input_h, input_w, preprocess). preprocess defaults to
# the body_id ImageNet pipeline (resize→RGB→[0,1]→ImageNet-normalize→CHW);
# a model needing different normalization passes its own.

MODELS_ROOT = "models"

# CLIP/SigLIP use their own normalization, NOT ImageNet. Feeding a CLIP
# model ImageNet-normalized input silently degrades it — exactly the kind
# of preprocessing mismatch we suspect bit CC-ReID. So CLIP-family models
# get this preprocess explicitly.
_CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def _clip_preprocess(crop_bgr: np.ndarray, height: int, width: int) -> np.ndarray:
    resized = cv2.resize(crop_bgr, (width, height), interpolation=cv2.INTER_CUBIC)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    normed = (rgb.astype(np.float32) / 255.0 - _CLIP_MEAN) / _CLIP_STD
    return np.transpose(normed, (2, 0, 1))


@dataclass
class ModelSpec:
    name: str
    onnx_path: str
    in_h: int
    in_w: int
    preprocess: Callable[[np.ndarray, int, int], np.ndarray] = _preprocess


def _default_models() -> list[ModelSpec]:
    """Every model that has an ONNX on disk gets benched. Missing files are
    skipped with a notice (so a partial export set still runs)."""
    specs = [
        # ReID-trained body embedders.
        ModelSpec("osnet", f"{MODELS_ROOT}/osnet_x1_0.onnx", 256, 128),
        ModelSpec("ccreid", f"{MODELS_ROOT}/ccreid_cal_ltcc.onnx", 384, 192),
        # General self-supervised embedders (ImageNet-normalized) — the
        # survey's cross-domain generalizers. dinov2 vits already beats
        # ccreid; vitl is the stronger variant.
        ModelSpec("dinov2_s", f"{MODELS_ROOT}/dinov2_vits14.onnx", 224, 224),
        ModelSpec("dinov2_l", f"{MODELS_ROOT}/dinov2_vitl.onnx", 518, 518),
        # Language-aligned (CLIP normalization) — survey's pick for stable
        # cross-domain ReID on unseen cameras.
        ModelSpec(
            "openclip_b32", f"{MODELS_ROOT}/openclip_vitb32.onnx", 224, 224, _clip_preprocess
        ),
    ]
    return [s for s in specs if Path(s.onnx_path).is_file()]


# ─── crop extraction (cached) ──────────────────────────────────────────


def _extract_crops(
    yolo: YOLO,
    clip_dir: Path,
    *,
    max_frames: int,
    device: str,
    cache_dir: Path,
) -> list[np.ndarray]:
    """Largest-person crop per frame (cached as .npy so re-runs skip YOLO)."""
    cache = cache_dir / f"{clip_dir.name}_crops.npz"
    if cache.is_file():
        data = np.load(cache, allow_pickle=True)
        return list(data["crops"])
    frames = sorted(clip_dir.glob("frame_*.jpg"))
    # Even stride so crops span the whole clip, not just the first N frames.
    if max_frames > 0 and len(frames) > max_frames:
        step = len(frames) // max_frames
        frames = frames[::step][:max_frames]
    crops: list[np.ndarray] = []
    for fp in frames:
        bgr = cv2.imread(str(fp))
        if bgr is None:
            continue
        h, w = bgr.shape[:2]
        det = yolo.predict(bgr, imgsz=960, conf=0.5, verbose=False, device=device)[0]
        best = None
        for b in det.boxes:
            if int(b.cls) != 0:
                continue
            x1, y1, x2, y2 = (float(v) for v in b.xyxyn[0])
            area = (x2 - x1) * (y2 - y1)
            if best is None or area > best[0]:
                best = (area, (x1, y1, x2, y2))
        if best is not None:
            c = _crop_person(bgr, best[1], w, h)
            if c is not None:
                crops.append(c)
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, crops=np.array(crops, dtype=object))
    return crops


# ─── embedding + compute telemetry ─────────────────────────────────────


@dataclass
class EmbedResult:
    """Embeddings + the compute telemetry needed to judge a model on the
    accuracy↔cost frontier, not accuracy alone. Raw recall is meaningless
    without the latency it costs to get it."""

    emb: np.ndarray
    dim: int
    file_mb: float
    load_ms: float  # cold: session create + warmup inference (one-off / model swap)
    embed_ms_per_crop: float  # steady-state per-crop latency (the 24/7 cost)
    batch_ms: float  # wall-clock for the whole batch


def _embed(spec: ModelSpec, crops: list[np.ndarray]) -> EmbedResult:
    import time

    file_mb = Path(spec.onnx_path).stat().st_size / 1e6
    t0 = time.perf_counter()
    sess = ort.InferenceSession(spec.onnx_path, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0].name
    # Warmup: a single inference so the steady-state timing excludes the
    # first-call graph-allocation cost (which belongs to load, not embed).
    warm = spec.preprocess(crops[0], spec.in_h, spec.in_w)[None].astype(np.float32)
    sess.run(None, {inp: warm})
    load_ms = (time.perf_counter() - t0) * 1000.0

    batch = np.stack([spec.preprocess(c, spec.in_h, spec.in_w) for c in crops]).astype(np.float32)
    t1 = time.perf_counter()
    raw = sess.run(None, {inp: batch})[0]
    batch_ms = (time.perf_counter() - t1) * 1000.0

    if raw.ndim > 2:  # DINOv2 returns (N, tokens, dim) on some exports
        raw = raw.reshape(raw.shape[0], -1)
    emb = _l2_normalize_rows(raw)
    return EmbedResult(
        emb=emb,
        dim=emb.shape[1],
        file_mb=file_mb,
        load_ms=load_ms,
        embed_ms_per_crop=batch_ms / max(1, len(crops)),
        batch_ms=batch_ms,
    )


# ─── fusion sweep ──────────────────────────────────────────────────────


def _fused_separability(
    per_model_emb: dict[str, np.ndarray],
    labels: list[str],
    weights: dict[str, float],
):
    """Separability of a WEIGHTED-noisy-OR ensemble.

    For every embedding pair, each model contributes cosine ``s_m``; the
    fused pair-score is ``1 - Π(1 - w_m * s_m)`` (same combiner as
    production fusion, applied to similarities). We then run those fused
    pair-scores through the genuine/imposter split. Implemented directly
    on the pair matrix so it composes any number of models.
    """
    names = list(per_model_emb)
    n = len(labels)
    # cosine matrices per model
    cos = {m: per_model_emb[m] @ per_model_emb[m].T for m in names}
    gen_scores, imp_scores = [], []
    for i in range(n):
        for j in range(i + 1, n):
            prod = 1.0
            for m in names:
                s = max(0.0, float(cos[m][i, j]))  # clamp neg cos to 0 for noisy-OR
                prod *= 1.0 - weights.get(m, 0.0) * s
            fused = 1.0 - prod
            (gen_scores if labels[i] == labels[j] else imp_scores).append(fused)
    gen = np.array(gen_scores)
    imp = np.array(imp_scores)
    if len(gen) == 0 or len(imp) == 0:
        return None
    # reuse eval_metrics' AUC/EER via a tiny shim: build a fake embedding
    # set is overkill — compute AUC directly.
    from eval_metrics import _eer, _roc_auc

    auc = _roc_auc(gen, imp)
    eer, _ = _eer(gen, imp)
    return auc, eer, float(gen.mean()), float(imp.mean())


def _weight_grid(names: list[str]) -> list[dict[str, float]]:
    """Coarse grid over {0.0, 0.5, 1.0} per model, dropping all-zero."""
    levels = [0.0, 0.5, 1.0]
    out = []
    for combo in itertools.product(levels, repeat=len(names)):
        if all(v == 0.0 for v in combo):
            continue
        out.append(dict(zip(names, combo, strict=True)))
    return out


# ─── main ──────────────────────────────────────────────────────────────


def _select(manifests: list[ClipManifest], camera: str | None) -> list[ClipManifest]:
    return [m for m in manifests if not camera or m.camera == camera]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--camera", default="", help="filter to one camera id")
    ap.add_argument("--yolo", default="yolo11x.pt")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--max-frames-per-clip", type=int, default=30)
    ap.add_argument("--cache", default=".scratch/crop_cache")
    args = ap.parse_args()

    corpus = Path(args.corpus)
    manifests = _select(discover_manifests(corpus), args.camera or None)
    if not manifests:
        print("no manifested clips match")
        return
    subjects = sorted({m.subject_id for m in manifests})
    print(f"clips={len(manifests)} subjects={subjects}")
    for m in manifests:
        print(f"  {m.name}: subj={m.subject_id} outfit={m.outfit_id} cam={m.camera}")

    yolo = YOLO(args.yolo)
    cache_dir = Path(args.cache)

    # 1) crops (cached) + labels, per clip
    clip_crops: list[tuple[str, list[np.ndarray]]] = []
    labels: list[str] = []
    for m in manifests:
        crops = _extract_crops(
            yolo,
            corpus / m.name,
            max_frames=args.max_frames_per_clip,
            device=args.device,
            cache_dir=cache_dir,
        )
        print(f"  crops[{m.name}] = {len(crops)}")
        if crops:
            clip_crops.append((m.subject_id, crops))
            labels.extend([m.subject_id] * len(crops))
    all_crops = [c for _, cs in clip_crops for c in cs]
    if not all_crops:
        print("no person crops extracted — nothing to bench")
        return

    # 2) embed every model over the same crops + collect compute telemetry.
    # Accuracy alone is not a verdict: the report pairs each model's
    # genuine/separability score with its per-crop latency, load cost, file
    # size and embedding dim — so model choice is on the accuracy↔compute
    # frontier (a marginally-better model that's 10x slower loses on a real
    # box). This is the model-selection analogue of the §10.11.3b sim's
    # latency x cost x accuracy Pareto.
    print("\n=== PER-MODEL: ACCURACY x COMPUTE ===")
    print(f"  {'model':8} {'embed_ms':>9} {'load_ms':>8} {'MB':>6} {'dim':>5}  accuracy")
    per_model_emb: dict[str, np.ndarray] = {}
    telem: dict[str, EmbedResult] = {}
    for spec in _default_models():
        if not Path(spec.onnx_path).is_file():
            print(f"  {spec.name}: SKIP (no file {spec.onnx_path})")
            continue
        res = _embed(spec, all_crops)
        per_model_emb[spec.name] = res.emb
        telem[spec.name] = res
        rep = separability(res.emb, labels)
        # acc = AUC when testable (≥2 subjects), else genuine-mean as a
        # consistency proxy (clearly labelled).
        acc = (
            f"AUC={rep.roc_auc:.3f}"
            if rep.roc_auc is not None
            else f"genuine_mean={rep.genuine_mean:.3f}"
        )
        print(
            f"  {spec.name:8} {res.embed_ms_per_crop:8.1f}m {res.load_ms:7.0f}m "
            f"{res.file_mb:5.0f} {res.dim:5}  {acc}"
        )

    # 3) fusion sweep (only meaningful with ≥2 subjects)
    if len(subjects) < 2:
        print(
            "\n=== FUSION SWEEP: skipped — need ≥2 subjects for separability. "
            "Capture a second person and re-run (no code change)."
        )
        return
    print("\n=== FUSION SWEEP (weighted noisy-OR; AUC x ensemble compute) ===")
    print("  ensemble cost = Σ embed_ms of the active models (they run in")
    print("  parallel in prod, but Σ is the honest total-compute figure).")
    names = list(per_model_emb)
    results = []
    for w in _weight_grid(names):
        r = _fused_separability(per_model_emb, labels, w)
        if r is None:
            continue
        auc, eer, _gm, _im = r
        active_models = [m for m in names if w[m] > 0]
        cost_ms = sum(telem[m].embed_ms_per_crop for m in active_models)
        active = "+".join(f"{m}:{w[m]}" for m in active_models)
        results.append((auc, eer, active, cost_ms))
    # Sort by AUC desc; the Pareto frontier (best AUC at each cost tier) is
    # what actually decides the ensemble — not raw top-AUC.
    results.sort(reverse=True)
    print(f"  {'AUC':>6} {'EER':>6} {'cost_ms':>8}  ensemble")
    for auc, eer, active, cost_ms in results[:15]:
        print(f"  {auc:6.3f} {eer:6.3f} {cost_ms:7.1f}m  [{active}]")
    # Highlight the Pareto-optimal set: no cheaper ensemble beats its AUC.
    pareto = []
    for auc, eer, active, cost_ms in sorted(results, key=lambda r: r[3]):
        if not pareto or auc > pareto[-1][0]:
            pareto.append((auc, eer, active, cost_ms))
    print("\n  PARETO FRONTIER (best accuracy per compute tier):")
    for auc, _eer, active, cost_ms in pareto:
        print(f"    AUC={auc:.3f} @ {cost_ms:.1f}ms  [{active}]")


if __name__ == "__main__":
    main()
