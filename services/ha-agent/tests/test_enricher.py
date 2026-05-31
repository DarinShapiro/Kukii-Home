"""Unit tests for AlertEnricher (Epic 10.9).

Drives the enrichment logic directly via ``_enrich`` (deterministic,
no background tasks) plus a couple of ``on_alert`` scheduling checks.
Uses a stub PreprocessorClient + a real EventStore on tmp_path.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from kukiihome_ha_agent.enricher import (
    AlertEnricher,
    _event_unix_ts,
    _pick_best_frame_ts,
)
from kukiihome_ha_agent.event_store import EventStore
from kukiihome_shared.preprocessor import FrameWindow


def _entity(frame_ts: float, actor: str = "alice") -> dict:
    return {
        "frame_ts": frame_ts,
        "kind": "person",
        "actor_id": actor,
        "actor_name": actor.title(),
        "bbox": (0.1, 0.1, 0.5, 0.9),
        "detection_confidence": 0.9,
        "identity_confidence": 0.92,
        "identity_method": "face_arcface",
        "track_id": "t1",
    }


def _detection(frame_ts: float, kind: str = "person") -> dict:
    return {
        "kind": kind,
        "confidence": 0.9,
        "bbox": (0.1, 0.1, 0.5, 0.9),
        "frame_ts": frame_ts,
    }


def _frame(ts: float, *, annotated: bool = True, quality: float = 0.8) -> dict:
    return {
        "ts": ts,
        "uri": f"http://localhost:8090/frames/c/{ts:.3f}/frame.jpg",
        "annotated_uri": (
            f"http://localhost:8090/frames/c/{ts:.3f}/annotated.jpg" if annotated else None
        ),
        "quality_score": quality,
    }


def _window(**kw) -> FrameWindow:
    base = {
        "camera_id": "front_porch",
        "ts_start": 100.0,
        "ts_end": 105.0,
        "frames": (),
        "detections": (),
        "identified_entities": (),
        "actor_matches": (),
    }
    base.update(kw)
    return FrameWindow(**base)


class _StubClient:
    def __init__(self, window: FrameWindow | None, image: bytes | None = b"ANNO"):
        self._window = window
        self._image = image
        self.window_calls: list[dict] = []
        self.image_calls: list[str] = []

    async def get_frame_window(self, *, camera_id, ts_start, ts_end, enrich=True):
        self.window_calls.append({"camera_id": camera_id, "ts_start": ts_start, "ts_end": ts_end})
        return self._window

    async def fetch_frame_image(self, uri):
        self.image_calls.append(uri)
        return self._image


def _store(tmp_path: Path) -> EventStore:
    store = EventStore(root=tmp_path / "events")
    store.record_from_alert(
        {
            "alert_id": "evt1",
            "camera_id": "front_porch",
            "ha_last_changed": "2026-05-28T15:30:00+00:00",
            "recorded_at": "2026-05-28T15:30:02+00:00",
            "headline": "Person",
        }
    )
    return store


# ─── _enrich happy path ─────────────────────────────────────────────


async def test_enrich_records_detections_identities_and_annotated(tmp_path: Path):
    store = _store(tmp_path)
    window = _window(
        frames=(_frame(102.0),),
        detections=(_detection(102.0),),
        identified_entities=(_entity(102.0),),
    )
    client = _StubClient(window, image=b"\xff\xd8\xff\xd9ANNOTATED")
    enricher = AlertEnricher(client=client, event_store=store)

    await enricher._enrich("evt1", "front_porch", store.get("evt1"))

    meta = store.get("evt1")
    assert meta["enriched"] is True
    assert meta["detections"][0]["kind"] == "person"
    assert meta["identified_entities"][0]["actor_name"] == "Alice"
    # Annotated frame fetched + written.
    assert client.image_calls == ["http://localhost:8090/frames/c/102.000/annotated.jpg"]
    assert store.frame_path("evt1", annotated=True).read_bytes() == b"\xff\xd8\xff\xd9ANNOTATED"


async def test_enrich_queries_window_around_ha_last_changed(tmp_path: Path):
    store = _store(tmp_path)
    client = _StubClient(_window(detections=(_detection(102.0),)))
    enricher = AlertEnricher(client=client, event_store=store)

    await enricher._enrich("evt1", "front_porch", store.get("evt1"))

    event_ts = datetime.fromisoformat("2026-05-28T15:30:00+00:00").timestamp()
    call = client.window_calls[0]
    assert call["camera_id"] == "front_porch"
    assert call["ts_start"] == event_ts - 4.0
    assert call["ts_end"] == event_ts + 2.0


# ─── graceful degradation ───────────────────────────────────────────


async def test_enrich_noop_when_preprocessor_unreachable(tmp_path: Path):
    store = _store(tmp_path)
    client = _StubClient(None)  # get_frame_window returns None
    enricher = AlertEnricher(client=client, event_store=store)

    await enricher._enrich("evt1", "front_porch", store.get("evt1"))

    meta = store.get("evt1")
    assert "enriched" not in meta  # untouched
    assert client.image_calls == []


async def test_enrich_noop_on_empty_window(tmp_path: Path):
    """Preprocessor reachable but saw nothing (camera silent / not
    ingested there) → no enrichment recorded."""
    store = _store(tmp_path)
    client = _StubClient(_window())  # no detections, no identities
    enricher = AlertEnricher(client=client, event_store=store)

    await enricher._enrich("evt1", "front_porch", store.get("evt1"))

    assert "enriched" not in store.get("evt1")


async def test_enrich_records_detections_even_without_identities(tmp_path: Path):
    """A detected-but-unrecognized person still enriches the alert
    with the detection list — there's just no annotated frame."""
    store = _store(tmp_path)
    client = _StubClient(_window(detections=(_detection(102.0),)))
    enricher = AlertEnricher(client=client, event_store=store)

    await enricher._enrich("evt1", "front_porch", store.get("evt1"))

    meta = store.get("evt1")
    assert meta["enriched"] is True
    assert meta["detections"][0]["kind"] == "person"
    # No identified entities → no annotated frame fetched.
    assert client.image_calls == []
    assert store.frame_path("evt1", annotated=True) is None


