#!/usr/bin/env python
"""Gait recognition probe (Epic #101): silhouette-sequence -> embedding.

Chains the full gait path on a saved frame corpus:
  YOLO-seg per frame -> largest-person mask -> OpenGait-style 64x44
  centered silhouette -> stack to [S,64,44] -> GaitBase ONNX
  (temporal-pools the whole clip) -> ONE 4096-d gait embedding per clip.

Unlike body-ID (one embedding per frame), gait keys on walking DYNAMICS,
so a gait template is per *sequence* — you compare CLIPS, not frames.
That means separability needs multiple walk clips per subject; this probe
produces the per-clip embedding and, given >=2 clips, reports pairwise
cosine (genuine if same subject_id per manifest, else imposter).

Requires a DENSE walk (the gait cycle needs ~native fps); a 1fps
wandering corpus won't have a coherent stride. Capture with
capture_corpus.py --stream sub (sub-stream ~30-37fps).

Usage:
    uv run --project services/preprocessor python scripts/dev/gait_probe.py \\
        --corpus face_debug/corpus --min-frames 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from ultralytics import YOLO

sys.path.insert(0, str(Path(__file__).parent))
from eval_corpus import discover_manifests
from extract_silhouettes import _center_silhouette

GAIT_MODEL = "C:/Users/darin_jwxgczt/SentiHome/models/gaitbase_grew.onnx"


def _clip_silhouettes(seg: YOLO, clip_dir: Path) -> np.ndarray:
    """Ordered [S,64,44] uint8 silhouette stack for one clip."""
    sils: list[np.ndarray] = []
    for p in sorted(clip_dir.glob("frame_*.jpg")):
        bgr = cv2.imread(str(p))
        if bgr is None:
            continue
        h, w = bgr.shape[:2]
        r = seg.predict(bgr, imgsz=640, conf=0.5, verbose=False, device="cpu")[0]
        if r.masks is None or len(r.masks) == 0:
            continue
        best_area, best_mask = 0.0, None
        for i, box in enumerate(r.boxes):
            if int(box.cls) != 0:  # person
                continue
            m = r.masks.data[i].cpu().numpy()
            m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
            area = float(m.sum())
            if area > best_area:
                best_area, best_mask = area, m
        if best_mask is None:
            continue
        ys, xs = np.where(best_mask > 0)
        crop = (best_mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1] * 255).astype(np.uint8)
        sils.append(_center_silhouette(crop))
    return np.asarray(sils, dtype=np.uint8) if sils else np.empty((0, 64, 44), np.uint8)


def _gait_embed(sess: ort.InferenceSession, sils: np.ndarray) -> np.ndarray:
    """[S,64,44] uint8 -> [4096] L2-normalized gait embedding."""
    x = (sils.astype(np.float32) / 255.0)[None, ...]  # [1,S,64,44]
    emb = sess.run(None, {"sils": x})[0][0]  # [4096]
    n = float(np.linalg.norm(emb))
    return emb / n if n > 1e-8 else emb


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="face_debug/corpus")
    ap.add_argument("--seg-weights", default="yolo11x-seg.pt")
    ap.add_argument("--min-frames", type=int, default=15, help="min silhouettes for a usable clip")
    args = ap.parse_args()

    corpus_root = Path(args.corpus)
    manifests = discover_manifests(corpus_root)
    if not manifests:
        print("no manifested clips")
        return
    seg = YOLO(args.seg_weights)
    sess = ort.InferenceSession(GAIT_MODEL, providers=["CPUExecutionProvider"])

    embeds: list[np.ndarray] = []
    subjects: list[str] = []
    names: list[str] = []
    for m in manifests:
        sils = _clip_silhouettes(seg, corpus_root / m.name)
        if len(sils) < args.min_frames:
            print(
                f"  - {m.name}: {len(sils)} silhouettes < {args.min_frames} (skip; need denser walk)"
            )
            continue
        emb = _gait_embed(sess, sils)
        embeds.append(emb)
        subjects.append(m.subject_id)
        names.append(m.name)
        print(
            f"  - {m.name}: subject={m.subject_id} silhouettes={len(sils)} -> gait embed {emb.shape}"
        )

    if len(embeds) < 2:
        print(
            f"\n{len(embeds)} usable clip(s) — need >=2 clips to compare. "
            "Gait is per-sequence: capture multiple dense walks."
        )
        return

    print("\n=== pairwise gait cosine (clip-level) ===")
    for i in range(len(embeds)):
        for j in range(i + 1, len(embeds)):
            cos = float(embeds[i] @ embeds[j])
            kind = "genuine" if subjects[i] == subjects[j] else "imposter"
            print(f"  {names[i]} vs {names[j]}: {cos:.3f} ({kind})")


if __name__ == "__main__":
    main()
