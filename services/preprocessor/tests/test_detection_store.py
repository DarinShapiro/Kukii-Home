"""DetectionStore tests — query + lag semantics, no torch/cv2 needed."""

from __future__ import annotations

from kukiihome_preprocessor.detection_store import DetectionRow, DetectionStore


def _store(tmp_path):
    return DetectionStore(tmp_path / "det.db")


def _det(event_id, cam, ts, kind, conf, track="1"):
    return DetectionRow(event_id=event_id, camera_id=cam, frame_ts=ts, frame_name=f"{ts}.jpg",
                        kind=kind, confidence=conf, bbox=(0.1, 0.1, 0.2, 0.2), track_id=track)


def test_register_and_pending(tmp_path):
    s = _store(tmp_path)
    s.register_event(event_id="e1", camera_id="pool", captured_ts=100.0, window_end=100.0)
    assert s.pending_events("pool") == ["e1"]
    assert s.is_enriched("e1") is False
    s.mark_enriched("e1", 130.0)
    assert s.is_enriched("e1") is True
    assert s.pending_events("pool") == []


def test_register_is_idempotent(tmp_path):
    s = _store(tmp_path)
    s.register_event(event_id="e1", camera_id="pool", captured_ts=100.0)
    s.mark_enriched("e1", 130.0)
    s.register_event(event_id="e1", camera_id="pool", captured_ts=100.0)  # repeat
    assert s.is_enriched("e1") is True  # enriched_ts not clobbered


def test_query_by_time_kind_confidence(tmp_path):
    s = _store(tmp_path)
    s.register_event(event_id="e1", camera_id="pool", captured_ts=200.0)
    s.add_detections([
        _det("e1", "pool", 100.0, "person", 0.9),
        _det("e1", "pool", 101.0, "dog", 0.3),
        _det("e1", "pool", 150.0, "person", 0.4),
        _det("e1", "other", 100.0, "person", 0.95),
    ])
    # camera filter
    assert all(r.camera_id == "pool" for r in s.query(camera_id="pool"))
    # time window
    assert len(s.query(camera_id="pool", ts_start=99, ts_end=120)) == 2
    # kind
    assert len(s.query(camera_id="pool", kind="dog")) == 1
    # min confidence
    persons = s.query(camera_id="pool", kind="person", min_confidence=0.5)
    assert len(persons) == 1 and persons[0].confidence == 0.9


def test_lag_pending_and_caught_up(tmp_path):
    s = _store(tmp_path)
    # two events captured, none enriched yet
    s.register_event(event_id="e1", camera_id="pool", captured_ts=100.0, window_end=100.0)
    s.register_event(event_id="e2", camera_id="pool", captured_ts=160.0, window_end=160.0)
    lag = s.lag("pool")
    assert lag.pending_events == 2
    assert lag.lag_seconds is None  # nothing enriched yet
    # enrich the first → behind by (160 - 100) = 60s, 1 still pending
    s.mark_enriched("e1", 130.0)
    lag = s.lag("pool")
    assert lag.pending_events == 1
    assert lag.lag_seconds == 60.0
    # enrich the second → caught up
    s.mark_enriched("e2", 200.0)
    lag = s.lag("pool")
    assert lag.pending_events == 0
    assert lag.lag_seconds == 0.0


def test_query_roundtrips_bbox_and_track(tmp_path):
    s = _store(tmp_path)
    s.register_event(event_id="e1", camera_id="pool", captured_ts=10.0)
    s.add_detections([_det("e1", "pool", 5.0, "person", 0.8, track="7")])
    row = s.query(camera_id="pool")[0]
    assert row.bbox == (0.1, 0.1, 0.2, 0.2)
    assert row.track_id == "7"
