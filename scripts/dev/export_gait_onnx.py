#!/usr/bin/env python
"""Export OpenGait GaitBase to ONNX (Epic #101).

GaitBase (the `Baseline` model) is the officially-recommended CNN gait
baseline. Its repo `BaseModel.__init__` is training-framework-coupled
(builds dataloaders, calls torch.cuda.set_device + .to(cuda)
unconditionally), so it can't be instantiated on a CPU box. This script
sidesteps that with a STANDALONE nn.Module that reimplements GaitBase's
build_network + forward using the repo's OWN modules (ResNet9,
SetBlockWrapper, PackSequenceWrapper, HorizontalPoolingPyramid,
SeparateFCs, SeparateBNNecks), then loads the published checkpoint by
key — so weights are identical to the trained model.

Architecture (from configs/gaitbase/gaitbase_da_gait3d.yaml):
  sils [N,S,64,44] -> unsqueeze C=1 -> SetBlockWrapper(ResNet9) [N,512,S,h,w]
  -> temporal max over S (PackSequenceWrapper(torch.max), seqL=None)
  -> HPP bin_num=[16] -> [N,512,16]
  -> SeparateFCs(512->256, 16) -> embed [N,256,16]
The inference descriptor is embed_1 (pre-BNNeck), flattened to [N, 4096].

Input: silhouettes 64x44, values in [0,1]. Dynamic batch + sequence axes.

Usage:
    python scripts/dev/export_gait_onnx.py \\
        --repo C:/Users/darin_jwxgczt/opengait_work \\
        --weights C:/Users/.../GaitBase_DA-60000.pt \\
        --output C:/Users/darin_jwxgczt/SentiHome/models/gaitbase_gait3d.onnx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch
from torch import nn


class GaitBaseStandalone(nn.Module):
    """GaitBase inference graph, framework-free. Module attribute names
    (Backbone/FCs/BNNecks) match the repo's `Baseline` so a checkpoint
    saved from the trained model loads by key."""

    def __init__(self, repo: str, class_num: int = 20000) -> None:
        super().__init__()
        sys.path.insert(0, str(Path(repo) / "opengait"))
        from modeling.backbones.resnet import ResNet9  # type: ignore[import-not-found]
        from modeling.modules import (  # type: ignore[import-not-found]
            HorizontalPoolingPyramid,
            PackSequenceWrapper,
            SeparateBNNecks,
            SeparateFCs,
            SetBlockWrapper,
        )

        backbone = ResNet9(
            block="BasicBlock",
            channels=[64, 128, 256, 512],
            in_channel=1,
            layers=[1, 1, 1, 1],
            strides=[1, 2, 2, 1],
            maxpool=False,
        )
        self.Backbone = SetBlockWrapper(backbone)
        self.FCs = SeparateFCs(parts_num=16, in_channels=512, out_channels=256)
        # class_num only sizes the (unused-at-inference) classifier head;
        # it differs per training set (Gait3D=3000, GREW=20000). Match the
        # checkpoint so it loads; the embedding (embed_1) is pre-BNNeck.
        self.BNNecks = SeparateBNNecks(parts_num=16, in_channels=256, class_num=class_num)
        self.TP = PackSequenceWrapper(torch.max)
        self.HPP = HorizontalPoolingPyramid(bin_num=[16])

    def forward(self, sils: torch.Tensor) -> torch.Tensor:
        # sils: [N, S, H, W] -> add channel -> [N, C=1, S, H, W]
        x = sils.unsqueeze(1)
        outs = self.Backbone(x)  # [N, 512, S, h, w]
        # temporal pooling over the sequence dim (dim=2); seqL=None ->
        # pool the whole clip (export = one clip at a time).
        outs = self.TP(outs, seqL=None, options={"dim": 2})[0]  # [N, 512, h, w]
        feat = self.HPP(outs)  # [N, 512, 16]
        embed_1 = self.FCs(feat)  # [N, 256, 16]
        # inference descriptor is embed_1; flatten parts -> [N, 4096]
        return embed_1.flatten(1)


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, help="Path to cloned OpenGait repo")
    p.add_argument("--weights", required=True, help="GaitBase checkpoint .pt")
    p.add_argument("--output", required=True)
    p.add_argument(
        "--class-num",
        type=int,
        default=20000,
        help="classifier head size of the checkpoint (GREW=20000, Gait3D=3000); "
        "head is unused at inference, just needs to match for loading",
    )
    p.add_argument("--opset", type=int, default=14)
    return p.parse_args()


def main() -> int:
    args = _parse()
    model = GaitBaseStandalone(args.repo, class_num=args.class_num)

    ckpt = torch.load(args.weights, map_location="cpu", weights_only=False)
    # OpenGait saves {'model': state_dict, ...}; tolerate raw state_dict too.
    state = ckpt.get("model", ckpt) if isinstance(ckpt, dict) else ckpt
    # Keep only keys present AND shape-matching (drops training-only keys
    # and any classifier-head size diff that doesn't affect the embedding).
    own = model.state_dict()
    filtered = {k: v for k, v in state.items() if k in own and v.shape == own[k].shape}
    missing, _unexpected = model.load_state_dict(filtered, strict=False)
    skipped = [k for k in state if k not in own or state[k].shape != own[k].shape]
    print(f"checkpoint keys: own={len(own)} ckpt={len(state)} loaded={len(filtered)}")
    print(f"  missing={len(missing)} skipped_from_ckpt={len(skipped)} {skipped[:4]}")
    if missing:
        print("  MISSING (first 8):", list(missing)[:8])
    model.eval()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.rand(1, 16, 64, 44)  # [N, S, H, W]
    print(f"exporting -> {out_path} (input [N,S,64,44]) ...")
    torch.onnx.export(
        model,
        dummy,
        str(out_path),
        input_names=["sils"],
        output_names=["embedding"],
        dynamic_axes={"sils": {0: "batch", 1: "seq"}, "embedding": {0: "batch"}},
        opset_version=args.opset,
        dynamo=False,
    )

    # Validate: ONNX runs, output finite, and matches torch within tol;
    # different seq lengths both work (dynamic S).
    import numpy as np
    import onnxruntime as ort

    sess = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
    for s in (16, 30):
        x = np.random.rand(2, s, 64, 44).astype(np.float32)
        onx = sess.run(None, {"sils": x})[0]
        with torch.no_grad():
            ten = model(torch.from_numpy(x)).numpy()
        max_diff = float(np.abs(onx - ten).max())
        print(
            f"  S={s}: onnx{onx.shape} finite={np.isfinite(onx).all()} "
            f"max|onnx-torch|={max_diff:.2e}"
        )
        if not (np.isfinite(onx).all() and max_diff < 1e-3):
            print("  VALIDATION FAILED")
            return 1
    print(f"OK exported + validated ({out_path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
