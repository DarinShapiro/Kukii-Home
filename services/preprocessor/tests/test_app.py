"""FastAPI surface tests against an isolated AppState.

No NATS, no background tasks: build a minimal AppState directly
and exercise the routes. The synthetic frame buffer is real (it
has no I/O — it's pure synthesis from inputs).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from kukiihome_preprocessor.app import AppState, create_app
from kukiihome_preprocessor.config import PreprocessorConfig
from kukiihome_preprocessor.pipelines.synthetic_frames import SyntheticFrameBuffer
from kukiihome_preprocessor.state import ActorCache


@pytest.fixture
def app_state() -> AppState:
    config = PreprocessorConfig(
        node_id="test-node",
        cameras=["front_porch", "driveway_cam"],
    )
    return AppState(
        config=config,
        cache=ActorCache(),
        frame_buffer=SyntheticFrameBuffer(
            configured_cameras=config.cameras,
            node_id=config.node_id,
            frames_per_second=2.0,
            buffer_horizon_seconds=300.0,
        ),
        started_ts=time.time(),
    )


@pytest.fixture
def client(app_state: AppState) -> TestClient:
    return TestClient(create_app(app_state))


# ─── /healthz ────────────────────────────────────────────────────────


def test_healthz_returns_ok(client: TestClient):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ─── /status ─────────────────────────────────────────────────────────


def test_status_returns_expected_shape(client: TestClient, app_state: AppState):
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["healthy"] is True
    assert body["cameras_total"] == len(app_state.config.cameras)
    assert body["cameras_active"] == body["cameras_total"]
    assert body["frame_windows_served_total"] == 0
    assert body["actors_cached"] == 0
    assert body["schema_version"] == "v1"
    assert "uptime_seconds" in body


# ─── /frame_window — the primary RPC ─────────────────────────────────


def test_frame_window_returns_frames_within_window(client: TestClient):
    """For a 2-second window at 2 fps, expect ~4 frames."""
    now = time.time()
    r = client.get(
        "/frame_window",
        params={
            "camera_id": "front_porch",
            "ts_start": now - 2.0,
            "ts_end": now,
            "enrich": "true",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["camera_id"] == "front_porch"
    assert body["preprocessor_node_id"] == "test-node"
    assert body["enrichment_mode"] == "enriched"
    # 2 seconds @ 2 fps → 4 frames.
    assert len(body["frames"]) == 4
    for frame in body["frames"]:
        assert frame["uri"].startswith("synthetic://front_porch/")
        assert frame["quality_score"] is not None


def test_frame_window_unenriched_omits_detections(client: TestClient):
    now = time.time()
    r = client.get(
        "/frame_window",
        params={
            "camera_id": "front_porch",
            "ts_start": now - 1.0,
            "ts_end": now,
            "enrich": "false",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["enrichment_mode"] == "frames_only"
    assert body["detections"] == []
    assert body["actor_matches"] == []
    # Frames are still returned; their quality_score is None
    # because the enrichment pass is what computes it.
    for frame in body["frames"]:
        assert frame["quality_score"] is None


def test_frame_window_unknown_camera_returns_empty(client: TestClient):
    """Out-of-config cameras don't error — they return empty,
    matching the production semantics where the buffer simply
    has nothing for that camera."""
    now = time.time()
    r = client.get(
        "/frame_window",
        params={
            "camera_id": "ghost_cam",
            "ts_start": now - 1.0,
            "ts_end": now,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["frames"] == []
    assert body["detections"] == []


def test_frame_window_too_old_returns_empty(client: TestClient):
    """Windows older than the buffer horizon (300s default) return
    empty — mirrors rolling-buffer aging."""
    r = client.get(
        "/frame_window",
        params={
            "camera_id": "front_porch",
            "ts_start": 0.0,
            "ts_end": 1.0,
        },
    )
    assert r.status_code == 200
    assert r.json()["frames"] == []


def test_frame_window_increments_served_counter(client: TestClient, app_state: AppState):
    now = time.time()
    for _ in range(3):
        client.get(
            "/frame_window",
            params={
                "camera_id": "front_porch",
                "ts_start": now - 1.0,
                "ts_end": now,
            },
        )
    status = client.get("/status").json()
    assert status["frame_windows_served_total"] == 3
    assert app_state.frame_windows_served_total == 3


def test_frame_window_rejects_missing_params(client: TestClient):
    r = client.get("/frame_window", params={"camera_id": "front_porch"})
    assert r.status_code == 422


# ─── /tune ───────────────────────────────────────────────────────────


def test_tune_records_knob(client: TestClient, app_state: AppState):
    r = client.post(
        "/tune",
        json={
            "knob_id": "face.match_threshold",
            "new_value": 0.62,
            "rationale": "VLM reported too many false positives on pool_cam",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"status": "applied", "knob_id": "face.match_threshold"}
    assert app_state.applied_knobs is not None
    assert "face.match_threshold" in app_state.applied_knobs


def test_tune_with_scope_keys_by_camera(client: TestClient, app_state: AppState):
    client.post("/tune", json={"knob_id": "face.match_threshold", "new_value": 0.7})
    client.post(
        "/tune",
        json={
            "knob_id": "face.match_threshold",
            "new_value": 0.55,
            "scope_camera_id": "pool_cam",
        },
    )
    assert app_state.applied_knobs is not None
    assert "face.match_threshold" in app_state.applied_knobs
    assert "face.match_threshold@pool_cam" in app_state.applied_knobs


def test_tune_rejects_malformed_payload(client: TestClient):
    r = client.post("/tune", json={"knob_id": "x"})
    assert r.status_code == 422


# ─── /actors/enroll ──────────────────────────────────────────────────


def test_enroll_caches_actor(client: TestClient):
    r = client.post(
        "/actors/enroll",
        json={
            "actor_id": "actor_alice",
            "action": "enrolled",
            "name": "Alice",
            "role": "resident",
            "access_profile": "full",
            "face_embedding": [0.1 * i for i in range(4)],
        },
    )
    assert r.status_code == 200
    assert r.json() == {"status": "cached", "actor_id": "actor_alice"}


def test_deactivate_removes_actor(client: TestClient):
    client.post("/actors/enroll", json={"actor_id": "actor_alice", "action": "enrolled"})
    r = client.post(
        "/actors/enroll",
        json={"actor_id": "actor_alice", "action": "deactivated"},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "deactivated", "actor_id": "actor_alice"}


def test_deactivate_of_unknown_actor_is_noop(client: TestClient):
    r = client.post(
        "/actors/enroll",
        json={"actor_id": "never_enrolled", "action": "deactivated"},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "noop", "actor_id": "never_enrolled"}


# ─── /frames/{camera_id}/{ts}.jpg ────────────────────────────────────


def test_frames_route_synthetic_backend_returns_404(client: TestClient):
    """Synthetic backend doesn't retain bytes — the FrameRef.uri it
    emits is a placeholder. The route should respond 404."""
    r = client.get("/frames/front_porch/123.456.jpg")
    assert r.status_code == 404


def test_frames_route_serves_bytes_from_rtsp_backend():
    """Wire the app against an RTSPFrameBuffer pre-populated with one
    frame; /frames should serve those bytes."""
    from kukiihome_preprocessor.pipelines.rolling_buffer import (
        BufferedFrame,
        RollingBuffer,
    )
    from kukiihome_preprocessor.pipelines.rtsp_frame_buffer import RTSPFrameBuffer

    rolling = RollingBuffer(horizon_seconds=60.0)

    # Seed synchronously via a tiny event loop — we just need one
    # entry in the buffer for the route to find.
    import asyncio as _asyncio

    payload = b"\xff\xd8\xff\xe0fake-jpeg"

    async def _seed() -> None:
        await rolling.write(
            "front_porch",
            BufferedFrame(ts=99.001, jpeg_bytes=payload, width=1280, height=720),
        )

    _asyncio.run(_seed())

    config = PreprocessorConfig(
        node_id="rtsp-test",
        cameras=["front_porch"],
        backend="rtsp",
    )
    rtsp_buffer = RTSPFrameBuffer(
        rolling_buffer=rolling,
        configured_cameras=config.cameras,
        node_id=config.node_id,
        external_base_url="http://example:8090",
    )
    state = AppState(
        config=config,
        cache=ActorCache(),
        frame_buffer=rtsp_buffer,
        started_ts=time.time(),
    )
    app_client = TestClient(create_app(state))

    r = app_client.get("/frames/front_porch/99.001.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == payload


def test_annotated_frames_route_synthetic_backend_returns_404(client: TestClient):
    """Synthetic backend doesn't render annotations."""
    r = client.get("/frames/front_porch/123.456/annotated.jpg")
    assert r.status_code == 404


