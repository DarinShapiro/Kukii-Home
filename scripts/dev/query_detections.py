"""Query the detection store — answer "who/what was on camera X, when" by SQL,
not by eyeballing frames. Also reports preprocessing lag.

Usage:
  python scripts/dev/query_detections.py --camera pool --kind person --last 600
  python scripts/dev/query_detections.py --camera pool --from 1780500000 --to 1780500600
  python scripts/dev/query_detections.py --camera pool --lag
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, "services/preprocessor/src")
from kukiihome_preprocessor.detection_store import DetectionStore


def _hms(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).astimezone().strftime("%H:%M:%S")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"C:/Users/darin_jwxgczt/Kukii-Home/detections.db")
    ap.add_argument("--camera", default="pool")
    ap.add_argument("--kind", default=None)
    ap.add_argument("--min-conf", type=float, default=0.0)
    ap.add_argument("--from", dest="t0", type=float, default=None)
    ap.add_argument("--to", dest="t1", type=float, default=None)
    ap.add_argument("--last", type=float, default=None, help="seconds back from now")
    ap.add_argument("--lag", action="store_true")
    args = ap.parse_args()

    store = DetectionStore(args.db)

    if args.lag:
        lr = store.lag(args.camera)
        lag = "n/a (nothing enriched)" if lr.lag_seconds is None else f"{lr.lag_seconds:.0f}s"
        state = "CAUGHT UP" if lr.pending_events == 0 else "BEHIND"
        print(f"[{args.camera}] enrichment {state}: lag={lag}, pending_events={lr.pending_events}")
        return

    t0, t1 = args.t0, args.t1
    if args.last is not None:
        t1 = time.time()
        t0 = t1 - args.last

    rows = store.query(camera_id=args.camera, ts_start=t0, ts_end=t1,
                       kind=args.kind, min_confidence=args.min_conf)
    if not rows:
        print("no detections match")
        return
    # summarize by kind + show the peak-confidence frame per kind
    by_kind: dict[str, list] = {}
    for r in rows:
        by_kind.setdefault(r.kind, []).append(r)
    print(f"{len(rows)} detections on {args.camera}:")
    for kind, rs in sorted(by_kind.items()):
        best = max(rs, key=lambda r: r.confidence)
        span = f"{_hms(min(r.frame_ts for r in rs))}-{_hms(max(r.frame_ts for r in rs))}"
        print(f"  {kind:8} x{len(rs):<4} conf[max={best.confidence:.2f}] "
              f"span {span}  best={best.frame_name}")


if __name__ == "__main__":
    main()
