#!/usr/bin/env python
"""Export an AIM-CCReID (CAL-family) cloth-changing ReID model to ONNX.

Clothing-robust body identification (Epic 10.11.5). Unlike OSNet —
which keys heavily on clothing colour/texture and fails across outfit
changes — CAL/AIM models are trained with a clothes-adversarial loss
on LTCC/PRCC to extract clothes-*irrelevant* features (body shape,
build, structure). The runtime body pipeline can consume the resulting
ONNX exactly like OSNet (crop -> embedding -> cosine), as a durable,
outfit-stable body template.

Source: AIM-CCReID (CVPR 2023), https://github.com/boomshakay/aim-ccreid
Backbone: torchvision ResNet50, last-stride-1, MaxAvg pooling ->
4096-d feature + BN. forward() returns (feature_map, feature); we
export the L2-normalized feature (what demo_single_image.py uses).
Input: 384x192 (HxW), ImageNet normalize, RGB.

Requires the cloned repo on disk (for its model definition) + a
checkpoint from its Google Drive (ltcc.pth.tar / prcc.pth.tar):

    python scripts/dev/export_ccreid_onnx.py \\
        --repo C:/Users/darin_jwxgczt/ccreid_work \\
        --weights C:/Users/darin_jwxgczt/ccreid_work/weights/ltcc.pth.tar \\
        --output C:/Users/darin_jwxgczt/SentiHome/models/ccreid_cal_ltcc.onnx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import torch
import torch.nn.functional as F


class _NormalizedFeature(torch.nn.Module):
    """Single-output wrapper: ResNet50 forward returns (map, feat); we
    want only the L2-normalized feature for matching/export."""

    def __init__(self, backbone: torch.nn.Module) -> None:
        super().__init__()
        self.backbone = backbone

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _map, feat = self.backbone(x)
        return F.normalize(feat, p=2, dim=1)


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, help="Path to cloned aim-ccreid repo")
    p.add_argument("--weights", required=True, help="Path to .pth.tar checkpoint")
    p.add_argument("--output", required=True, help="Output ONNX path")
    p.add_argument("--height", type=int, default=384)
    p.add_argument("--width", type=int, default=192)
    p.add_argument("--feature-dim", type=int, default=4096)
    p.add_argument("--opset", type=int, default=14)
    return p.parse_args()


def main() -> int:
    args = _parse()
    sys.path.insert(0, str(Path(args.repo).resolve()))
    try:
        from models.img_resnet import ResNet50  # type: ignore[import-not-found]
    except ImportError as e:
        print(f"ERROR importing model from --repo: {e}")
        return 1

    # Minimal config object exposing only what ResNet50.__init__ reads.
    config = SimpleNamespace(
        MODEL=SimpleNamespace(
            RES4_STRIDE=1,
            FEATURE_DIM=args.feature_dim,
            POOLING=SimpleNamespace(NAME="maxavg", P=3),
        )
    )

    print("building ResNet50 (last-stride-1, maxavg) ...")
    model = ResNet50(config)
    ckpt = torch.load(args.weights, map_location="cpu", weights_only=True)
    state = ckpt.get("model_state_dict", ckpt)
    missing, unexpected = model.load_state_dict(state, strict=False)
    print(f"loaded checkpoint: missing={len(missing)} unexpected={len(unexpected)}")
    if missing:
        print("  missing keys (first 5):", missing[:5])
    if unexpected:
        print("  unexpected keys (first 5):", unexpected[:5])

    wrapped = _NormalizedFeature(model).eval()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.randn(1, 3, args.height, args.width)
    print(f"exporting -> {out_path} (input 1x3x{args.height}x{args.width}) ...")
    torch.onnx.export(
        wrapped,
        dummy,
        str(out_path),
        input_names=["input"],
        output_names=["embedding"],
        dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=args.opset,
        dynamo=False,
    )

    # Validate.
    import numpy as np
    import onnxruntime as ort

    sess = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
    batch = np.random.randn(3, 3, args.height, args.width).astype(np.float32)
    emb = sess.run(None, {"input": batch})[0]
    norms = np.linalg.norm(emb, axis=1)
    ok = emb.shape == (3, args.feature_dim) and np.isfinite(emb).all()
    print(
        f"OK validated: shape={emb.shape} finite={np.isfinite(emb).all()} "
        f"L2norms~{norms.round(3).tolist()} ({out_path.stat().st_size / 1e6:.1f} MB)"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