def test_annotated_frames_route_serves_bytes_from_rtsp_backend():
    """RTSP backend with annotation cache: the route returns the
    cached annotated JPEG."""
    import asyncio as _asyncio

    from kukiihome_preprocessor.pipelines.rolling_buffer import AnnotationCache
    from kukiihome_preprocessor.pipelines.rtsp_frame_buffer import RTSPFrameBuffer

    ann_cache = AnnotationCache(horizon_seconds=60.0)
    payload = b"\xff\xd8\xff\xe0annotated-jpeg"

    async def _seed() -> None:
        await ann_cache.put("front_porch", 88.001, payload)

    _asyncio.run(_seed())

    from kukiihome_preprocessor.pipelines.rolling_buffer import RollingBuffer

    config = PreprocessorConfig(
        node_id="rtsp-test",
        cameras=["front_porch"],
        backend="rtsp",
    )
    rtsp_buffer = RTSPFrameBuffer(
        rolling_buffer=RollingBuffer(horizon_seconds=60.0),
        configured_cameras=config.cameras,
        node_id=config.node_id,
        external_base_url="http://example:8090",
        annotation_cache=ann_cache,
    )
    state = AppState(
        config=config,
        cache=ActorCache(),
        frame_buffer=rtsp_buffer,
        started_ts=time.time(),
    )
    app_client = TestClient(create_app(state))

    r = app_client.get("/frames/front_porch/88.001/annotated.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == payload


def test_frames_route_unknown_camera_returns_404():
    """RTSP backend: known route shape, unknown camera → 404."""
    from kukiihome_preprocessor.pipelines.rolling_buffer import RollingBuffer
    from kukiihome_preprocessor.pipelines.rtsp_frame_buffer import RTSPFrameBuffer

    rolling = RollingBuffer(horizon_seconds=60.0)
    config = PreprocessorConfig(cameras=["front_porch"], backend="rtsp")
    rtsp_buffer = RTSPFrameBuffer(
        rolling_buffer=rolling,
        configured_cameras=config.cameras,
        node_id="t",
        external_base_url="http://example:8090",
    )
    state = AppState(
        config=config,
        cache=ActorCache(),
        frame_buffer=rtsp_buffer,
        started_ts=time.time(),
    )
    app_client = TestClient(create_app(state))

    r = app_client.get("/frames/ghost_cam/100.0.jpg")
    assert r.status_code == 404