# ─── best-frame selection ───────────────────────────────────────────


async def test_enrich_picks_frame_with_most_identities(tmp_path: Path):
    store = _store(tmp_path)
    window = _window(
        frames=(_frame(101.0), _frame(102.0)),
        identified_entities=(
            _entity(101.0, "alice"),
            _entity(102.0, "alice"),
            _entity(102.0, "bob"),  # ts=102 has 2 → should win
        ),
    )
    client = _StubClient(window)
    enricher = AlertEnricher(client=client, event_store=store)

    await enricher._enrich("evt1", "front_porch", store.get("evt1"))

    assert client.image_calls == ["http://localhost:8090/frames/c/102.000/annotated.jpg"]


# ─── on_alert scheduling ────────────────────────────────────────────


async def test_on_alert_schedules_task(tmp_path: Path):
    store = _store(tmp_path)
    client = _StubClient(_window(detections=(_detection(102.0),)))
    enricher = AlertEnricher(client=client, event_store=store)

    enricher.on_alert(store.get("evt1"))
    # Let the fire-and-forget task run.
    await asyncio.sleep(0)
    await asyncio.gather(*list(enricher._pending_tasks), return_exceptions=True)
    assert len(client.window_calls) == 1


async def test_on_alert_noop_without_camera_or_id(tmp_path: Path):
    store = _store(tmp_path)
    client = _StubClient(_window())
    enricher = AlertEnricher(client=client, event_store=store)

    enricher.on_alert({"alert_id": "evt1"})  # no camera_id
    enricher.on_alert({"camera_id": "c"})  # no alert_id
    await asyncio.sleep(0)
    assert enricher._pending_tasks == set()
    assert client.window_calls == []


# ─── helpers ────────────────────────────────────────────────────────


def test_event_unix_ts_prefers_ha_last_changed():
    ts = _event_unix_ts(
        {
            "ha_last_changed": "2026-05-28T15:30:00+00:00",
            "recorded_at": "2026-05-28T15:30:05+00:00",
        }
    )
    assert ts == datetime.fromisoformat("2026-05-28T15:30:00+00:00").timestamp()


def test_event_unix_ts_falls_back_to_recorded_at():
    ts = _event_unix_ts({"recorded_at": "2026-05-28T15:30:05+00:00"})
    assert ts == datetime.fromisoformat("2026-05-28T15:30:05+00:00").timestamp()


def test_event_unix_ts_none_when_unparseable():
    assert _event_unix_ts({"ha_last_changed": "not-a-date"}) is None
    assert _event_unix_ts({}) is None


def test_pick_best_frame_ts_none_without_identities():
    assert _pick_best_frame_ts(_window(detections=(_detection(1.0),))) is None
