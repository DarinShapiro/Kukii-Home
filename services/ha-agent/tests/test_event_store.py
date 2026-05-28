"""Unit tests for the per-event persistent store."""

from __future__ import annotations

import json
from pathlib import Path

from sentihome_ha_agent.event_store import EventStore


def _store(tmp_path: Path) -> EventStore:
    return EventStore(root=tmp_path / "events")


def _alert(
    alert_id: str = "alert_x",
    *,
    evidence_ref: str | None = None,
    **extra,
) -> dict:
    base = {
        "alert_id": alert_id,
        "recorded_at": "2026-05-28T15:30:00+00:00",
        "camera_id": "front_porch",
        "camera_name": "Front Porch",
        "headline": "Person at Front Porch",
        "sensor_classification": "person",
        "timings": {"ha_to_snapshot_complete_ms": 800},
    }
    if evidence_ref:
        base["evidence_ref"] = evidence_ref
    base.update(extra)
    return base


# ─── record_from_alert ──────────────────────────────────────────────


def test_record_from_alert_writes_meta_json(tmp_path: Path):
    store = _store(tmp_path)
    event_id = store.record_from_alert(_alert("a1"))
    assert event_id == "a1"
    meta = json.loads((tmp_path / "events" / "a1" / "meta.json").read_text("utf-8"))
    assert meta["event_id"] == "a1"
    assert meta["alert_id"] == "a1"
    assert meta["camera_name"] == "Front Porch"


def test_record_from_alert_stamps_default_triage_decision(tmp_path: Path):
    """All alert-fired events get triage_decision='alert_fired' by
    default. The discriminator field is present from day one so
    future near-miss / vlm-flagged-silent records don't need a
    migration."""
    store = _store(tmp_path)
    store.record_from_alert(_alert("a1"))
    meta = store.get("a1")
    assert meta is not None
    assert meta["triage_decision"] == "alert_fired"


def test_record_from_alert_reserves_vlm_response_field(tmp_path: Path):
    """Phase 11 VLM hook: vlm_response is None today, populated when
    the VLM dispatch lands."""
    store = _store(tmp_path)
    store.record_from_alert(_alert("a1"))
    meta = store.get("a1")
    assert meta is not None
    assert "vlm_response" in meta
    assert meta["vlm_response"] is None


def test_record_from_alert_copies_evidence_into_event_dir(tmp_path: Path):
    """The on-disk snapshot at evidence_ref gets copied to
    <event_id>/frame.jpg so the alert's frame is bundled with
    everything else and survives later evidence-cleanup runs."""
    snap = tmp_path / "snapshots" / "x.jpg"
    snap.parent.mkdir(parents=True)
    snap.write_bytes(b"\xff\xd8\xff\xd9JPEG-BYTES")

    store = _store(tmp_path)
    store.record_from_alert(_alert("a1", evidence_ref=str(snap)))

    frame_path = store.frame_path("a1")
    assert frame_path is not None
    assert frame_path.read_bytes() == b"\xff\xd8\xff\xd9JPEG-BYTES"
    # Original wasn't moved — backward-compat with legacy snapshot route.
    assert snap.exists()


def test_record_from_alert_handles_missing_evidence_file(tmp_path: Path):
    """evidence_ref pointing at a non-existent file: log + continue.
    The event meta is still written; frame_path returns None."""
    store = _store(tmp_path)
    event_id = store.record_from_alert(_alert("a1", evidence_ref="/nonexistent/x.jpg"))
    assert event_id == "a1"
    assert store.frame_path("a1") is None


def test_record_from_alert_returns_none_for_missing_id(tmp_path: Path):
    store = _store(tmp_path)
    assert store.record_from_alert({"camera_id": "x"}) is None


def test_record_from_alert_treats_dotdot_in_id_safely(tmp_path: Path):
    """Defensive: '..' in event_id can't escape the root dir."""
    store = _store(tmp_path)
    bad_id = "../../escape"
    store.record_from_alert(_alert(bad_id))
    # Sanitized dir lives under root, not outside it.
    escaped = tmp_path / "escape" / "meta.json"
    assert not escaped.exists()
    # And get() with the same id finds it (sanitization is symmetric).
    assert store.get(bad_id) is not None


# ─── record_enrichment (Epic 10.9) ──────────────────────────────────


def test_record_enrichment_merges_detections_and_identities(tmp_path: Path):
    store = _store(tmp_path)
    store.record_from_alert(_alert("a1"))
    ok = store.record_enrichment(
        "a1",
        detections=[{"kind": "person", "confidence": 0.9}],
        identified_entities=[{"actor_name": "Alice", "identity_method": "face_arcface"}],
        actor_matches=[{"actor_id": "alice", "match_method": "face_arcface"}],
    )
    assert ok is True
    meta = store.get("a1")
    assert meta is not None
    assert meta["detections"][0]["kind"] == "person"
    assert meta["identified_entities"][0]["actor_name"] == "Alice"
    assert meta["actor_matches"][0]["actor_id"] == "alice"
    assert meta["enriched"] is True
    assert "enriched_at" in meta


