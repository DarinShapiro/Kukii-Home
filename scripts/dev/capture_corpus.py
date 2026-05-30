#!/usr/bin/env python
"""Capture a dense-fps frame corpus straight off RTSP (no inference).

For gait (needs a dense ~native-fps sequence of a steady walk) and
cross-outfit re-ID validation. Unlike live_face_probe's 1fps interval
grab, this saves EVERY decoded frame for a fixed duration so the gait
cycle is captured. No model loading -> fast -> doesn't drop frames.

Usage:
    python scripts/dev/capture_corpus.py --name walk2_outfit2 --seconds 15
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import av
import cv2

RTSP = "rtsp://admin:J9v%258emo@192.168.68.89:554/cam/realmonitor?channel=1&subtype=0"
CORPUS_ROOT = Path("C:/Users/darin_jwxgczt/SentiHome/face_debug/corpus")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="corpus subdir name")
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--max-frames", type=int, default=600)
    args = ap.parse_args()

    out = CORPUS_ROOT / args.name
    out.mkdir(parents=True, exist_ok=True)

    print("opening RTSP main stream ...", flush=True)
    container = av.open(RTSP, options={"rtsp_transport": "tcp"}, timeout=15)
    n = 0
    t_start = time.perf_counter()
    first_ts = None
    try:
        for frame in container.decode(video=0):
            bgr = frame.to_ndarray(format="bgr24")
            cv2.imwrite(str(out / f"frame_{n:04d}.jpg"), bgr)
            n += 1
            now = time.perf_counter()
            if first_ts is None:
                first_ts = now
            if n % 15 == 0:
                print(f"  {n} frames ({now - t_start:.1f}s)", flush=True)
            if now - t_start >= args.seconds or n >= args.max_frames:
                break
    finally:
        container.close()

    elapsed = time.perf_counter() - t_start
    fps = n / elapsed if elapsed > 0 else 0.0
    print(f"saved {n} frames to {out} in {elapsed:.1f}s (~{fps:.1f} fps)", flush=True)


if __name__ == "__main__":
    main()
