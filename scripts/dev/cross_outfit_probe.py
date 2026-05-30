#!/usr/bin/env python
"""Cross-outfit body re-ID: enroll on corpus A, match corpus B.

The decisive clothing-robustness test (Epic 10.11.5). Enroll a body
centroid from corpus A (outfit 1), then score every person crop in
corpus B (outfit 2) against it. Runs BOTH models side by side:

  * OSNet  (256x128, clothing-dependent appearance re-ID)
  * CC-ReID (384x192, CAL clothes-adversarial — body shape)

Expected: OSNet cross-outfit cosine craters (clothes changed); CC-ReID
holds (body shape unchanged). Also reports a same-outfit A->A control
so the cross-outfit drop is interpretable.

Usage:
    python scripts/dev/cross_outfit_probe.py \\
        --enroll face_debug/corpus/stand1 \\
        --test   face_debug/corpus/walk2_outfit2
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
MODELS = {
    "OSNet": ("C:/Users/darin_jwxgczt/SentiHome/models/osnet_x1_0.onnx", 256, 128),
    "CC-ReID": ("C:/Users/darin_jwxgczt/SentiHome/models/ccreid_cal_ltcc.onnx", 384, 192),
}


def person_crops(yolo: YOLO, corpus: Path) -> list[np.ndarray]:
    crops: list[np.ndarray] = []
    for p in sorted(corpus.glob("frame_*.jpg")):
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
        if best is None:
            continue
        crop = _crop_person(bgr, best[1], w, h)
        if crop is not None:
            crops.append(crop)
    return crops


def embed(model_path: str, h: int, w: int, crops: list[np.ndarray]) -> np.ndarray:
    sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    batch = np.stack([_preprocess(c, h, w) for c in crops], axis=0).astype(np.float32)
    raw = sess.run(None, {sess.get_inputs()[0].name: batch})[0]
    return _l2_normalize_rows(raw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--enroll", required=True, help="corpus A (outfit 1)")
    ap.add_argument("--test", required=True, help="corpus B (outfit 2)")
    args = ap.parse_args()

    yolo = YOLO(OV_MODEL, task="detect")
    print("extracting person crops ...", flush=True)
    crops_a = person_crops(yolo, Path(args.enroll))
    crops_b = person_crops(yolo, Path(args.test))
    print(f"enroll(A)={len(crops_a)} crops   test(B)={len(crops_b)} crops\n")
    if len(crops_a) < 4 or len(crops_b) < 4:
        print("not enough crops")
        return

    half = len(crops_a) // 2
    print(
        f"{'model':>9} {'A->A ctrl':>10} {'A->B xout':>10} {'B mean':>8} {'B min':>7} {'B>=0.6':>8}"
    )
    for name, (path, h, w) in MODELS.items():
        ea = embed(path, h, w, crops_a)
        eb = embed(path, h, w, crops_b)
        centroid = _l2_normalize_rows(ea[:half].mean(axis=0, keepdims=True))[0]
        ctrl = float((ea[half:] @ centroid).mean())  # same-outfit control
        sims_b = eb @ centroid  # cross-outfit
        frac = float((sims_b >= 0.6).mean())
        print(
            f"{name:>9} {ctrl:>10.3f} {float(sims_b.mean()):>10.3f} "
            f"{float(sims_b.mean()):>8.3f} {float(sims_b.min()):>7.3f} {frac:>8.0%}"
        )

    print(
        "\nInterpretation: A->A is the same-outfit control (both should be high). "
        "A->B is cross-outfit — OSNet expected to drop sharply, CC-ReID to hold."
    )


if __name__ == "__main__":
    main()
