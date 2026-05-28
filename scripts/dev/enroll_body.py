#!/usr/bin/env python
"""Enroll a body re-ID embedding into the preprocessor's ActorCache.

Companion to ``enroll_face.py``: same shape, but produces an OSNet
512-d body embedding instead of an ArcFace face embedding.

Two input modes:

* **Cropped mode** (default) — each photo is already cropped to the
  person, no background. OSNet runs directly on the resized image.
* **Auto-crop mode** (``--auto-crop``) — full-frame photos; the
  script runs YOLO11 to find the largest person bbox in each photo
  and crops to it. Handy when the operator hasn't pre-trimmed the
  reference photos.

Multiple reference photos -> per-photo embeddings are averaged and
re-normalized. Same trick as face enrollment: the average across
2-3 angles cancels out lighting / pose noise.

Usage:

    # Cropped photos (preferred — operator controls the crop):
    python scripts/dev/enroll_body.py \
        --actor-id alice \
        --name Alice \
        --model /data/sentihome/models/osnet_x1_0.onnx \
        --photo alice_body_1.jpg --photo alice_body_2.jpg

    # Or full-frame photos with YOLO auto-crop:
    python scripts/dev/enroll_body.py \
        --actor-id alice --name Alice \
        --model /data/sentihome/models/osnet_x1_0.onnx \
        --photo-dir /path/to/alice_full_frames/ \
        --auto-crop

Connects to NATS at ``$NATS_URL`` or ``nats://localhost:4222``.

Requires onnxruntime + opencv-python-headless (runtime preprocessor
deps). For ``--auto-crop`` also requires ultralytics.
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
from sentihome_shared.preprocessor import (
    SUBJECT_ACTOR_ENROLLED,
    ActorEnrollmentEvent,
)

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Must match BodyIdConfig defaults — OSNet was trained at 256x128.
_OSNET_H = 256
_OSNET_W = 128

_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


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


def _auto_crop_person(bgr: np.ndarray) -> np.ndarray | None:
    """Run YOLO to find the largest person bbox in a full-frame
    photo. Returns the cropped pixels (BGR), or None if no person
    detected with confidence >= 0.5."""
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("--auto-crop requires ultralytics: pip install ultralytics")

    # Cache the model on the module so multiple photos don't reload.
    model = getattr(_auto_crop_person, "_model", None)
    if model is None:
        print("Loading YOLO11n for person auto-crop ...", flush=True)
        model = YOLO("yolo11n.pt")
        _auto_crop_person._model = model  # type: ignore[attr-defined]

    results = model.predict(bgr, classes=[0], conf=0.5, verbose=False)
    if not results:
        return None
    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return None
    # Pick the largest-area box (most likely the subject).
    xyxy = boxes.xyxy.cpu().numpy()  # shape (N, 4)
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
    """OSNet-standard preprocessing — must match BodyIdRecognizer's
    pipeline exactly or the enrolled embeddings won't match live
    inference."""
    resized = cv2.resize(crop_bgr, (_OSNET_W, _OSNET_H), interpolation=cv2.INTER_CUBIC)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    normed = (rgb.astype(np.float32) / 255.0 - _IMAGENET_MEAN) / _IMAGENET_STD
    return np.transpose(normed, (2, 0, 1))  # HWC -> CHW


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-8:
        return v
    return v / n


def _compute_embedding(photo_paths: list[Path], model_path: str, auto_crop: bool) -> np.ndarray:
    """Run OSNet on every photo, average the L2-normalized
    embeddings, re-normalize. Skips photos that fail to decode /
    auto-crop / produce a finite embedding."""
    try:
        import onnxruntime as ort
    except ImportError:
        sys.exit("Requires onnxruntime: pip install onnxruntime")

    if not Path(model_path).is_file():
        sys.exit(
            f"Model file not found at {model_path}\n"
            "Run scripts/dev/export_osnet_onnx.py to produce it."
        )

    print(f"Loading OSNet ONNX from {model_path} ...", flush=True)
    session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name

    embeddings: list[np.ndarray] = []
    for path in photo_paths:
        bgr = cv2.imread(str(path))
        if bgr is None:
            print(f"  SKIP {path.name}: cv2 could not decode", flush=True)
            continue

        if auto_crop:
            crop = _auto_crop_person(bgr)
            if crop is None:
                print(
                    f"  SKIP {path.name}: no person detected with conf>=0.5",
                    flush=True,
                )
                continue
        else:
            crop = bgr

        # Single-image inference. Batching across photos would be
        # faster, but enrollment runs once and the latency is
        # dominated by model load anyway.
        chw = _preprocess(crop)
        batch = chw[None, :, :, :].astype(np.float32)
        out = session.run(None, {input_name: batch})[0][0]  # shape (512,)
        emb = _normalize(out)
        if not np.isfinite(emb).all():
            print(f"  SKIP {path.name}: non-finite embedding", flush=True)
            continue
        embeddings.append(emb)
        print(
            f"  OK   {path.name}: crop {crop.shape[1]}x{crop.shape[0]}, emb_dim={emb.shape[0]}",
            flush=True,
        )

    if not embeddings:
        sys.exit("No usable embeddings produced. Enrollment aborted.")

    avg = np.mean(np.stack(embeddings, axis=0), axis=0)
    avg_normed = _normalize(avg)
    print(
        f"Averaged {len(embeddings)} embeddings -> final dim={avg_normed.shape[0]}",
        flush=True,
    )
    return avg_normed


async def _publish_enrollment(
    *,
    nats_url: str,
    actor_id: str,
    name: str,
    embedding: np.ndarray,
    role: str | None,
) -> None:
    event = ActorEnrollmentEvent(
        actor_id=actor_id,
        action="enrolled",
        name=name,
        role=role,
        body_embedding=tuple(float(x) for x in embedding.tolist()),
    )
    nc = NATS()
    await nc.connect(servers=[nats_url])
    try:
        await nc.publish(SUBJECT_ACTOR_ENROLLED, event.model_dump_json().encode("utf-8"))
        await nc.flush()
        print(
            f"Published body enrollment for {actor_id} ({name}) on {SUBJECT_ACTOR_ENROLLED}",
            flush=True,
        )
    finally:
        await nc.drain()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--actor-id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", default=None)
    p.add_argument(
        "--model",
        default="/data/sentihome/models/osnet_x1_0.onnx",
        help="Path to the OSNet ONNX (must match preprocessor's body_id_model_path).",
    )
    p.add_argument(
        "--photo",
        action="append",
        default=[],
        help="Path to a reference photo. Repeatable.",
    )
    p.add_argument(
        "--photo-dir",
        action="append",
        default=[],
        help="Directory of reference photos. Repeatable.",
    )
    p.add_argument(
        "--auto-crop",
        action="store_true",
        help="Run YOLO to find the largest person bbox in each photo before embedding.",
    )
    p.add_argument(
        "--nats-url",
        default=os.environ.get("NATS_URL", "nats://localhost:4222"),
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    paths = _collect_photo_paths(args.photo, args.photo_dir)
    print(f"Enrolling actor_id={args.actor_id} from {len(paths)} photo(s)")
    embedding = _compute_embedding(paths, args.model, args.auto_crop)
    asyncio.run(
        _publish_enrollment(
            nats_url=args.nats_url,
            actor_id=args.actor_id,
            name=args.name,
            embedding=embedding,
            role=args.role,
        )
    )


if __name__ == "__main__":
    main()
