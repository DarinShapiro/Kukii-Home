#!/usr/bin/env python
"""Export a YOLO model to OpenVINO IR for fast Intel-hardware inference.

Run once per (model, quantization-choice) combination. Produces a
directory like ``yolo11x_openvino_model/`` next to the .pt file
containing the OpenVINO IR (an ``.xml`` topology + ``.bin`` weights
+ ultralytics metadata). The preprocessor's YOLODetector loads that
directory when configured with ``backend="openvino"``.

Why a separate export step instead of doing it at first-use time:
* Export is slow (~1-2 min for yolo11x + calibration if INT8).
* INT8 quantization needs a calibration dataset — ultralytics uses
  COCO128 by default, which is downloaded on demand. We don't want
  the preprocessor service to do this at boot.
* The IR directory is portable: export once on a dev box, copy to
  the production host. The .pt file isn't needed at runtime.

Usage:
    # FP16 — fast to export, ~2x speedup over PyTorch CPU on Intel:
    python scripts/dev/export_yolo_openvino.py --weights yolo11x.pt

    # INT8 — slower to export (needs calibration), bigger speedup
    # (~5-10x over PyTorch CPU on Intel CPUs/iGPUs), ~1-2% mAP loss:
    python scripts/dev/export_yolo_openvino.py --weights yolo11x.pt --int8

Output directory is printed; set KUKIIHOME_PREPROCESSOR_DETECTION_WEIGHTS
to that path and KUKIIHOME_PREPROCESSOR_DETECTION_BACKEND=openvino
to actually use it at runtime.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--weights",
        default="yolo11x.pt",
        help="Source .pt model (name or path). Auto-downloads if not present.",
    )
    parser.add_argument(
        "--int8",
        action="store_true",
        help="Quantize to INT8 (smaller, faster, ~1-2 percent mAP loss).",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Input image size to bake into the IR (default 640).",
    )
    parser.add_argument(
        "--half",
        action="store_true",
        help="FP16 export (smaller than FP32, faster on Intel iGPU).",
    )
    args = parser.parse_args()

    # Lazy import — keeps `--help` snappy.
    try:
        from ultralytics import YOLO  # type: ignore[import-not-found]
    except ImportError:
        print("ERROR: ultralytics not installed. `pip install ultralytics`.")
        return 1

    print(f"loading source weights: {args.weights}")
    model = YOLO(args.weights)

    t0 = time.perf_counter()
    print(f"exporting -> openvino  imgsz={args.imgsz} int8={args.int8} half={args.half}...")
    out_path = model.export(
        format="openvino",
        imgsz=args.imgsz,
        int8=args.int8,
        half=args.half,
        # NMS on/off is debated for OpenVINO; default off so the
        # detector sees raw YOLO output + applies its own NMS,
        # matching the pytorch path.
        nms=False,
    )
    elapsed = time.perf_counter() - t0

    # ultralytics returns the path to the generated directory.
    out_dir = Path(out_path)
    if out_dir.is_dir():
        files = sorted(out_dir.rglob("*"))
        total_bytes = sum(f.stat().st_size for f in files if f.is_file())
    else:
        files = []
        total_bytes = 0

    print(
        f"\nexport complete in {elapsed:.1f}s -> {out_dir}\n"
        f"  files: {len(files)}\n"
        f"  total size: {total_bytes / (1024 * 1024):.1f} MiB"
    )
    print("\nUse this model at runtime:")
    print(
        f"  KUKIIHOME_PREPROCESSOR_DETECTION=true \\\n"
        f"  KUKIIHOME_PREPROCESSOR_DETECTION_BACKEND=openvino \\\n"
        f"  KUKIIHOME_PREPROCESSOR_DETECTION_WEIGHTS={out_dir} \\\n"
        f"  KUKIIHOME_PREPROCESSOR_DETECTION_DEVICE=AUTO    # or GPU / CPU"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
