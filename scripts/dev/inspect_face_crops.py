#!/usr/bin/env python
"""Inspect the actual face crops the recognizer saw for a frame window.

Daylight face-rec diagnostic (task #95). Faithfully replicates the
live FacePipeline head-crop path: reads a saved FrameWindow JSON
(``fw_walk.json``), and for every *person* detection pulls that frame
back from the running preprocessor's ``/frames/{cam}/{ts}.jpg``
endpoint, crops the head region exactly as
``face_pipeline._head_region`` does, runs InsightFace on that head
region, and for each detected face:

  * saves the head region (what the detector saw),
  * saves the landmark-aligned **112x112 crop** (what ArcFace embeds,
    via ``face_align.norm_crop``),
  * computes cosine vs the live ``darin`` 4-photo centroid,
  * prints det_score + cosine + the face's pixel size.

Then writes two contact sheets — head regions and aligned crops — so
we can *see* the detail the model had to work with. Read-only w.r.t.
the preprocessor.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from insightface.app import FaceAnalysis
from insightface.utils import face_align
from kukiihome_preprocessor.pipelines.identity.face_pipeline import _head_region

PREPROC = "http://localhost:8090"
CAM = "dahuapoolcam"
OUT = Path("C:/Users/darin_jwxgczt/Kukii-Home/face_debug")
REF_PHOTOS = [
    "C:/Users/darin_jwxgczt/Downloads/68989224054__B95AD3E0-00E2-4BDF-903E-49A1A5A961FC.fullsizerender.JPG",
    "C:/Users/darin_jwxgczt/Downloads/IMG_2184.JPG",
    "C:/Users/darin_jwxgczt/Downloads/IMG_1711.JPG",
    "C:/Users/darin_jwxgczt/Downloads/IMG_1351.JPG",
]


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v if n < 1e-8 else v / n


def _largest(faces):
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def build_centroid(app: FaceAnalysis) -> np.ndarray:
    embs = []
    for p in REF_PHOTOS:
        bgr = cv2.imread(p)
        if bgr is None:
            continue
        faces = app.get(bgr)
        if not faces:
            continue
        embs.append(np.asarray(_largest(faces).normed_embedding, dtype=np.float32))
    return _normalize(np.mean(np.stack(embs), axis=0))


def fetch_frame(ts: float) -> np.ndarray | None:
    try:
        url = f"{PREPROC}/frames/{CAM}/{ts:.3f}.jpg"  # localhost dev fetch
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            if resp.status != 200:
                return None
            content = resp.read()
    except Exception:
        return None
    return cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)


def _pad_to(img: np.ndarray, size: int) -> np.ndarray:
    """Letterbox a head crop into a size x size tile for the contact sheet."""
    h, w = img.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = cv2.resize(img, (nw, nh))
    tile = np.zeros((size, size, 3), dtype=np.uint8)
    oy, ox = (size - nh) // 2, (size - nw) // 2
    tile[oy : oy + nh, ox : ox + nw] = resized
    return tile


def main() -> None:
    fw = json.loads(Path("C:/Users/darin_jwxgczt/Kukii-Home/fw_walk.json").read_text())
    persons = [d for d in fw.get("detections", []) if d["kind"] == "person"]
    print(f"person detections in window: {len(persons)}")
    OUT.mkdir(parents=True, exist_ok=True)

    app = FaceAnalysis(name="buffalo_s", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    centroid = build_centroid(app)
    print(f"centroid dim={centroid.shape[0]} (from {len(REF_PHOTOS)} ref photos)\n")

    results = []
    no_face = 0
    frame_cache: dict[float, np.ndarray | None] = {}
    for d in persons:
        ts = d["frame_ts"]
        if ts not in frame_cache:
            frame_cache[ts] = fetch_frame(ts)
        bgr = frame_cache[ts]
        if bgr is None:
            continue
        h, w = bgr.shape[:2]
        head = _head_region(bgr, tuple(d["bbox"]), w, h)
        if head is None:
            continue
        faces = app.get(head)
        if not faces:
            no_face += 1
            continue
        f = _largest(faces)
        aligned = face_align.norm_crop(head, f.kps, image_size=112)
        emb = _normalize(np.asarray(f.normed_embedding, dtype=np.float32))
        cos = float(np.dot(emb, centroid))
        fx1, fy1, fx2, fy2 = (int(v) for v in f.bbox)
        results.append(
            {
                "ts": ts,
                "det_score": float(f.det_score),
                "cosine": cos,
                "face_px": max(fx2 - fx1, fy2 - fy1),
                "person_conf": d["confidence"],
                "head": head,
                "aligned": aligned,
            }
        )

    results.sort(key=lambda r: r["cosine"], reverse=True)
    print(
        f"head regions with a detected face: {len(results)}   |   no-face head regions: {no_face}\n"
    )
    print(f"{'ts':>14} {'person':>6} {'det':>5} {'cosine':>7} {'face_px':>8}")
    for r in results:
        print(
            f"{r['ts']:>14.1f} {r['person_conf']:>6.2f} {r['det_score']:>5.2f} "
            f"{r['cosine']:>7.3f} {r['face_px']:>8d}"
        )

    top = results[:12]
    for i, r in enumerate(top):
        tag = f"cos{r['cosine']:.2f}_det{r['det_score']:.2f}_px{r['face_px']}"
        cv2.imwrite(str(OUT / f"aligned_{i:02d}_{tag}.png"), r["aligned"])
        cv2.imwrite(str(OUT / f"head_{i:02d}_{tag}.png"), r["head"])

    def sheet(tiles, name):
        if not tiles:
            return
        cols = min(6, len(tiles))
        rows = (len(tiles) + cols - 1) // cols
        canvas = np.zeros((rows * 112, cols * 112, 3), dtype=np.uint8)
        for i, t in enumerate(tiles):
            ry, cx = divmod(i, cols)
            canvas[ry * 112 : ry * 112 + 112, cx * 112 : cx * 112 + 112] = t
        cv2.imwrite(str(OUT / name), canvas)

    sheet([r["aligned"] for r in top], "aligned_sheet.png")
    sheet([_pad_to(r["head"], 112) for r in top], "head_sheet.png")
    print(f"\nwrote {len(top)} aligned+head crops, aligned_sheet.png, head_sheet.png to {OUT}")

    if results:
        b = results[0]
        print(
            f"\nBEST: cosine={b['cosine']:.3f} det={b['det_score']:.2f} face_px={b['face_px']} "
            f"→ {'MATCH' if b['cosine'] >= 0.5 else 'NO MATCH'} (thr 0.5)"
        )


if __name__ == "__main__":
    main()
