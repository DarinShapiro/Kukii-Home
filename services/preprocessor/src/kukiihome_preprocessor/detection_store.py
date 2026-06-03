"""Queryable detection store (SQLite).

The durable event sink persists *frames*; this stores the *detections* the
enrichment pass produces, in a form you query rather than eyeball. Answers
"was there a person on the pool cam between 11:20 and 11:25, at what
confidence" with a SQL lookup — not by sampling arbitrary JPEGs.

Two tables:
  events     — one row per motion event, with captured_ts (when the frames
               landed, ~window_end) and enriched_ts (when detection finished,
               NULL = still pending). The (captured - enriched) gap is the
               preprocessing-lag metric: how far behind real-time detection is,
               and whether it's catching up.
  detections — one row per (frame, detected object): kind, confidence, bbox,
               track_id, frame_ts. Indexed by (camera, frame_ts) and kind.

Local-first: a single SQLite file, no server. Safe for the enrichment worker
(writer) and query CLI (reader) to share via WAL mode.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    camera_id    TEXT NOT NULL,
    node_id      TEXT,
    trigger_ts   REAL,
    window_start REAL,
    window_end   REAL,
    frame_count  INTEGER,
    captured_ts  REAL NOT NULL,   -- frames durably persisted (~window_end)
    enriched_ts  REAL             -- detection finished; NULL = pending
);
CREATE TABLE IF NOT EXISTS detections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   TEXT NOT NULL,
    camera_id  TEXT NOT NULL,
    frame_ts   REAL NOT NULL,
    frame_name TEXT,
    kind       TEXT NOT NULL,
    confidence REAL NOT NULL,
    bbox       TEXT,              -- json [x1,y1,x2,y2] (normalized or px)
    track_id   TEXT
);
CREATE INDEX IF NOT EXISTS idx_det_cam_ts ON detections(camera_id, frame_ts);
CREATE INDEX IF NOT EXISTS idx_det_kind   ON detections(kind);
CREATE INDEX IF NOT EXISTS idx_ev_cam     ON events(camera_id, captured_ts);
"""


@dataclass
class DetectionRow:
    event_id: str
    camera_id: str
    frame_ts: float
    frame_name: str | None
    kind: str
    confidence: float
    bbox: tuple[float, float, float, float] | None
    track_id: str | None


@dataclass
class LagReport:
    camera_id: str
    pending_events: int
    newest_captured_ts: float | None
    newest_enriched_ts: float | None
    lag_seconds: float | None
    """newest_captured_window_end - newest_enriched_window_end. None if nothing
    enriched yet. Positive + pending>0 → behind; ~0 + pending=0 → caught up."""


class DetectionStore:
    """SQLite-backed detection index. Thread-safe per-connection; open one per
    process (WAL lets a reader + writer coexist across processes)."""

    def __init__(self, path: str | Path = "detections.db") -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── writes ──────────────────────────────────────────────────────────

    def register_event(
        self,
        *,
        event_id: str,
        camera_id: str,
        captured_ts: float,
        node_id: str | None = None,
        trigger_ts: float | None = None,
        window_start: float | None = None,
        window_end: float | None = None,
        frame_count: int | None = None,
    ) -> None:
        """Record an event as PENDING (enriched_ts NULL). Idempotent: a repeat
        register for the same id leaves enriched_ts untouched."""
        self._conn.execute(
            """INSERT INTO events
               (event_id, camera_id, node_id, trigger_ts, window_start,
                window_end, frame_count, captured_ts, enriched_ts)
               VALUES (?,?,?,?,?,?,?,?, NULL)
               ON CONFLICT(event_id) DO NOTHING""",
            (event_id, camera_id, node_id, trigger_ts, window_start,
             window_end, frame_count, captured_ts),
        )
        self._conn.commit()

    def add_detections(self, rows: list[DetectionRow]) -> None:
        self._conn.executemany(
            """INSERT INTO detections
               (event_id, camera_id, frame_ts, frame_name, kind, confidence, bbox, track_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (r.event_id, r.camera_id, r.frame_ts, r.frame_name, r.kind,
                 r.confidence, json.dumps(r.bbox) if r.bbox is not None else None, r.track_id)
                for r in rows
            ],
        )
        self._conn.commit()

    def mark_enriched(self, event_id: str, enriched_ts: float) -> None:
        self._conn.execute(
            "UPDATE events SET enriched_ts=? WHERE event_id=?", (enriched_ts, event_id)
        )
        self._conn.commit()

    def is_enriched(self, event_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT enriched_ts FROM events WHERE event_id=?", (event_id,)
        )
        row = cur.fetchone()
        return row is not None and row["enriched_ts"] is not None

    def pending_events(self, camera_id: str | None = None) -> list[str]:
        if camera_id:
            cur = self._conn.execute(
                "SELECT event_id FROM events WHERE enriched_ts IS NULL AND camera_id=? "
                "ORDER BY captured_ts",
                (camera_id,),
            )
        else:
            cur = self._conn.execute(
                "SELECT event_id FROM events WHERE enriched_ts IS NULL ORDER BY captured_ts"
            )
        return [r["event_id"] for r in cur.fetchall()]

    # ── queries ─────────────────────────────────────────────────────────

    def query(
        self,
        *,
        camera_id: str | None = None,
        ts_start: float | None = None,
        ts_end: float | None = None,
        kind: str | None = None,
        min_confidence: float = 0.0,
    ) -> list[DetectionRow]:
        clauses = ["confidence >= ?"]
        params: list[object] = [min_confidence]
        if camera_id:
            clauses.append("camera_id = ?")
            params.append(camera_id)
        if ts_start is not None:
            clauses.append("frame_ts >= ?")
            params.append(ts_start)
        if ts_end is not None:
            clauses.append("frame_ts <= ?")
            params.append(ts_end)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        # `clauses` are hardcoded fragments ("kind = ?" etc.); every value is
        # bound via params. No user string is interpolated -> S608 false positive.
        where = " AND ".join(clauses)
        sql = f"SELECT * FROM detections WHERE {where} ORDER BY frame_ts"  # noqa: S608
        cur = self._conn.execute(sql, params)
        out = []
        for r in cur.fetchall():
            out.append(
                DetectionRow(
                    event_id=r["event_id"], camera_id=r["camera_id"], frame_ts=r["frame_ts"],
                    frame_name=r["frame_name"], kind=r["kind"], confidence=r["confidence"],
                    bbox=tuple(json.loads(r["bbox"])) if r["bbox"] else None,
                    track_id=r["track_id"],
                )
            )
        return out

    def lag(self, camera_id: str) -> LagReport:
        cur = self._conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM events WHERE camera_id=? AND enriched_ts IS NULL),
                 (SELECT MAX(window_end) FROM events WHERE camera_id=?),
                 (SELECT MAX(window_end) FROM events WHERE camera_id=? AND enriched_ts IS NOT NULL)""",
            (camera_id, camera_id, camera_id),
        )
        pending, newest_cap, newest_enr = cur.fetchone()
        lag = None
        if newest_cap is not None and newest_enr is not None:
            lag = max(0.0, newest_cap - newest_enr)
        elif newest_cap is not None and newest_enr is None:
            lag = None  # nothing enriched yet
        return LagReport(
            camera_id=camera_id, pending_events=pending or 0,
            newest_captured_ts=newest_cap, newest_enriched_ts=newest_enr, lag_seconds=lag,
        )
