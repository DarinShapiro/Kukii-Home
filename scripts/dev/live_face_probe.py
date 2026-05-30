#!/usr/bin/env python
"""Live face probe — grab frames straight off RTSP and show what the
recognizer sees, with zero dependence on the preprocessor's rolling
buffer (so nothing can evict mid-test).

For each grabbed 4K frame it runs the *exact* production path:
  1. YOLO person detection on the dynamic OpenVINO IR (Intel iGPU),
  2. head-region crop of each person box (face_pipeline._head_region),
  3. InsightFace detect + landmark-aligned 112x112 crop on the head,
  4. cosine vs the live ``darin`` 4-photo centroid.

Saves head crops, aligned crops, and a det/cosine table. Doubles as the
end-to-end validation that the dynamic-batch OpenVINO export actually
runs on the iGPU (the static-batch IR raised a [1,..] vs [N,..] shape
error in-service).

Usage:
    python scripts/dev/live_face_probe.py --frames 15 --interval 1.0
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import av
import cv2
import numpy as np
from insightface.app import FaceAnalysis
from insightface.utils import face_align
from sentihome_preprocessor.pipelines.identity.face_pipeline import _head_region
from ultralytics import YOLO

RTSP = "rtsp://admin:J9v%258emo@192.168.68.89:554/cam/realmonitor?channel=1&subtype=0"
OV_MODEL = "C:/Users/darin_jwxgczt/SentiHome/yolo11x_openvino_model"
OUT = Path("C:/Users/darin_jwxgczt/SentiHome/face_debug")
REF_PHOTOS = [
    "C:/Users/darin_jwxgczt/Downloads/68989224054__B95AD3E0-00E2-4BDF-903E-49A1A5A961FC.fullsizerender.JPG",
    "C:/Users/darin_jwxgczt/Downloads/IMG_2184.JPG",
    "C:/Users/darin_jwxgczt/Downloads/IMG_1711.JPG",
    "C:/Users/darin_jwxgczt/Downloads/IMG_1351.JPG",
]


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v if n < 1e-8 else v / n


def _largest(faces):
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def grab_frames(n: int, interval: float, save_dir: Path | None) -> list[np.ndarray]:
    """Pull n frames ~interval seconds apart off the live main stream.

    If ``save_dir`` is given, each grabbed frame is written there as
    ``frame_NNNN.jpg`` — a reusable corpus so the pipeline can be
    rerun offline (different model / threshold / crop) with no walk.
    """
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
    out: list[np.ndarray] = []
    container = av.open(RTSP, options={"rtsp_transport": "tcp"}, timeout=15)
    try:
        last = 0.0
        for frame in container.decode(video=0):
            now = time.perf_counter()
            if now - last < interval:
                continue
            last = now
            bgr = frame.to_ndarray(format="bgr24")
            out.append(bgr)
            if save_dir is not None:
                cv2.imwrite(str(save_dir / f"frame_{len(out) - 1:04d}.jpg"), bgr)
            print(f"  grabbed frame {len(out)}/{n}", flush=True)
            if len(out) >= n:
                break
    finally:
        container.close()
    if save_dir is not None:
        print(f"  saved {len(out)} raw frames to {save_dir}")
    return out


def load_frames(replay_dir: Path) -> list[np.ndarray]:
    """Load a saved frame corpus (frame_*.jpg) for offline replay."""
    paths = sorted(replay_dir.glob("frame_*.jpg"))
    frames = [img for p in paths if (img := cv2.imread(str(p))) is not None]
    print(f"  loaded {len(frames)} frames from {replay_dir}")
    return frames


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=15)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument(
        "--save-dir",
        default="C:/Users/darin_jwxgczt/SentiHome/face_debug/corpus",
        help="Where to save raw grabbed frames (capture mode). Reusable corpus.",
    )
    ap.add_argument(
        "--replay",
        default=None,
        help="Load frames from this dir instead of RTSP (offline rerun, no walk).",
    )
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    app = FaceAnalysis(name="buffalo_s", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    embs = []
    for p in REF_PHOTOS:
        bgr = cv2.imread(p)
        if bgr is None:
            continue
        faces = app.get(bgr)
        if faces:
            embs.append(np.asarray(_largest(faces).normed_embedding, dtype=np.float32))
    centroid = _normalize(np.mean(np.stack(embs), axis=0))
    print(f"centroid from {len(embs)} ref photos\n")

    yolo = YOLO(OV_MODEL, task="detect")

    if args.replay:
        print(f"replaying saved corpus from {args.replay} (no RTSP)...")
        frames = load_frames(Path(args.replay))
    else:
        print(f"grabbing {args.frames} frames off RTSP (stand in front of the camera!)...")
        frames = grab_frames(args.frames, args.interval, Path(args.save_dir))
    print(f"got {len(frames)} frames\n")

    results = []
    no_face = 0
    persons_total = 0
    for fi, bgr in enumerate(frames):
        h, w = bgr.shape[:2]
        # Dynamic OpenVINO IR on the iGPU (validates the re-export).
        det = yolo.predict(bgr, imgsz=640, conf=0.5, verbose=False, device="intel:gpu")[0]
        for b in det.boxes:
            if int(b.cls) != 0:  # person class
                continue
            persons_total += 1
            x1, y1, x2, y2 = (float(v) for v in b.xyxyn[0])
            head = _head_region(bgr, (x1, y1, x2, y2), w, h)
            if head is None:
                continue
            faces = app.get(head)
            if not faces:
                no_face += 1
                continue
            f = _largest(faces)
            aligned = face_align.norm_crop(head, f.kps, image_size=112)
            cos = float(np.dot(_normalize(np.asarray(f.normed_embedding, dtype=np.float32)), centroid))
            fx1, fy1, fx2, fy2 = (int(v) for v in f.bbox)
            results.append(
                {
                    "frame": fi,
                    "det_score": float(f.det_score),
                    "cosine": cos,
                    "face_px": max(fx2 - fx1, fy2 - fy1),
                    "head": head,
                    "aligned": aligned,
                }
            )

    results.sort(key=lambda r: r["cosine"], reverse=True)
    print(f"persons detected: {persons_total} | faces found: {len(results)} | no-face heads: {no_face}\n")
    print(f"{'frame':>5} {'det':>5} {'cosine':>7} {'face_px':>8}")
    for r in results:
        print(f"{r['frame']:>5} {r['det_score']:>5.2f} {r['cosine']:>7.3f} {r['face_px']:>8d}")

    top = results[:12]
    for i, r in enumerate(top):
        tag = f"cos{r['cosine']:.2f}_det{r['det_score']:.2f}_px{r['face_px']}"
        cv2.imwrite(str(OUT / f"live_aligned_{i:02d}_{tag}.png"), r["aligned"])
        cv2.imwrite(str(OUT / f"live_head_{i:02d}_{tag}.png"), r["head"])
    if top:
        cols = min(6, len(top))
        rows = (len(top) + cols - 1) // cols
        sheet = np.zeros((rows * 112, cols * 112, 3), dtype=np.uint8)
        for i, r in enumerate(top):
            ry, cx = divmod(i, cols)
            sheet[ry * 112 : ry * 112 + 112, cx * 112 : cx * 112 + 112] = r["aligned"]
        cv2.imwrite(str(OUT / "live_aligned_sheet.png"), sheet)
        b = results[0]
        print(
            f"\nBEST: cosine={b['cosine']:.3f} det={b['det_score']:.2f} face_px={b['face_px']} "
            f"→ {'MATCH' if b['cosine'] >= 0.5 else 'NO MATCH'} (thr 0.5)"
        )
        print(f"wrote crops + live_aligned_sheet.png to {OUT}")


if __name__ == "__main__":
    main()
