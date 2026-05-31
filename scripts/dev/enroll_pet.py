#!/usr/bin/env python
"""Enroll a pet (DINOv2) embedding into the preprocessor's ActorCache.

Companion to ``enroll_face.py`` / ``enroll_body.py``: produces a
DINOv2 CLS embedding for a household pet (dog/cat) and publishes it
as an ActorEnrollmentEvent (``pet_dinov2_centroid``) over NATS. The
running preprocessor matches live dog/cat detections against it.

Two input modes:

* **Cropped** (default) — each photo is already cropped to the animal.
* **Auto-crop** (``--auto-crop``) — full-frame photos; YOLO11 finds
  the largest cat/dog bbox per photo and crops to it.

Multiple photos -> per-photo embeddings averaged + re-normalized
(cancels pose/lighting noise).

Usage:

    python scripts/dev/enroll_pet.py \
        --actor-id rex --name Rex --kind dog \
        --model /data/kukiihome/models/dinov2_vits14.onnx \
        --photo rex1.jpg --photo rex2.jpg

    python scripts/dev/enroll_pet.py \
        --actor-id mittens --name Mittens --kind cat \
        --model /data/kukiihome/models/dinov2_vits14.onnx \
        --photo-dir ~/mittens/ --auto-crop

Connects to NATS at ``$NATS_URL`` or ``nats://localhost:4222``.
Requires onnxruntime + opencv-python-headless; ``--auto-crop`` also
needs ultralytics.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from nats.aio.client import Client as NATS
from kukiihome_shared.preprocessor import (
    SUBJECT_ACTOR_ENROLLED,
    ActorEnrollmentEvent,
)

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Must match PetConfig defaults — DINOv2 224x224, ImageNet normalize.
_INPUT_SIZE = 224
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# COCO class indices for the YOLO auto-crop: 15=cat, 16=dog.
_COCO_PET_CLASSES = [15, 16]


def _collect_photo_paths(photos: list[str], photo_dirs: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in photos:
        path = Path(p).expanduser().resolve()
        if not path.is_file():
            sys.exit(f"--photo {p}: not a file")
        out.append(path)
    for d in photo_dirs:
        dir_path = Path(d).expanduser().resolve()
        if not dir_path.is_dir():
            sys.exit(f"--photo-dir {d}: not a directory")
        for child in sorted(dir_path.iterdir()):
            if child.suffix.lower() in SUPPORTED_EXTS:
                out.append(child)
    if not out:
        sys.exit("No photos collected. Pass --photo or --photo-dir.")
    return out


def _auto_crop_pet(bgr: np.ndarray) -> np.ndarray | None:
    """YOLO11 → largest cat/dog bbox crop, or None if none found."""
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("--auto-crop requires ultralytics: pip install ultralytics")
    model = getattr(_auto_crop_pet, "_model", None)
    if model is None:
        print("Loading YOLO11n for pet auto-crop ...", flush=True)
        model = YOLO("yolo11n.pt")
        _auto_crop_pet._model = model  # type: ignore[attr-defined]
    results = model.predict(bgr, classes=_COCO_PET_CLASSES, conf=0.5, verbose=False)
    if not results:
        return None
    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return None
    xyxy = boxes.xyxy.cpu().numpy()
    areas = (xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1])
    best = int(np.argmax(areas))
    x1, y1, x2, y2 = xyxy[best].astype(int)
    h, w = bgr.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return bgr[y1:y2, x1:x2]


def _preprocess(crop_bgr: np.ndarray) -> np.ndarray:
    resized = cv2.resize(crop_bgr, (_INPUT_SIZE, _INPUT_SIZE), interpolation=cv2.INTER_CUBIC)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    normed = (rgb.astype(np.float32) / 255.0 - _IMAGENET_MEAN) / _IMAGENET_STD
    return np.transpose(normed, (2, 0, 1))


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n >= 1e-8 else v


def _compute_embedding(photo_paths: list[Path], model_path: str, auto_crop: bool) -> np.ndarray:
    try:
        import onnxruntime as ort
    except ImportError:
        sys.exit("Requires onnxruntime: pip install onnxruntime")
    if not Path(model_path).is_file():
        sys.exit(
            f"Model file not found at {model_path}\n"
            "Run scripts/dev/export_dinov2_onnx.py to produce it."
        )
    print(f"Loading DINOv2 ONNX from {model_path} ...", flush=True)
    session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name

    embeddings: list[np.ndarray] = []
    for path in photo_paths:
        bgr = cv2.imread(str(path))
        if bgr is None:
            print(f"  SKIP {path.name}: cv2 could not decode", flush=True)
            continue
        crop = _auto_crop_pet(bgr) if auto_crop else bgr
        if crop is None:
            print(f"  SKIP {path.name}: no cat/dog detected with conf>=0.5", flush=True)
            continue
        chw = _preprocess(crop)
        out = session.run(None, {input_name: chw[None, :, :, :].astype(np.float32)})[0][0]
        emb = _normalize(out)
        if not np.isfinite(emb).all():
            print(f"  SKIP {path.name}: non-finite embedding", flush=True)
            continue
        embeddings.append(emb)
        print(f"  OK   {path.name}: emb_dim={emb.shape[0]}", flush=True)

    if not embeddings:
        sys.exit("No usable embeddings produced. Enrollment aborted.")
    avg = _normalize(np.mean(np.stack(embeddings, axis=0), axis=0))
    print(f"Averaged {len(embeddings)} embeddings -> final dim={avg.shape[0]}", flush=True)
    return avg


async def _publish_enrollment(
    *, nats_url: str, actor_id: str, name: str, embedding: np.ndarray, role: str | None
) -> None:
    event = ActorEnrollmentEvent(
        actor_id=actor_id,
        action="enrolled",
        name=name,
        role=role,
        pet_dinov2_centroid=tuple(float(x) for x in embedding.tolist()),
    )
    nc = NATS()
    await nc.connect(servers=[nats_url])
    try:
        await nc.publish(SUBJECT_ACTOR_ENROLLED, event.model_dump_json().encode("utf-8"))
        await nc.flush()
        print(
            f"Published pet enrollment for {actor_id} ({name}) on {SUBJECT_ACTOR_ENROLLED}",
            flush=True,
        )
    finally:
        await nc.drain()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--actor-id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument(
        "--kind", choices=("dog", "cat"), required=True, help="Used for the actor role label."
    )
    p.add_argument("--role", default=None)
    p.add_argument(
        "--model",
        default="/data/kukiihome/models/dinov2_vits14.onnx",
        help="Path to the DINOv2 ONNX (must match preprocessor's pet_model_path).",
    )
    p.add_argument("--photo", action="append", default=[])
    p.add_argument("--photo-dir", action="append", default=[])
    p.add_argument("--auto-crop", action="store_true")
    p.add_argument("--nats-url", default=os.environ.get("NATS_URL", "nats://localhost:4222"))
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    paths = _collect_photo_paths(args.photo, args.photo_dir)
    print(f"Enrolling pet actor_id={args.actor_id} ({args.kind}) from {len(paths)} photo(s)")
    embedding = _compute_embedding(paths, args.model, args.auto_crop)
    asyncio.run(
        _publish_enrollment(
            nats_url=args.nats_url,
            actor_id=args.actor_id,
            name=args.name,
            embedding=embedding,
            role=args.role or args.kind,
        )
    )


if __name__ == "__main__":
    main()
