"""Empirical: does tiled detection recover the dog that downsampling misses?

The dog walkthrough (`pool_rex_0922_dog`, 4K) is the corpus's hardest
small-object case: a low, foreshortened, distant dog that YOLO11x at
`imgsz=640` scored ~0.34 (below the 0.5 motion gate → invisible to the pet
pipeline). The maintainer's principle — "never downsample; detect on the
4K, crop from the 4K" — is *literally* tiled detection. This script tests
whether it pays off, on the laptop, with the footage we already have.

For a sample of frames it runs two detectors at a low conf floor and reports
how often (and how confidently) each sees the dog:

  A. full-frame  — `model.predict(frame, imgsz=1280)`  (the interim fix)
  B. tiled       — overlapping native-res tiles, batched, merged
                   (kukiihome_preprocessor.pipelines.tiling)

Caveat baked in: this footage is glare-degraded (camera shooting through a
glass rail, temporary placement). So a *negative* result here is not
conclusive — but a *positive* result (tiling finds the dog more often /
more confidently despite the glare) is strong evidence for the principle.
Re-run on the clean permanent-mount footage to confirm.

Usage:
    python scripts/dev/tiling_eval.py [--stride 80] [--conf 0.15] [--limit 24]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
from kukiihome_preprocessor.pipelines.tiling import Box, compute_tiles, detect_tiled

CORPUS = Path(r"C:/Users/darin_jwxgczt/Kukii-Home/face_debug/corpus/pool_rex_0922_dog")
OUT = Path(r"C:/Users/darin_jwxgczt/Kukii-Home/face_debug/tiling_eval.json")
TILE = 1280
OVERLAP = 0.2


def _results_to_boxes(result, classes: dict[int, str]) -> list[Box]:
    boxes = getattr(result, "boxes", None)
    if boxes is None or boxes.xyxy is None:
        return []
    xyxy = boxes.xyxy.cpu().numpy()
    conf = boxes.conf.cpu().numpy()
    cls = boxes.cls.cpu().numpy().astype(int)
    out: list[Box] = []
    for i in range(len(cls)):
        out.append(
            Box(
                x1=float(xyxy[i][0]),
                y1=float(xyxy[i][1]),
                x2=float(xyxy[i][2]),
                y2=float(xyxy[i][3]),
                conf=float(conf[i]),
                cls=classes.get(int(cls[i]), str(int(cls[i]))),
            )
        )
    return out


def _summarize(label: str, per_frame: list[list[Box]], cls: str) -> dict:
    hits = [max((b.conf for b in bs if b.cls == cls), default=0.0) for bs in per_frame]
    seen = [h for h in hits if h > 0.0]
    return {
        "method": label,
        "frames": len(per_frame),
        f"{cls}_frames_detected": len(seen),
        f"{cls}_detection_rate": round(len(seen) / max(1, len(per_frame)), 3),
        f"{cls}_max_conf": round(max(hits), 3) if hits else 0.0,
        f"{cls}_mean_conf_when_seen": round(sum(seen) / len(seen), 3) if seen else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stride", type=int, default=80)
    ap.add_argument("--conf", type=float, default=0.15)
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--cls", default="dog")
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO("yolo11x.pt")
    classes = model.names  # {idx: name}

    frame_paths = sorted(str(p) for p in CORPUS.glob("frame_*.jpg"))

    def tile_detect_fn(crops: list) -> list[list[Box]]:
        results = model.predict(crops, imgsz=TILE, conf=args.conf, verbose=False)
        return [_results_to_boxes(r, classes) for r in results]

    # ── Phase 1: dense full-frame locate pass (also the full-frame baseline)
    locate = frame_paths[:: args.stride]
    print(f"[tiling_eval] phase 1: full-frame locate over {len(locate)} frames "
          f"(stride={args.stride}, conf={args.conf}, class={args.cls})")
    full_by_path: dict[str, list[Box]] = {}
    t_full = 0.0
    for j, path in enumerate(locate):
        frame = cv2.imread(path)
        if frame is None:
            continue
        t0 = time.perf_counter()
        res = model.predict(frame, imgsz=1280, conf=args.conf, verbose=False)
        t_full += time.perf_counter() - t0
        full_by_path[path] = _results_to_boxes(res[0], classes)
        d = max((b.conf for b in full_by_path[path] if b.cls == args.cls), default=0.0)
        p = max((b.conf for b in full_by_path[path] if b.cls == "person"), default=0.0)
        if (j + 1) % 10 == 0 or d > 0:
            print(f"  locate [{j+1}/{len(locate)}] {Path(path).name} "
                  f"full {args.cls}={d:.2f} person={p:.2f}")

    # ── Phase 2: pick the frames most likely to contain the dog, tile those.
    def dog_conf(path: str) -> float:
        return max((b.conf for b in full_by_path[path] if b.cls == args.cls), default=0.0)

    positives = [p for p in full_by_path if dog_conf(p) > 0.0]
    if positives:
        # Frames where full-frame already sees *something* dog-like — the
        # fair head-to-head (does tiling raise the score above the gate?).
        selected = sorted(positives, key=dog_conf, reverse=True)[: args.limit]
        basis = "full-frame dog-positive frames"
    else:
        # Full-frame saw no dog anywhere → does tiling find one it can't?
        # Sample the middle 60% of the clip where a walkthrough subject is
        # most likely present.
        paths = sorted(full_by_path)
        lo, hi = int(len(paths) * 0.2), int(len(paths) * 0.8)
        mid = paths[lo:hi] or paths
        step = max(1, len(mid) // max(1, args.limit))
        selected = mid[::step][: args.limit]
        basis = "even spread (full-frame found no dog anywhere)"
    print(f"\n[tiling_eval] phase 2: tiling {len(selected)} frames — basis: {basis}")

    full_per_frame: list[list[Box]] = []
    tiled_per_frame: list[list[Box]] = []
    t_tiled = 0.0
    for i, path in enumerate(sorted(selected)):
        frame = cv2.imread(path)
        if frame is None:
            continue
        n_tiles = len(compute_tiles(frame.shape[1], frame.shape[0], tile=TILE, overlap=OVERLAP))
        full_boxes = full_by_path[path]
        t0 = time.perf_counter()
        tiled_boxes = detect_tiled(frame, tile_detect_fn, tile=TILE, overlap=OVERLAP)
        t_tiled += time.perf_counter() - t0
        full_per_frame.append(full_boxes)
        tiled_per_frame.append(tiled_boxes)
        fd = max((b.conf for b in full_boxes if b.cls == args.cls), default=0.0)
        td = max((b.conf for b in tiled_boxes if b.cls == args.cls), default=0.0)
        print(f"  [{i+1}/{len(selected)}] {Path(path).name} "
              f"tiles={n_tiles}  full {args.cls}={fd:.2f}  tiled {args.cls}={td:.2f}")

    n = max(1, len(full_per_frame))
    n_locate = max(1, len(full_by_path))
    report = {
        "corpus": CORPUS.name,
        "class": args.cls,
        "conf_floor": args.conf,
        "tile": TILE,
        "overlap": OVERLAP,
        "locate_frames": len(full_by_path),
        "locate_dog_detection_rate": round(
            sum(1 for p in full_by_path if dog_conf(p) > 0) / n_locate, 3
        ),
        "selection_basis": basis,
        "frames_evaluated": len(full_per_frame),
        "full_frame": {**_summarize("full_frame@1280", full_per_frame, args.cls),
                       "avg_ms": round(1000 * t_full / n_locate, 1)},
        "tiled": {**_summarize("tiled@native", tiled_per_frame, args.cls),
                  "avg_ms": round(1000 * t_tiled / n, 1)},
    }
    OUT.write_text(json.dumps(report, indent=2))
    print("\n=== SUMMARY ===")
    print(json.dumps(report["full_frame"], indent=2))
    print(json.dumps(report["tiled"], indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
