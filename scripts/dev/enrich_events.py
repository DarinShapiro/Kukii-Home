"""Enrichment worker: turn persisted motion events into queryable detections.

Scans the durable event store on disk, and for every event not yet enriched
in the DetectionStore, runs the production detector on its frames and writes
the results. Decoupled from capture — runs offline (CPU catch-up) or as a
continuous service (GPU real-time). The backlog of un-enriched events is the
detection queue; `query_detections.py --lag` reports how far behind it is.

Usage:
  python scripts/dev/enrich_events.py [--events DIR] [--db PATH] [--loop] [--imgsz 1280]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, "services/preprocessor/src")
from kukiihome_preprocessor.detection_store import DetectionRow, DetectionStore

# mapped-kind per-class floors mirror the production detector
PER_CLASS = {"dog": 0.25, "cat": 0.25, "animal": 0.25}
DEFAULT_FLOOR = 0.5
COCO_MAP = {
    "person": "person", "car": "vehicle", "motorcycle": "vehicle", "bus": "vehicle",
    "truck": "vehicle", "bicycle": "vehicle", "dog": "dog", "cat": "cat", "bird": "animal",
    "horse": "animal", "sheep": "animal", "cow": "animal", "bear": "animal", "deer": "animal",
}


def enrich_event(model, names, event_dir: Path, store: DetectionStore, imgsz: int) -> int:
    manifest = json.loads((event_dir / "event.json").read_text())
    eid = manifest["event_id"]
    store.register_event(
        event_id=eid, camera_id=manifest["camera_id"], node_id=manifest.get("node_id"),
        trigger_ts=manifest.get("trigger_ts"), window_start=manifest.get("window_start"),
        window_end=manifest.get("window_end"), frame_count=manifest.get("frame_count"),
        captured_ts=manifest.get("window_end") or manifest.get("created_at") or time.time(),
    )
    ts_by_name = {fi["name"]: fi["ts"] for fi in manifest.get("frame_index", [])}
    rows: list[DetectionRow] = []
    floor = min([DEFAULT_FLOOR, *PER_CLASS.values()])
    for fp in sorted(event_dir.glob("frame_*.jpg")):
        im = cv2.imread(str(fp))
        if im is None:
            continue
        h, w = im.shape[:2]
        name = fp.name
        fts = ts_by_name.get(name, 0.0)
        r = model.predict(im, imgsz=imgsz, conf=floor, verbose=False)[0]
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
            rows.append(DetectionRow(
                event_id=eid, camera_id=manifest["camera_id"], frame_ts=fts, frame_name=name,
                kind=kind, confidence=round(conf, 3),
                bbox=(round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)),
                track_id=tid,
            ))
    store.add_detections(rows)
    store.mark_enriched(eid, time.time())
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default=r"C:/Users/darin_jwxgczt/Kukii-Home/events")
    ap.add_argument("--db", default=r"C:/Users/darin_jwxgczt/Kukii-Home/detections.db")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--loop", action="store_true", help="keep processing as new events appear")
    args = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO("yolo11x.pt")
    names = model.names
    store = DetectionStore(args.db)

    while True:
        events = sorted(Path(args.events).glob("*/*/event.json"))
        did = 0
        for ev_json in events:
            ev_dir = ev_json.parent
            eid = ev_dir.name
            if store.is_enriched(eid):
                continue
            n = enrich_event(model, names, ev_dir, store, args.imgsz)
            print(f"enriched {eid}: {n} detections", flush=True)
            did += 1
        if not args.loop:
            print(f"DONE: enriched {did} new event(s)", flush=True)
            break
        if did == 0:
            time.sleep(5)


if __name__ == "__main__":
    main()
