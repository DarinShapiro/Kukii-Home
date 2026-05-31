#!/usr/bin/env python
"""Rigorous identity-eval harness (Epic #103, piece 3b).

Ties the manifest (eval_corpus) + metrics (eval_metrics) to real model
embeddings. For each manifested clip it extracts the subject's person
crops, embeds them with the chosen body model, labels every embedding
with the clip's ground-truth subject_id, then computes a
SeparabilityReport (genuine-vs-imposter AUC/EER/d-prime) over the
selected clips.

Why this exists: the cross-outfit finding was only "directional" because
conditions weren't controlled and there was no imposter baseline. This
harness makes conclusions defensible AND replayable — every number comes
from saved, manifested clips, regenerable with no re-walk.

Axis filters (--camera/--lighting/--outfit/--subject) select a controlled
slice; --per-condition reports separately per value of an axis. With one
subject the report honestly says separability is untestable.

Models: osnet (256x128) | ccreid (384x192). Reuses the body pipeline's
exact crop+preprocess so numbers match production.

Usage:
    uv run --project services/preprocessor python scripts/dev/eval_identity.py \\
        --model osnet --corpus face_debug/corpus
"""

from __future__ import annotations

import argparse
import sys
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

OV_MODEL = "C:/Users/darin_jwxgczt/Kukii-Home/yolo11x_openvino_model"
MODELS = {
    "osnet": ("C:/Users/darin_jwxgczt/Kukii-Home/models/osnet_x1_0.onnx", 256, 128),
    "ccreid": ("C:/Users/darin_jwxgczt/Kukii-Home/models/ccreid_cal_ltcc.onnx", 384, 192),
}


def _clip_crops(yolo: YOLO, clip_dir: Path, limit: int) -> list[np.ndarray]:
    crops: list[np.ndarray] = []
    for p in sorted(clip_dir.glob("frame_*.jpg"))[:limit]:
        bgr = cv2.imread(str(p))
        if bgr is None:
            continue
        h, w = bgr.shape[:2]
        det = yolo.predict(bgr, imgsz=640, conf=0.5, verbose=False, device="intel:gpu")[0]
        best = None
        for b in det.boxes:
            if int(b.cls) != 0:
                continue
            x1, y1, x2, y2 = (float(v) for v in b.xyxyn[0])
            area = (x2 - x1) * (y2 - y1)
            if best is None or area > best[0]:
                best = (area, (x1, y1, x2, y2))
        if best is not None:
            crop = _crop_person(bgr, best[1], w, h)
            if crop is not None:
                crops.append(crop)
    return crops


def _embed(model_path: str, h: int, w: int, crops: list[np.ndarray]) -> np.ndarray:
    sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    batch = np.stack([_preprocess(c, h, w) for c in crops], axis=0).astype(np.float32)
    raw = sess.run(None, {sess.get_inputs()[0].name: batch})[0]
    return _l2_normalize_rows(raw)


def _select(manifests: list[ClipManifest], args) -> list[ClipManifest]:
    def ok(m: ClipManifest) -> bool:
        return (
            (not args.camera or m.camera == args.camera)
            and (not args.lighting or m.lighting == args.lighting)
            and (not args.outfit or m.outfit_id == args.outfit)
            and (not args.subject or m.subject_id == args.subject)
        )

    return [m for m in manifests if ok(m)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=tuple(MODELS), default="osnet")
    ap.add_argument("--corpus", default="face_debug/corpus")
    ap.add_argument("--camera", default="")
    ap.add_argument("--lighting", default="")
    ap.add_argument("--outfit", default="")
    ap.add_argument("--subject", default="")
    ap.add_argument("--limit", type=int, default=40, help="max frames/clip")
    args = ap.parse_args()

    corpus_root = Path(args.corpus)
    manifests = _select(discover_manifests(corpus_root), args)
    if not manifests:
        print("no manifested clips match the filters")
        return
    print(f"model={args.model} clips={len(manifests)}")
    for m in manifests:
        print(
            f"  - {m.name}: subject={m.subject_id} outfit={m.outfit_id} "
            f"cam={m.camera} light={m.lighting} ({m.frame_count} frames)"
        )

    model_path, h, w = MODELS[args.model]
    yolo = YOLO(OV_MODEL, task="detect")

    all_embs: list[np.ndarray] = []
    labels: list[str] = []
    for m in manifests:
        crops = _clip_crops(yolo, corpus_root / m.name, args.limit)
        if not crops:
            print(f"  ! {m.name}: no person crops")
            continue
        embs = _embed(model_path, h, w, crops)
        all_embs.append(embs)
        labels.extend([m.subject_id] * len(embs))
        print(f"  {m.name}: {len(embs)} embeddings")

    if not all_embs:
        print("no embeddings extracted")
        return
    X = np.vstack(all_embs)
    report = separability(X, labels)
    print("\n=== SEPARABILITY ===")
    print(report.summary())


if __name__ == "__main__":
    main()
