#!/usr/bin/env python
"""Benchmark YOLO detection: OpenVINO iGPU (FP16) vs PyTorch CPU.

Grabs one real 4K frame off the Dahua main stream via PyAV, then times
the exact two paths the preprocessor can run:

  * OpenVINO IR on device GPU (Intel Iris Plus iGPU), FP16
  * PyTorch .pt on CPU  (the current baseline)

Reports per-frame latency (first run separated — it includes the
one-time model compile/warmup). Apples-to-apples: same frame, same
imgsz, same conf, just different inference backend/device.
"""

from __future__ import annotations

import time

import av
import numpy as np

RTSP = "rtsp://admin:J9v%258emo@192.168.68.89:554/cam/realmonitor?channel=1&subtype=0"
OV_MODEL = "C:/Users/darin_jwxgczt/SentiHome/yolo11x_openvino_model"
PT_MODEL = "C:/Users/darin_jwxgczt/SentiHome/yolo11x.pt"
IMGSZ = 640
CONF = 0.5
N = 10


def grab_frame() -> np.ndarray:
    container = av.open(RTSP, options={"rtsp_transport": "tcp"}, timeout=15)
    try:
        for frame in container.decode(video=0):
            bgr = frame.to_ndarray(format="bgr24")
            return bgr
    finally:
        container.close()
    raise RuntimeError("no frame decoded")


def bench(model, device_label: str, bgr: np.ndarray) -> None:
    # Warmup / compile (first call on iGPU compiles the IR — exclude it).
    t0 = time.perf_counter()
    model.predict(bgr, imgsz=IMGSZ, conf=CONF, verbose=False)
    warm = (time.perf_counter() - t0) * 1000.0

    times = []
    for _ in range(N):
        t0 = time.perf_counter()
        r = model.predict(bgr, imgsz=IMGSZ, conf=CONF, verbose=False)
        times.append((time.perf_counter() - t0) * 1000.0)
    n_det = len(r[0].boxes)
    arr = np.array(times)
    print(
        f"{device_label:>22}: warmup={warm:8.1f}ms | "
        f"steady mean={arr.mean():7.1f}ms  min={arr.min():7.1f}ms  "
        f"max={arr.max():7.1f}ms  (n={N}, dets={n_det})"
    )


def main() -> None:
    from ultralytics import YOLO

    print("grabbing one 4K frame off the main stream...")
    bgr = grab_frame()
    print(f"frame shape = {bgr.shape}\n")

    print("loading OpenVINO IR (device=GPU)...")
    ov_model = YOLO(OV_MODEL, task="detect")
    # ultralytics passes device through to the OpenVINO backend.
    bench_ov(ov_model, bgr)

    print("\nloading PyTorch .pt (device=cpu)...")
    pt_model = YOLO(PT_MODEL)
    bench(pt_model, "PyTorch CPU", bgr)


def bench_ov(model, bgr: np.ndarray) -> None:
    # ultralytics OpenVINO backend selects device via the `device` kwarg
    # on predict; "intel:gpu" targets the iGPU.
    t0 = time.perf_counter()
    model.predict(bgr, imgsz=IMGSZ, conf=CONF, verbose=False, device="intel:gpu")
    warm = (time.perf_counter() - t0) * 1000.0
    times = []
    for _ in range(N):
        t0 = time.perf_counter()
        r = model.predict(bgr, imgsz=IMGSZ, conf=CONF, verbose=False, device="intel:gpu")
        times.append((time.perf_counter() - t0) * 1000.0)
    arr = np.array(times)
    print(
        f"{'OpenVINO iGPU (FP16)':>22}: warmup={warm:8.1f}ms | "
        f"steady mean={arr.mean():7.1f}ms  min={arr.min():7.1f}ms  "
        f"max={arr.max():7.1f}ms  (n={N}, dets={len(r[0].boxes)})"
    )


if __name__ == "__main__":
    main()