def test_record_enrichment_writes_annotated_jpeg(tmp_path: Path):
    store = _store(tmp_path)
    store.record_from_alert(_alert("a1"))
    assert store.frame_path("a1", annotated=True) is None  # none yet
    store.record_enrichment("a1", annotated_jpeg=b"\xff\xd8\xff\xd9ANNOTATED")
    annotated = store.frame_path("a1", annotated=True)
    assert annotated is not None
    assert annotated.read_bytes() == b"\xff\xd8\xff\xd9ANNOTATED"


def test_record_enrichment_partial_does_not_blank_existing(tmp_path: Path):
    """Passing only detections must not wipe identified_entities that
    a prior enrichment (or the original alert) already set."""
    store = _store(tmp_path)
    store.record_from_alert(_alert("a1", identified_entities=[{"actor_name": "Bob"}]))
    store.record_enrichment("a1", detections=[{"kind": "dog", "confidence": 0.8}])
    meta = store.get("a1")
    assert meta["detections"][0]["kind"] == "dog"
    # Untouched because we passed identified_entities=None (the default).
    assert meta["identified_entities"][0]["actor_name"] == "Bob"


def test_record_enrichment_returns_false_for_unknown_event(tmp_path: Path):
    store = _store(tmp_path)
    assert store.record_enrichment("ghost", detections=[]) is False
    # No orphan dir/file created for the unknown id.
    assert not (tmp_path / "events" / "ghost").exists()


# ─── get ────────────────────────────────────────────────────────────


def test_get_returns_none_for_unknown(tmp_path: Path):
    assert _store(tmp_path).get("nope") is None


def test_get_merges_feedback_into_meta_when_present(tmp_path: Path):
    store = _store(tmp_path)
    store.record_from_alert(_alert("a1"))
    store.record_feedback("a1", feedback={"reason": "empty_frame", "notes": "just leaves"})
    meta = store.get("a1")
    assert meta is not None
    assert meta["feedback"]["reason"] == "empty_frame"
    assert meta["feedback"]["notes"] == "just leaves"


def test_get_omits_feedback_key_when_no_feedback_file(tmp_path: Path):
    store = _store(tmp_path)
    store.record_from_alert(_alert("a1"))
    meta = store.get("a1")
    assert meta is not None
    assert "feedback" not in meta


# ─── record_feedback ────────────────────────────────────────────────


def test_record_feedback_rejects_unknown_event(tmp_path: Path):
    """No event dir = nothing to attach feedback to. Don't create an
    orphan feedback.json — that'd be a sign of a bug, not a write to
    swallow."""
    store = _store(tmp_path)
    assert store.record_feedback("ghost", feedback={"reason": "x"}) is False


def test_record_feedback_overwrites_existing(tmp_path: Path):
    """User can resubmit feedback — last write wins."""
    store = _store(tmp_path)
    store.record_from_alert(_alert("a1"))
    store.record_feedback("a1", feedback={"reason": "empty_frame"})
    store.record_feedback("a1", feedback={"reason": "wrong_identity"})
    meta = store.get("a1")
    assert meta is not None
    assert meta["feedback"]["reason"] == "wrong_identity"


# ─── mark_dismissed ────────────────────────────────────────────────


def test_mark_dismissed_stamps_meta(tmp_path: Path):
    store = _store(tmp_path)
    store.record_from_alert(_alert("a1"))
    assert store.mark_dismissed("a1") is True
    meta = store.get("a1")
    assert meta is not None
    assert meta["dismissed"] is True
    assert "dismissed_at" in meta


def test_mark_dismissed_returns_false_for_unknown(tmp_path: Path):
    assert _store(tmp_path).mark_dismissed("ghost") is False


# ─── list_recent ────────────────────────────────────────────────────


def test_list_recent_returns_empty_when_root_missing(tmp_path: Path):
    """Pre-first-event: no dir exists yet. Don't crash, return []."""
    assert _store(tmp_path).list_recent() == []


def test_list_recent_sorted_by_recorded_at_desc(tmp_path: Path):
    store = _store(tmp_path)
    store.record_from_alert(_alert("old", recorded_at="2026-05-01T10:00:00+00:00"))
    store.record_from_alert(_alert("new", recorded_at="2026-05-28T10:00:00+00:00"))
    store.record_from_alert(_alert("mid", recorded_at="2026-05-15T10:00:00+00:00"))
    ids = [e["event_id"] for e in store.list_recent()]
    assert ids == ["new", "mid", "old"]


def test_list_recent_honors_limit(tmp_path: Path):
    store = _store(tmp_path)
    for i in range(5):
        store.record_from_alert(_alert(f"a{i}", recorded_at=f"2026-05-2{i}T00:00:00+00:00"))
    assert len(store.list_recent(limit=3)) == 3


def test_list_recent_skips_malformed_meta(tmp_path: Path):
    """A corrupt meta.json in one event dir shouldn't poison the
    whole listing."""
    store = _store(tmp_path)
    store.record_from_alert(_alert("good"))
    # Write garbage as a sibling event dir.
    bad_dir = tmp_path / "events" / "bad"
    bad_dir.mkdir()
    (bad_dir / "meta.json").write_text("{ not json", encoding="utf-8")
    ids = [e["event_id"] for e in store.list_recent()]
    assert ids == ["good"]
