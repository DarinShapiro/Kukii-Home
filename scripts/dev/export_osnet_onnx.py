#!/usr/bin/env python
"""Export an OSNet model to ONNX for the preprocessor's body re-ID pipeline.

The runtime body-ID pipeline (``pipelines/body_id.py``) consumes a
pre-exported ONNX file via onnxruntime — same shape as the YOLO
OpenVINO export. This script is the one-time producer.

Usage:

    # Default: osnet_x1_0 with ImageNet-pretrained weights (the
    # cheapest baseline, decent quality, ~8MB). Writes to the
    # standard preprocessor path.
    python scripts/dev/export_osnet_onnx.py

    # MSMT17-finetuned for production-grade ReID. Download the
    # checkpoint from torchreid's model zoo first:
    #   https://kaiyangzhou.github.io/deep-person-reid/MODEL_ZOO.html
    # then point at it:
    python scripts/dev/export_osnet_onnx.py \
        --weights ~/Downloads/osnet_x1_0_msmt17.pth \
        --output /data/sentihome/models/osnet_x1_0_msmt17.onnx

    # Smaller / faster variant — osnet_x0_25 (~1MB).
    python scripts/dev/export_osnet_onnx.py --model osnet_x0_25

Requires torchreid + its transitive deps installed (gdown +
tensorboard get pulled by torchreid's package __init__):

    pip install torchreid gdown tensorboard onnxscript onnx

These are dev-time deps — the runtime preprocessor only needs
onnxruntime to consume the resulting .onnx.

Why ImageNet-pretrained as the default: it's what loads with a
single ``pretrained=True`` flag (no manual download), and it works
well enough to validate the end-to-end pipeline. Swap to MSMT17
weights when you want better cross-camera generalization.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import torch

_DEFAULT_OUTPUT = Path("/data/sentihome/models/osnet_x1_0.onnx")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--model",
        default="osnet_x1_0",
        choices=("osnet_x1_0", "osnet_x0_75", "osnet_x0_5", "osnet_x0_25"),
        help="OSNet variant. Larger = higher accuracy, larger file.",
    )
    p.add_argument(
        "--weights",
        default=None,
        help=(
            "Optional path to a .pth checkpoint (e.g. MSMT17-finetuned). "
            "When omitted, downloads ImageNet-pretrained weights via "
            "torchreid (Google Drive)."
        ),
    )
    p.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT),
        help=f"Output ONNX path. Default: {_DEFAULT_OUTPUT}",
    )
    p.add_argument(
        "--input-height",
        type=int,
        default=256,
        help="OSNet input height. 256 is the trained default.",
    )
    p.add_argument(
        "--input-width",
        type=int,
        default=128,
        help="OSNet input width. 128 is the trained default.",
    )
    p.add_argument(
        "--opset",
        type=int,
        default=14,
        help="ONNX opset version. 14+ has the ops OSNet needs.",
    )
    return p.parse_args()


def _build_model(model_name: str, weights_path: str | None):
    """Construct the OSNet model, loading the requested weights.

    With ``weights_path=None``: torchreid downloads ImageNet weights
    on first call (cached at ``~/.cache/torch/checkpoints/``).
    With a path: loads the .pth checkpoint via torch.load + accepts
    minor key mismatches (the classifier head is dropped — we want
    embeddings, not class logits).
    """
    try:
        from torchreid.reid.models.osnet import (  # type: ignore[import-not-found]
            osnet_x0_5,
            osnet_x0_25,
            osnet_x0_75,
            osnet_x1_0,
        )
    except ImportError as e:
        sys.exit(
            f"Failed to import torchreid: {e}\n"
            "Install the dev deps:\n"
            "    pip install torchreid gdown tensorboard onnxscript onnx"
        )

    factories = {
        "osnet_x1_0": osnet_x1_0,
        "osnet_x0_75": osnet_x0_75,
        "osnet_x0_5": osnet_x0_5,
        "osnet_x0_25": osnet_x0_25,
    }
    factory = factories[model_name]

    if weights_path is None:
        # torchreid downloads ImageNet weights via gdown on first
        # use. Subsequent calls hit the cache.
        model = factory(pretrained=True)
    else:
        # Manual checkpoint (e.g. MSMT17-finetuned). Load the
        # state_dict and let load_state_dict drop the classifier
        # head — we only care about the embedding output.
        model = factory(pretrained=False)
        state = torch.load(weights_path, map_location="cpu", weights_only=True)
        # torchreid checkpoints sometimes wrap state_dict in a
        # top-level dict.
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        # Drop the classifier head (different dim per training set).
        state = {k: v for k, v in state.items() if not k.startswith("classifier.")}
        missing, unexpected = model.load_state_dict(state, strict=False)
        if unexpected:
            print(f"WARN unexpected keys in checkpoint: {unexpected[:5]}...")
        if missing:
            print(f"WARN missing keys (acceptable for embedding-only): {missing[:5]}...")

    model.eval()
    return model


def _export_onnx(
    model,
    output_path: Path,
    height: int,
    width: int,
    opset: int,
) -> None:
    """torch.onnx.export with a dynamic batch axis so the runtime
    can feed N person crops in one session.run."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.randn(1, 3, height, width)

    # Force the legacy TorchScript-based export. The new dynamo
    # exporter has trouble with some OSNet ops as of torch 2.7.
    torch.onnx.export(
        model,
        dummy,
        str(output_path),
        input_names=["input"],
        output_names=["embedding"],
        dynamic_axes={
            "input": {0: "batch"},
            "embedding": {0: "batch"},
        },
        opset_version=opset,
        dynamo=False,
    )


def _validate_onnx(output_path: Path, height: int, width: int) -> None:
    """Smoke test: load the exported ONNX, run a batch of 3, sanity-
    check the shape + finiteness. Catches export bugs early."""
    try:
        import numpy as np
        import onnxruntime as ort  # type: ignore[import-not-found]
    except ImportError as e:
        print(f"WARN cannot validate (missing dep): {e}")
        return

    session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    batch = np.random.randn(3, 3, height, width).astype(np.float32)
    out = session.run(None, {"input": batch})[0]
    assert out.shape == (3, 512), f"expected (3, 512), got {out.shape}"
    assert np.isfinite(out).all(), "embedding contains non-finite values"
    print(f"OK validated: batch=3 -> shape {out.shape}, finite values.")


def main() -> None:
    args = _parse_args()
    print(f"Building model: {args.model} (weights={args.weights or 'ImageNet'})")
    model = _build_model(args.model, args.weights)

    output = Path(args.output)
    print(f"Exporting to {output} ...")
    _export_onnx(model, output, args.input_height, args.input_width, args.opset)
    size_mb = output.stat().st_size / 1024 / 1024
    print(f"Wrote {output} ({size_mb:.2f} MB)")

    _validate_onnx(output, args.input_height, args.input_width)
    print(
        "Done. Point the preprocessor at this file via:\n"
        f"    SENTIHOME_PREPROCESSOR_BODY_ID=true\n"
        f"    SENTIHOME_PREPROCESSOR_BODY_ID_MODEL_PATH={output}"
    )


if __name__ == "__main__":
    # Set utf-8 stdout so torchreid's print() doesn't crash on
    # cp1252 consoles (Windows default). Same trick we use in
    # publish_camera_config.py.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass
    main()
