#!/usr/bin/env python
"""Export a DINOv2 backbone to ONNX for the preprocessor's pet pipeline.

Mirrors ``export_osnet_onnx.py``. The runtime pet pipeline
(``pipelines/pet.py``) consumes the resulting ONNX via onnxruntime
and uses the CLS-token embedding as the per-animal descriptor.

Usage:

    # Default: dinov2_vits14 (384-d embedding, ~88MB). Writes to the
    # standard preprocessor path.
    python scripts/dev/export_dinov2_onnx.py

    # Larger backbones (higher accuracy, larger files):
    python scripts/dev/export_dinov2_onnx.py --model dinov2_vitb14  # 768-d
    python scripts/dev/export_dinov2_onnx.py --model dinov2_vitl14  # 1024-d

Requires torch + the DINOv2 hub deps (downloaded on first use):

    pip install torch onnx onnxscript

These are dev-time deps — the runtime preprocessor only needs
onnxruntime to consume the .onnx.

NOTE: the DINOv2 weights download from torch.hub on first run; this
needs network access. Fixed 224x224 input (DINOv2 patch size 14 →
16x16 patches) avoids the dynamic-positional-encoding paths that
trip up ONNX export, so only the batch axis is dynamic.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import torch

_DEFAULT_OUTPUT = Path("/data/sentihome/models/dinov2_vits14.onnx")
_EMBED_DIM = {
    "dinov2_vits14": 384,
    "dinov2_vitb14": 768,
    "dinov2_vitl14": 1024,
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--model",
        default="dinov2_vits14",
        choices=tuple(_EMBED_DIM),
        help="DINOv2 backbone. Larger = higher accuracy, larger file.",
    )
    p.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT),
        help=f"Output ONNX path. Default: {_DEFAULT_OUTPUT}",
    )
    p.add_argument(
        "--input-size",
        type=int,
        default=224,
        help="Square input size; must be a multiple of 14. 224 is the cheap default.",
    )
    p.add_argument("--opset", type=int, default=14)
    return p.parse_args()


def _build_model(model_name: str):
    """Load the DINOv2 backbone from torch.hub. forward(x) returns
    the CLS-token embedding (B, embed_dim)."""
    try:
        model = torch.hub.load(
            "facebookresearch/dinov2", model_name, pretrained=True, verbose=False
        )
    except Exception as e:
        sys.exit(
            f"Failed to load {model_name} from torch.hub: {e}\n"
            "Needs network on first run (downloads the weights). "
            "Install deps: pip install torch onnx onnxscript"
        )
    model.eval()
    return model


def _export_onnx(model, output_path: Path, size: int, opset: int) -> None:
    if size % 14 != 0:
        sys.exit(f"--input-size must be a multiple of 14 (DINOv2 patch size); got {size}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.randn(1, 3, size, size)
    torch.onnx.export(
        model,
        dummy,
        str(output_path),
        input_names=["input"],
        output_names=["embedding"],
        dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=opset,
        dynamo=False,
    )


def _validate_onnx(output_path: Path, size: int, expected_dim: int) -> None:
    try:
        import numpy as np
        import onnxruntime as ort  # type: ignore[import-not-found]
    except ImportError as e:
        print(f"WARN cannot validate (missing dep): {e}")
        return
    session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    batch = np.random.randn(3, 3, size, size).astype(np.float32)
    out = session.run(None, {"input": batch})[0]
    assert out.shape == (3, expected_dim), f"expected (3, {expected_dim}), got {out.shape}"
    assert np.isfinite(out).all(), "embedding contains non-finite values"
    print(f"OK validated: batch=3 -> shape {out.shape}, finite values.")


def main() -> None:
    args = _parse_args()
    print(f"Building model: {args.model}")
    model = _build_model(args.model)

    output = Path(args.output)
    print(f"Exporting to {output} ...")
    _export_onnx(model, output, args.input_size, args.opset)
    size_mb = output.stat().st_size / 1024 / 1024
    print(f"Wrote {output} ({size_mb:.2f} MB)")

    _validate_onnx(output, args.input_size, _EMBED_DIM[args.model])
    print(
        "Done. Point the preprocessor at this file via:\n"
        "    SENTIHOME_PREPROCESSOR_PET=true\n"
        f"    SENTIHOME_PREPROCESSOR_PET_MODEL_PATH={output}"
    )


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass
    main()
