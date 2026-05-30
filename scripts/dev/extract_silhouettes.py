#!/usr/bin/env python
"""Extract gait silhouettes from a frame corpus (Epic 10.11.6, step 1).

Gait models (OpenGait GaitBase/DeepGaitV2) consume a *sequence* of
binary person silhouettes, 64x44, person centered. This script is the
input side: YOLO-seg per frame -> largest-person instance mask ->
crop to the person -> center + resize to 64x44 binary -> save the
sequence + a contact sheet so we can *eyeball* whether this camera's
(steep top-down) geometry yields usable gait silhouettes before
investing in the gait model export.

Usage:
    python scripts/dev/extract_silhouettes.py --corpus face_debug/corpus/stand1
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

OUT = Path("C:/Users/darin_jwxgczt/SentiHome/face_debug/silhouettes")
GAIT_H, GAIT_W = 64, 44


def _center_silhouette(mask: np.ndarray) -> np.ndarray:
    """OpenGait-style normalize: crop to the silhouette's vertical
    extent, scale to height 64, then center horizontally in a 44-wide
    frame (by the mask's centroid)."""
    ys, xs = np.where(mask > 0)
    if len(ys) == 0:
        return np.zeros((GAIT_H, GAIT_W), np.uint8)
    top, bot = ys.min(), ys.max()
    cropped = mask[top : bot + 1, :]
    h = cropped.shape[0]
    scale = GAIT_H / h
    new_w = max(1, int(cropped.shape[1] * scale))
    resized = cv2.resize(cropped, (new_w, GAIT_H), interpolation=cv2.INTER_NEAREST)
    # horizontal center by centroid
    xs2 = np.where(resized > 0)[1]
    cx = int(xs2.mean()) if len(xs2) else new_w // 2
    canvas = np.zeros((GAIT_H, GAIT_W), np.uint8)
    left = GAIT_W // 2 - cx
    for x in range(resized.shape[1]):
        tx = x + left
        if 0 <= tx < GAIT_W:
            canvas[:, tx] = np.maximum(canvas[:, tx], resized[:, x])
    return canvas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="face_debug/corpus/stand1")
    ap.add_argument("--weights", default="yolo11x-seg.pt")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    paths = sorted(Path(args.corpus).glob("frame_*.jpg"))
    print(f"corpus frames: {len(paths)}")
    seg = YOLO(args.weights)

    sils: list[np.ndarray] = []
    for p in paths:
        bgr = cv2.imread(str(p))
        if bgr is None:
            continue
        h, w = bgr.shape[:2]
        r = seg.predict(bgr, imgsz=640, conf=0.5, verbose=False, device="cpu")[0]
        if r.masks is None or len(r.masks) == 0:
            continue
        # largest person mask
        best_area, best_mask = 0.0, None
        for i, box in enumerate(r.boxes):
            if int(box.cls) != 0:
                continue
            m = r.masks.data[i].cpu().numpy()
            m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
            area = float(m.sum())
            if area > best_area:
                best_area, best_mask = area, m
        if best_mask is None:
            continue
        x = np.where(best_mask > 0)[1]
        y = np.where(best_mask > 0)[0]
        crop = (best_mask[y.min() : y.max() + 1, x.min() : x.max() + 1] * 255).astype(np.uint8)
        sils.append(_center_silhouette(crop))

    print(f"silhouettes extracted: {len(sils)}")
    if not sils:
        print("no silhouettes — segmentation found no person")
        return

    for i, s in enumerate(sils):
        cv2.imwrite(str(OUT / f"sil_{i:03d}.png"), s)

    # contact sheet (upscaled 3x for visibility), up to 24
    show = sils[:24]
    tw, th = GAIT_W * 3, GAIT_H * 3
    cols = 8
    rows = (len(show) + cols - 1) // cols
    sheet = np.zeros((rows * th, cols * tw), np.uint8)
    for i, s in enumerate(show):
        up = cv2.resize(s, (tw, th), interpolation=cv2.INTER_NEAREST)
        ry, cx = divmod(i, cols)
        sheet[ry * th : ry * th + th, cx * tw : cx * tw + tw] = up
    cv2.imwrite(str(OUT / "silhouette_sheet.png"), sheet)
    print(f"wrote {len(sils)} silhouettes + silhouette_sheet.png to {OUT}")


if __name__ == "__main__":
    main()
