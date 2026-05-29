#!/usr/bin/env python
"""Offline body re-ID (OSNet) probe over a saved frame corpus.

Companion to live_face_probe.py, but for the body-ID modality and
fully offline — no walk, no live buffer. Replays a saved frame corpus
(face_debug/corpus/<name>) through the exact production body path:

  1. YOLO person detection on the dynamic OpenVINO IR (iGPU),
  2. crop each person bbox, preprocess EXACTLY as the runtime pipeline
     (importing body_id._crop_person / _preprocess / _l2_normalize_rows),
  3. OSNet ONNX -> 512-d L2-normalized embedding,
  4. self-consistency eval: enroll a centroid from the first half of
     person crops, score the held-out second half by cosine vs that
     centroid (and vs a random-other baseline for contrast).

This validates the OSNet export + the body pipeline produce
discriminative, stable embeddings on THIS camera's geometry — where
face-rec failed (49px faces) but the body is large and sharp.

Usage:
    python scripts/dev/body_id_probe.py --corpus face_debug/corpus/stand1
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from sentihome_preprocessor.pipelines.body_id import (
    _crop_person,
    _l2_normalize_rows,
    _preprocess,
)
from ultralytics import YOLO

OV_MODEL = "C:/Users/darin_jwxgczt/SentiHome/yolo11x_openvino_model"
OSNET = "C:/Users/darin_jwxgczt/SentiHome/models/osnet_x1_0.onnx"
OUT = Path("C:/Users/darin_jwxgczt/SentiHome/face_debug")
H, W = 256, 128


def embed(session, crops_bgr: list[np.ndarray]) -> np.ndarray:
    batch = np.stack([_preprocess(c, H, W) for c in crops_bgr], axis=0).astype(np.float32)
    name = session.get_inputs()[0].name
    raw = session.run(None, {name: batch})[0]
    return _l2_normalize_rows(raw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="face_debug/corpus/stand1")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    paths = sorted(Path(args.corpus).glob("frame_*.jpg"))
    print(f"corpus frames: {len(paths)}")

    yolo = YOLO(OV_MODEL, task="detect")
    session = ort.InferenceSession(OSNET, providers=["CPUExecutionProvider"])

    crops: list[np.ndarray] = []
    for p in paths:
        bgr = cv2.imread(str(p))
        if bgr is None:
            continue
        h, w = bgr.shape[:2]
        det = yolo.predict(bgr, imgsz=640, conf=0.5, verbose=False, device="intel:gpu")[0]
        # largest person per frame (the subject)
        best = None
        for b in det.boxes:
            if int(b.cls) != 0:
                continue
            x1, y1, x2, y2 = (float(v) for v in b.xyxyn[0])
            area = (x2 - x1) * (y2 - y1)
            if best is None or area > best[0]:
                best = (area, (x1, y1, x2, y2))
        if best is None:
            continue
        crop = _crop_person(bgr, best[1], w, h)
        if crop is not None:
            crops.append(crop)

    n = len(crops)
    print(f"person crops: {n}")
    if n < 4:
        print("not enough person crops for a split eval")
        return

    embs = embed(session, crops)  # (n, 512)
    half = n // 2
    centroid = _l2_normalize_rows(embs[:half].mean(axis=0, keepdims=True))[0]
    holdout = embs[half:]
    sims = holdout @ centroid  # cosine, all L2-normed

    # baseline: mean pairwise cosine between random distinct embeddings
    rng_idx = [(i, (i + 7) % n) for i in range(n)]
    baseline = float(np.mean([float(embs[a] @ embs[b]) for a, b in rng_idx if a != b]))

    print(f"\nenroll centroid from {half} crops; holdout = {len(holdout)} crops")
    print(f"{'holdout#':>9} {'cos_vs_self':>12}")
    for i, s in enumerate(sims):
        print(f"{i:>9} {float(s):>12.3f}")
    print(f"\nself-match  mean={float(sims.mean()):.3f}  min={float(sims.min()):.3f}  max={float(sims.max()):.3f}")
    print(f"intra-corpus pairwise baseline (same person, sanity) = {baseline:.3f}")
    thr = 0.6
    n_match = int((sims >= thr).sum())
    print(f"holdout above body threshold {thr}: {n_match}/{len(holdout)}")

    # save a few person crops for visual
    for i, c in enumerate(crops[:8]):
        cv2.imwrite(str(OUT / f"body_{i:02d}.png"), cv2.resize(c, (W, H)))
    print(f"wrote sample body crops to {OUT}")


if __name__ == "__main__":
    main()
