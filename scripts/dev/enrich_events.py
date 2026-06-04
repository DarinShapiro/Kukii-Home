"""Enrichment worker: turn persisted motion events into queryable detections.

Scans the durable event store on disk, and for every event not yet enriched
in the DetectionStore, runs the production detector on its frames and writes
the results. Decoupled from capture — runs offline (CPU catch-up) or as a
continuous service (GPU real-time). The backlog of un-enriched events is the
detection queue; `query_detections.py --lag` reports how far behind it is.

With ``--embed`` it ALSO runs the enabled always-embed identity pipelines
(body-ID for now; gait/face/pet/CC-ReID as they migrate) over each person
detection and persists the embeddings — independent of whether any actor is
enrolled. A later ``resolve_event`` then names those tracks against a corpus
enrolled after the fact, with no re-inference over the (long-evicted) frames.
The identity pipelines are built from the same env-driven config + shared
factory the live service uses, so the models never drift between paths.

Usage:
  python scripts/dev/enrich_events.py [--events DIR] [--db PATH] [--loop] [--imgsz 1280] [--embed]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, "services/preprocessor/src")
from kukiihome_preprocessor.detection_store import (
    DetectionRow,
    DetectionStore,
    EmbeddingRow,
)

# mapped-kind per-class floors mirror the production detector
PER_CLASS = {"dog": 0.25, "cat": 0.25, "animal": 0.25}
DEFAULT_FLOOR = 0.5
COCO_MAP = {
    "person": "person", "car": "vehicle", "motorcycle": "vehicle", "bus": "vehicle",
    "truck": "vehicle", "bicycle": "vehicle", "dog": "dog", "cat": "cat", "bird": "animal",
    "horse": "animal", "sheep": "animal", "cow": "animal", "bear": "animal", "deer": "animal",
}


def enrich_event(
    model, names, event_dir: Path, store: DetectionStore, imgsz: int, embed_pipelines=None,
    tracker: str | None = None,
) -> int:
    manifest = json.loads((event_dir / "event.json").read_text())
    eid = manifest["event_id"]
    camera_id = manifest["camera_id"]
    store.register_event(
        event_id=eid, camera_id=camera_id, node_id=manifest.get("node_id"),
        trigger_ts=manifest.get("trigger_ts"), window_start=manifest.get("window_start"),
        window_end=manifest.get("window_end"), frame_count=manifest.get("frame_count"),
        captured_ts=manifest.get("window_end") or manifest.get("created_at") or time.time(),
    )
    ts_by_name = {fi["name"]: fi["ts"] for fi in manifest.get("frame_index", [])}
    floor = min([DEFAULT_FLOOR, *PER_CLASS.values()])
    frame_paths = sorted(event_dir.glob("frame_*.jpg"))
    total = 0
    # When embedding, accumulate (frame, person-tags) so the (async) identity
    # pipelines run in one pass after the detection loop — one event loop spin
    # up per event, not per frame.
    embed_inputs: list = []
    tracker_started = False
    for idx, fp in enumerate(frame_paths):
        im = cv2.imread(str(fp))
        if im is None:
            continue
        h, w = im.shape[:2]
        name = fp.name
        fts = ts_by_name.get(name, 0.0)
        # Track (not just detect) so each person carries a stable track_id
        # across the event's frames — the join key body-ID embeds against
        # (an embedding with no track can't be correlated to anything).
        # persist=False on the first frame starts a fresh tracker so IDs +
        # Kalman motion state don't bleed from the previous event into this
        # one (different time, different scene). A custom `tracker` config
        # (e.g. botsort_reid.yaml) enables appearance ReID to re-link fragments.
        track_kwargs = {
            "imgsz": imgsz, "conf": floor, "persist": tracker_started, "verbose": False,
        }
        if tracker:
            track_kwargs["tracker"] = tracker
        r = model.track(im, **track_kwargs)[0]
        tracker_started = True
        frame_rows: list[DetectionRow] = []
        for i in range(len(r.boxes.cls)):
            coco = names[int(r.boxes.cls[i])]
            kind = COCO_MAP.get(coco)
            if kind is None:
                continue
            conf = float(r.boxes.conf[i])
            if conf < PER_CLASS.get(kind, DEFAULT_FLOOR):
                continue
            x1, y1, x2, y2 = (float(v) for v in r.boxes.xyxy[i].tolist())
            tid = str(int(r.boxes.id[i])) if getattr(r.boxes, "id", None) is not None else None
            frame_rows.append(DetectionRow(
                event_id=eid, camera_id=camera_id, frame_ts=fts, frame_name=name,
                kind=kind, confidence=round(conf, 3),
                bbox=(round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)),
                track_id=tid,
            ))
        # Commit INCREMENTALLY (per frame) so the store fills + partial queries
        # work + a crash doesn't lose the whole event. The detections table is
        # the streaming answer; mark_enriched flips the event to "done" at the end.
        if frame_rows:
            store.add_detections(frame_rows)
            total += len(frame_rows)
        if embed_pipelines:
            tags = _tracked_tags(frame_rows)
            if tags:
                embed_inputs.append((_buffered_frame(fp, fts, w, h), tags))
        if (idx + 1) % 20 == 0:
            print(f"  {eid}: {idx + 1}/{len(frame_paths)} frames, {total} detections so far",
                  flush=True)
    if embed_inputs:
        n_emb = asyncio.run(_embed_and_store(embed_pipelines, store, eid, camera_id, embed_inputs))
        print(f"  {eid}: persisted {n_emb} identity embedding(s)", flush=True)
    store.mark_enriched(eid, time.time())
    return total


def _tracked_tags(frame_rows: list[DetectionRow]):
    """All tracked detections as DetectionTags — the per-frame embed
    pipelines' input. Each pipeline self-filters to its own ``triggers_on``
    kinds (body→person, pet→dog/cat), so the worker just hands over every
    tracked det and lets the DAG route. Untracked dets are skipped: an
    embedding with no track can never be resolved back to anything."""
    from kukiihome_shared.preprocessor import DetectionTag

    return tuple(
        DetectionTag(
            kind=row.kind, confidence=row.confidence, bbox=row.bbox,
            frame_ts=row.frame_ts, track_id=row.track_id,
        )
        for row in frame_rows
        if row.track_id is not None and row.bbox is not None
    )


def _buffered_frame(fp: Path, ts: float, w: int, h: int):
    """A BufferedFrame from the on-disk JPEG — the pipelines decode the raw
    bytes themselves, so hand them the file verbatim (lossless, no re-encode)."""
    from kukiihome_preprocessor.pipelines.rolling_buffer import BufferedFrame

    return BufferedFrame(ts=ts, jpeg_bytes=fp.read_bytes(), width=w, height=h)


async def _embed_and_store(embed_pipelines, store, eid, camera_id, embed_inputs) -> int:
    """Run the always-embed pipelines and persist the embeddings — both the
    per-frame modalities (body/pet/face) and the temporal one (gait, one
    descriptor per track from its frame sequence). Stamps event_id + camera_id
    (which the pipeline-side TrackEmbedding doesn't carry) onto each store row.

    The gait (Stage-2 / E4) pass: build each person track's frame sequence
    across the event and run the temporal pipelines once over it. Gated by
    config (no gait pipeline → no-op) and by the pipeline's own min-frames
    floor (short tracks yield nothing) — the cheap cost control. The capture-
    quality gate (only gait the tracks face/body couldn't capture cleanly) is a
    live-path optimization; the offline worker embeds every track that clears
    min-frames."""
    from collections import defaultdict

    from kukiihome_preprocessor.pipelines.identity import (
        collect_embeddings,
        collect_track_embeddings,
    )

    def _row(te):
        return EmbeddingRow(
            event_id=eid, camera_id=camera_id, track_id=te.track_id,
            frame_ts=te.frame_ts, modality=te.modality, match_method=te.match_method,
            embedding=_np().asarray(te.embedding, dtype="float32"),
        )

    rows: list[EmbeddingRow] = []
    # ── per-frame (body / pet / face) ──
    for frame, tags in embed_inputs:
        for te in await collect_embeddings(embed_pipelines, frame=frame, detections=tags):
            rows.append(_row(te))

    # ── temporal (gait): one descriptor per person track ──
    sequences: dict[str, list] = defaultdict(list)
    for frame, tags in embed_inputs:
        for d in tags:
            if d.kind == "person" and d.track_id is not None:
                sequences[d.track_id].append((frame, d.bbox))
    tracks = {tid: tuple(items) for tid, items in sequences.items()}
    if tracks:
        for te in await collect_track_embeddings(embed_pipelines, tracks=tracks):
            rows.append(_row(te))

    if rows:
        store.add_embeddings(rows)
    return len(rows)


def _np():
    import numpy as np

    return np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default=r"C:/Users/darin_jwxgczt/Kukii-Home/events")
    ap.add_argument("--db", default=r"C:/Users/darin_jwxgczt/Kukii-Home/detections.db")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--loop", action="store_true", help="keep processing as new events appear")
    ap.add_argument("--embed", action="store_true",
                    help="also run the enabled always-embed identity pipelines and persist "
                         "embeddings (config via KUKIIHOME_PREPROCESSOR_* env, e.g. BODY_ID=true)")
    ap.add_argument("--tracker", default=None,
                    help="tracker config for model.track (e.g. scripts/dev/botsort_reid.yaml to "
                         "enable appearance ReID; default = ultralytics BoTSORT, motion-only)")
    args = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO("yolo11x.pt")
    names = model.names
    store = DetectionStore(args.db)

    embed_pipelines = None
    if args.embed:
        from kukiihome_preprocessor.config import load_from_env
        from kukiihome_preprocessor.pipelines.builders import build_identity_pipelines

        embed_pipelines = build_identity_pipelines(load_from_env())
        names_enabled = [p.name for p in embed_pipelines]
        if names_enabled:
            print(f"always-embed: {', '.join(names_enabled)}", flush=True)
        else:
            print("always-embed: no identity pipelines enabled — set "
                  "KUKIIHOME_PREPROCESSOR_BODY_ID=true (etc.); skipping embedding", flush=True)

    while True:
        events = sorted(Path(args.events).glob("*/*/event.json"))
        did = 0
        for ev_json in events:
            ev_dir = ev_json.parent
            eid = ev_dir.name
            if store.is_enriched(eid):
                continue
            n = enrich_event(model, names, ev_dir, store, args.imgsz, embed_pipelines,
                             tracker=args.tracker)
            print(f"enriched {eid}: {n} detections", flush=True)
            did += 1
        if not args.loop:
            print(f"DONE: enriched {did} new event(s)", flush=True)
            break
        if did == 0:
            time.sleep(5)


if __name__ == "__main__":
    main()
