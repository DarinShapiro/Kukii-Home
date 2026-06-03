"""IdentityStore + /identity API — the Review-UI backend.

Seeds a detections.db with detections + always-embedded track_embeddings (the
worker's output shape), then drives the full loop the operator UI performs:
list unresolved tracks → label one → it (re)resolves retroactively → the track
shows resolved + the subject appears.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient
from kukiihome_preprocessor.app import AppState, create_app
from kukiihome_preprocessor.detection_store import (
    DetectionRow,
    DetectionStore,
    EmbeddingRow,
)
from kukiihome_preprocessor.identity_store import IdentityStore
from kukiihome_preprocessor.state import ActorCache


def _unit(vec) -> np.ndarray:
    a = np.asarray(vec, dtype=np.float32)
    return a / np.linalg.norm(a)


def _seed(db_path) -> tuple[DetectionStore, IdentityStore]:
    """Two tracks on the 'pool' cam: t1 (person, body), d1 (dog, pet)."""
    ds = DetectionStore(db_path)
    ds.register_event(event_id="e1", camera_id="pool", captured_ts=100.0)
    ds.add_detections([
        DetectionRow("e1", "pool", 5.0, "f5.jpg", "person", 0.9, (0.1, 0.1, 0.5, 0.9), "t1"),
        DetectionRow("e1", "pool", 6.0, "f6.jpg", "person", 0.8, (0.1, 0.1, 0.5, 0.9), "t1"),
        DetectionRow("e1", "pool", 5.0, "f5.jpg", "dog", 0.7, (0.6, 0.6, 0.9, 0.9), "d1"),
    ])
    alice = _unit([0.2, 0.9, 0.1, 0.3])
    rex = _unit([0.7, 0.1, 0.2, 0.4])
    ds.add_embeddings([
        EmbeddingRow("e1", "pool", "t1", 5.0, "body", "body_id_osnet", alice),
        EmbeddingRow("e1", "pool", "t1", 6.0, "body", "body_id_osnet", alice),
        EmbeddingRow("e1", "pool", "d1", 5.0, "pet", "pet_dinov2", rex),
    ])
    ds.mark_enriched("e1", 130.0)
    return ds, IdentityStore(db_path)


# ─── store: track summaries ─────────────────────────────────────────


def test_track_summaries_group_and_classify(tmp_path):
    _ds, idn = _seed(tmp_path / "det.db")
    tracks = {t.track_id: t for t in idn.track_summaries()}
    assert set(tracks) == {"t1", "d1"}
    assert tracks["t1"].kind == "person" and tracks["t1"].modalities == ["body"]
    assert tracks["t1"].n_frames == 2
    assert tracks["d1"].kind == "pet" and tracks["d1"].modalities == ["pet"]
    # all unresolved before any labelling
    assert all(t.status == "unresolved" for t in tracks.values())
    # kind filter
    assert {t.track_id for t in idn.track_summaries(kind="pet")} == {"d1"}
    assert {t.track_id for t in idn.track_summaries(kind="person")} == {"t1"}


# ─── store: label → enroll → retroactive resolve ────────────────────


def test_label_enrolls_and_resolves(tmp_path):
    ds, idn = _seed(tmp_path / "det.db")
    sid = idn.upsert_subject(display_name="Alice", kind="person")
    enrolled = idn.enroll_from_track(ds, subject_id=sid, event_id="e1", track_id="t1")
    assert enrolled == ["body"]
    matched = idn.resolve_all(ds)
    assert matched >= 2  # t1 across its two body frames

    summaries = {t.track_id: t for t in idn.track_summaries()}
    assert summaries["t1"].status == "resolved"
    assert summaries["t1"].subject_name == "Alice"
    assert summaries["d1"].status == "unresolved"  # pet not enrolled yet

    # filter by status
    assert {t.track_id for t in idn.track_summaries(status="resolved")} == {"t1"}
    assert {t.track_id for t in idn.track_summaries(status="unresolved")} == {"d1"}

    subs = {s.display_name: s for s in idn.list_subjects()}
    assert "Alice" in subs and subs["Alice"].modalities == ["body"]
    assert subs["Alice"].appearances == 1  # one (event, track)


def test_resolutions_are_idempotent(tmp_path):
    ds, idn = _seed(tmp_path / "det.db")
    sid = idn.upsert_subject(display_name="Alice", kind="person")
    idn.enroll_from_track(ds, subject_id=sid, event_id="e1", track_id="t1")
    first = idn.resolve_all(ds)
    second = idn.resolve_all(ds)  # re-run: upsert, no duplicate rows
    assert first == second
    rows = idn._conn.execute("SELECT COUNT(*) FROM resolutions").fetchone()[0]
    assert rows == first


# ─── API: TestClient over the wired app ─────────────────────────────


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "det.db"
    ds, idn = _seed(db)
    state = AppState(
        config=_min_config(), cache=ActorCache(), frame_buffer=object(),
        started_ts=0.0, detection_store=ds, identity_store=idn,
        event_store_dir=str(tmp_path / "events"),
    )
    return TestClient(create_app(state))


def _min_config():
    from kukiihome_preprocessor.config import PreprocessorConfig

    return PreprocessorConfig(cameras=["pool"])


def test_api_tracks_and_label_flow(client):
    r = client.get("/identity/tracks")
    assert r.status_code == 200
    tracks = r.json()["tracks"]
    assert {t["track_id"] for t in tracks} == {"t1", "d1"}
    t1 = next(t for t in tracks if t["track_id"] == "t1")
    assert t1["status"] == "unresolved"
    assert t1["thumb_url"] == "identity/tracks/e1/t1/thumb.jpg"

    # label t1 as Alice → enroll + resolve
    r = client.post("/identity/label", json={"event_id": "e1", "track_id": "t1", "name": "Alice"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subject_id"] == "alice"
    assert body["enrolled_modalities"] == ["body"]
    assert body["matched"] >= 2

    # now t1 reads resolved; subject listed
    t1 = next(t for t in client.get("/identity/tracks").json()["tracks"] if t["track_id"] == "t1")
    assert t1["status"] == "resolved" and t1["subject_name"] == "Alice"
    subs = client.get("/identity/subjects").json()["subjects"]
    assert any(s["display_name"] == "Alice" and s["kind"] == "person" for s in subs)


def test_api_label_pet_derives_kind(client):
    r = client.post("/identity/label", json={"event_id": "e1", "track_id": "d1", "name": "Rex"})
    assert r.status_code == 200, r.text
    assert r.json()["enrolled_modalities"] == ["pet"]
    subs = {s["display_name"]: s for s in client.get("/identity/subjects").json()["subjects"]}
    assert subs["Rex"]["kind"] == "pet"  # derived from the dog detection


def test_api_thumb_404_without_frame_on_disk(client):
    # event_store_dir points at a non-existent tree → crop returns 404, not 500.
    r = client.get("/identity/tracks/e1/t1/thumb.jpg")
    assert r.status_code == 404


# ─── Feature 2: label folds into the live recognition cache ──────────


def test_build_enrollment_event_carries_templates(tmp_path):
    ds, idn = _seed(tmp_path / "det.db")
    sid = idn.upsert_subject(display_name="Alice", kind="person")
    idn.enroll_from_track(ds, subject_id=sid, event_id="e1", track_id="t1")
    ev = idn.build_enrollment_event(sid)
    assert ev is not None
    assert ev.actor_id == "alice" and ev.action == "enrolled" and ev.name == "Alice"
    assert ev.body_embedding is not None and len(ev.body_embedding) == 4
    assert ev.pet_dinov2_centroid is None
    assert idn.build_enrollment_event("ghost") is None


def test_api_label_updates_live_cache(client):
    import asyncio

    client.post("/identity/label", json={"event_id": "e1", "track_id": "t1", "name": "Alice"})
    cache = client.app.state.app_state.cache
    actors = asyncio.run(cache.snapshot())
    alice = next((a for a in actors if a.actor_id == "alice"), None)
    assert alice is not None and alice.body_embedding is not None


# ─── Feature 3: merge / split corrections ───────────────────────────


def test_reject_track_returns_to_queue(tmp_path):
    ds, idn = _seed(tmp_path / "det.db")
    sid = idn.upsert_subject(display_name="Alice", kind="person")
    idn.enroll_from_track(ds, subject_id=sid, event_id="e1", track_id="t1")
    idn.resolve_all(ds)
    assert {t.track_id for t in idn.track_summaries(status="resolved")} == {"t1"}

    n = idn.reject_track("e1", "t1")
    assert n >= 1
    assert {t.track_id for t in idn.track_summaries(status="resolved")} == set()
    assert "t1" in {t.track_id for t in idn.track_summaries(status="unresolved")}
    # appearance no longer counts a rejected resolution
    assert {s.display_name: s.appearances for s in idn.list_subjects()}["Alice"] == 0


def test_merge_repoints_and_deactivates(tmp_path):
    from kukiihome_shared.preprocessor import ActorMatch

    ds, idn = _seed(tmp_path / "det.db")
    a = idn.upsert_subject(display_name="Alice", kind="person")
    idn.enroll_from_track(ds, subject_id=a, event_id="e1", track_id="t1")
    b = idn.upsert_subject(display_name="Bob", kind="person")
    idn.enroll_from_track(ds, subject_id=b, event_id="e1", track_id="t1")
    idn.persist_resolutions(
        (ActorMatch(actor_id="bob", confidence=0.8, match_method="body_id_osnet",
                    frame_ts=5.0, track_id="t1"),),
        camera_id="pool", event_id="e1",
    )

    assert idn.merge_subjects("bob", "alice") is True
    assert {s.display_name for s in idn.list_subjects()} == {"Alice"}  # bob deactivated
    row = idn._conn.execute(
        "SELECT subject_id FROM resolutions WHERE track_id='t1'"
    ).fetchone()
    assert row["subject_id"] == "alice"  # repointed
    assert idn._conn.execute(
        "SELECT COUNT(*) FROM subject_templates WHERE subject_id='bob'"
    ).fetchone()[0] == 0  # bob's templates folded away


def test_merge_guards(tmp_path):
    ds, idn = _seed(tmp_path / "det.db")
    a = idn.upsert_subject(display_name="Alice", kind="person")
    idn.enroll_from_track(ds, subject_id=a, event_id="e1", track_id="t1")
    r = idn.upsert_subject(display_name="Rex", kind="pet")
    idn.enroll_from_track(ds, subject_id=r, event_id="e1", track_id="d1")
    with pytest.raises(ValueError):
        idn.merge_subjects("rex", "alice")          # cross-kind
    assert idn.merge_subjects("alice", "alice") is False  # self
    assert idn.merge_subjects("ghost", "alice") is False  # unknown


def _d1(client):
    return next(
        t for t in client.get("/identity/tracks").json()["tracks"] if t["track_id"] == "d1"
    )


def test_relabel_after_reject_overrides(client):
    """A label must override a prior reject on the same track — otherwise the
    'rejected' verdict (which persist_resolutions preserves) would leave the
    track stuck unresolved forever. The mechanism behind the false-merge fix:
    reject the wrong resolution, then label the track with the right identity."""
    client.post("/identity/label", json={"event_id": "e1", "track_id": "d1", "name": "Rex"})
    assert _d1(client)["status"] == "resolved" and _d1(client)["subject_name"] == "Rex"

    client.post("/identity/reject", json={"event_id": "e1", "track_id": "d1"})
    assert _d1(client)["status"] == "unresolved"  # rejected → back in the queue

    # re-label — must clear the rejection and resolve again (would stay
    # 'unresolved' without clear_track_resolutions).
    client.post("/identity/label", json={"event_id": "e1", "track_id": "d1", "name": "Rex"})
    assert _d1(client)["status"] == "resolved" and _d1(client)["subject_name"] == "Rex"


def test_api_reject_and_merge_endpoints(client):
    client.post("/identity/label", json={"event_id": "e1", "track_id": "t1", "name": "Alice"})
    assert any(
        t["status"] == "resolved"
        for t in client.get("/identity/tracks").json()["tracks"] if t["track_id"] == "t1"
    )
    r = client.post("/identity/reject", json={"event_id": "e1", "track_id": "t1"})
    assert r.status_code == 200 and r.json()["rejected"] >= 1
    assert all(
        t["status"] == "unresolved"
        for t in client.get("/identity/tracks").json()["tracks"] if t["track_id"] == "t1"
    )

    # cross-kind merge rejected (400); unknown subject 404.
    client.post("/identity/label", json={"event_id": "e1", "track_id": "t1", "name": "Alice"})
    client.post("/identity/label", json={"event_id": "e1", "track_id": "d1", "name": "Rex"})
    assert client.post(
        "/identity/subjects/merge", json={"from_id": "rex", "into_id": "alice"}
    ).status_code == 400
    assert client.post(
        "/identity/subjects/merge", json={"from_id": "ghost", "into_id": "alice"}
    ).status_code == 404
