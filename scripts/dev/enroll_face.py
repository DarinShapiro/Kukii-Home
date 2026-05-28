#!/usr/bin/env python
"""Enroll a face into the preprocessor's ActorCache.

Computes an ArcFace embedding from one or more reference photos of a
person, averages them into a single canonical embedding, and
publishes an :class:`ActorEnrollmentEvent` on the NATS subject the
preprocessor subscribes to (``sentihome.memory.actor.enrolled``).
The running preprocessor picks it up and starts matching live faces
against the new embedding within seconds.

Multiple reference photos -> per-photo embeddings are averaged then
re-normalized. This is the standard ArcFace enrollment trick: the
average of a few angles is a much better template than any single
shot, since it cancels out lighting / pose noise.

Usage:
    # Single photo:
    python scripts/dev/enroll_face.py \\
        --actor-id alice \\
        --name Alice \\
        --photo /path/to/alice.jpg

    # Multiple photos (averaged):
    python scripts/dev/enroll_face.py \\
        --actor-id alice \\
        --name Alice \\
        --photo alice1.jpg --photo alice2.jpg --photo alice3.jpg

    # Or point at a directory of photos:
    python scripts/dev/enroll_face.py \\
        --actor-id alice \\
        --name Alice \\
        --photo-dir /path/to/alice_photos/

Connects to NATS at ``$NATS_URL`` or ``nats://localhost:4222``.

Requires insightface + onnxruntime + opencv-python-headless installed
(they are runtime deps of the preprocessor package, so an editable
install of services/preprocessor pulls them in).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import numpy as np
from nats.aio.client import Client as NATS
from sentihome_shared.preprocessor import (
    SUBJECT_ACTOR_ENROLLED,
    ActorEnrollmentEvent,
)

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


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


def _compute_embedding(photo_paths: list[Path], model_pack: str) -> np.ndarray:
    """Run InsightFace on every photo, take the largest face per
    image, average the L2-normalized embeddings, re-normalize. The
    'largest face' rule handles photos where the subject is in
    frame plus incidental bystanders."""
    import cv2
    from insightface.app import FaceAnalysis  # type: ignore[import-not-found]

    print(f"Loading InsightFace model_pack={model_pack} ...", flush=True)
    app = FaceAnalysis(name=model_pack, providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))

    embeddings: list[np.ndarray] = []
    for path in photo_paths:
        bgr = cv2.imread(str(path))
        if bgr is None:
            print(f"  SKIP {path}: cv2 could not decode", flush=True)
            continue
        faces = app.get(bgr)
        if not faces:
            print(f"  SKIP {path}: no face detected", flush=True)
            continue
        # Pick the largest-bbox face (most likely the subject).
        largest = max(
            faces,
            key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
        )
        emb = (
            np.asarray(largest.normed_embedding, dtype=np.float32)
            if largest.normed_embedding is not None
            else _normalize(np.asarray(largest.embedding, dtype=np.float32))
        )
        embeddings.append(emb)
        print(
            f"  OK   {path.name}: face det_score={float(largest.det_score):.3f}, "
            f"emb_dim={emb.shape[0]}",
            flush=True,
        )

    if not embeddings:
        sys.exit("No usable faces in any photo. Enrollment aborted.")

    avg = np.mean(np.stack(embeddings, axis=0), axis=0)
    avg_normed = _normalize(avg)
    print(
        f"Averaged {len(embeddings)} embeddings -> final dim={avg_normed.shape[0]}",
        flush=True,
    )
    return avg_normed


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-8:
        return v
    return v / n


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
        face_embedding=tuple(float(x) for x in embedding.tolist()),
    )
    nc = NATS()
    await nc.connect(servers=[nats_url])
    try:
        await nc.publish(SUBJECT_ACTOR_ENROLLED, event.model_dump_json().encode("utf-8"))
        await nc.flush()
        print(
            f"Published enrollment for {actor_id} ({name}) on {SUBJECT_ACTOR_ENROLLED}",
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
        "--model-pack",
        default="buffalo_s",
        choices=("buffalo_s", "buffalo_l", "antelopev2"),
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
    embedding = _compute_embedding(paths, args.model_pack)
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
