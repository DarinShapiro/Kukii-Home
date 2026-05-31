"""Unit tests for PreprocessorClient (Epic 10.9).

Uses httpx.MockTransport to intercept requests without a live server,
so we can assert on URL construction + graceful failure handling.
"""

from __future__ import annotations

import httpx
from kukiihome_ha_agent.preprocessor_client import PreprocessorClient
from kukiihome_shared.preprocessor import FrameWindow


def _client_with_handler(handler) -> PreprocessorClient:
    """Build a PreprocessorClient whose httpx session is backed by a
    MockTransport handler. ``self._base`` is still used for URL
    construction, so we can assert on the path the client builds."""
    c = PreprocessorClient("http://inference.box:8090")
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


def _sample_window() -> FrameWindow:
    return FrameWindow(
        camera_id="front_porch",
        ts_start=100.0,
        ts_end=105.0,
        frames=(
            {
                "ts": 102.0,
                "uri": "http://localhost:8090/frames/front_porch/102.000/frame.jpg",
                "annotated_uri": "http://localhost:8090/frames/front_porch/102.000/annotated.jpg",
                "quality_score": 0.8,
            },
        ),
        detections=(
            {"kind": "person", "confidence": 0.9, "bbox": (0.1, 0.1, 0.5, 0.9), "frame_ts": 102.0},
        ),
        identified_entities=(
            {
                "frame_ts": 102.0,
                "kind": "person",
                "actor_id": "alice",
                "actor_name": "Alice",
                "bbox": (0.1, 0.1, 0.5, 0.9),
                "detection_confidence": 0.9,
                "identity_confidence": 0.92,
                "identity_method": "face_arcface",
                "track_id": "t1",
            },
        ),
    )


# ─── get_frame_window ───────────────────────────────────────────────


async def test_get_frame_window_parses_payload_and_builds_url():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=_sample_window().model_dump_json())

    client = _client_with_handler(handler)
    fw = await client.get_frame_window(camera_id="front_porch", ts_start=100.0, ts_end=105.0)
    assert fw is not None
    assert fw.camera_id == "front_porch"
    assert len(fw.identified_entities) == 1
    assert fw.identified_entities[0].actor_name == "Alice"
    # URL is built off our base + query params.
    assert seen["url"].startswith("http://inference.box:8090/frame_window")
    assert "camera_id=front_porch" in seen["url"]
    assert "enrich=true" in seen["url"]
    await client.close()


async def test_get_frame_window_returns_none_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _client_with_handler(handler)
    assert (await client.get_frame_window(camera_id="c", ts_start=1.0, ts_end=2.0)) is None
    await client.close()


async def test_get_frame_window_returns_none_on_bad_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "a frame window"})

    client = _client_with_handler(handler)
    assert (await client.get_frame_window(camera_id="c", ts_start=1.0, ts_end=2.0)) is None
    await client.close()


async def test_get_frame_window_returns_none_when_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client_with_handler(handler)
    assert (await client.get_frame_window(camera_id="c", ts_start=1.0, ts_end=2.0)) is None
    await client.close()


# ─── fetch_frame_image ──────────────────────────────────────────────


async def test_fetch_frame_image_uses_path_not_uri_host():
    """The annotated_uri may carry a localhost/misconfigured host. The
    client must re-target its own base_url using only the URI path."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"\xff\xd8\xff\xd9JPEG")

    client = _client_with_handler(handler)
    data = await client.fetch_frame_image(
        "http://localhost:8090/frames/front_porch/102.000/annotated.jpg"
    )
    assert data == b"\xff\xd8\xff\xd9JPEG"
    # Re-targeted to OUR base host, preserving the path.
    assert seen["url"] == ("http://inference.box:8090/frames/front_porch/102.000/annotated.jpg")
    await client.close()


async def test_fetch_frame_image_returns_none_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client_with_handler(handler)
    assert await client.fetch_frame_image("http://x/y.jpg") is None
    await client.close()


async def test_fetch_frame_image_none_for_empty_uri():
    client = _client_with_handler(lambda r: httpx.Response(200, content=b"x"))
    assert await client.fetch_frame_image("") is None
    await client.close()
