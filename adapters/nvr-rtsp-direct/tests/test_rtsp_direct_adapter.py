"""Tests for RTSPDirectAdapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sentihome_adapter_rtsp_direct import CameraConfig, RTSPDirectAdapter
from sentihome_shared.adapter import PreprocessingMode, UnsupportedCapability
from sentihome_shared.adapter.base import FrameWindow, MotionEvent


class _FakeFrameBuffer:
    """In-memory frame buffer for testing."""

    def __init__(self) -> None:
        self.get_window_calls: list[tuple[str, datetime, datetime]] = []
        self.events_to_yield: list[MotionEvent] = []

    async def get_window(
        self,
        camera_id: str,
        ts_start: datetime,
        ts_end: datetime,
        *,
        with_metadata: bool,
    ) -> FrameWindow:
        self.get_window_calls.append((camera_id, ts_start, ts_end))
        return FrameWindow(
            camera_id=camera_id,
            ts_start=ts_start,
            ts_end=ts_end,
            frames=[],
            metadata={"preprocessing_mode": "direct", "preprocessing_latency_ms": 5},
        )

    async def subscribe(self, camera_id: str | None) -> AsyncIterator[MotionEvent]:
        for event in self.events_to_yield:
            if camera_id is None or event.camera_id == camera_id:
                yield event


CAM_FRONT = CameraConfig(
    camera_id="front_door",
    rtsp_url="rtsp://192.168.1.10:554/main",
    substream_url="rtsp://192.168.1.10:554/sub",
    onvif_url="http://192.168.1.10:80",
    name="Front Door",
    width=1920,
    height=1080,
    fps=15,
    supports_ptz=False,
    audio=True,
)
CAM_BACK = CameraConfig(
    camera_id="backyard",
    rtsp_url="rtsp://192.168.1.11:554/main",
    supports_ptz=True,
)


pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────
# list_cameras
# ─────────────────────────────────────────────────────────────────────


async def test_list_cameras_returns_all_configured() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT, CAM_BACK])
    cams = await adapter.list_cameras()
    assert {c.camera_id for c in cams} == {"front_door", "backyard"}


async def test_list_cameras_advertises_direct_mode() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT])
    cams = await adapter.list_cameras()
    assert cams[0].preprocessing_mode == PreprocessingMode.DIRECT


async def test_list_cameras_reports_ptz_capability() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT, CAM_BACK])
    cams = {c.camera_id: c for c in await adapter.list_cameras()}
    assert cams["front_door"].ptz is False
    assert cams["backyard"].ptz is True


async def test_list_cameras_includes_substream_when_configured() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT, CAM_BACK])
    cams = {c.camera_id: c for c in await adapter.list_cameras()}
    assert "substream" in cams["front_door"].stream_profiles
    assert "substream" not in cams["backyard"].stream_profiles


# ─────────────────────────────────────────────────────────────────────
# get_frame_window
# ─────────────────────────────────────────────────────────────────────


async def test_get_frame_window_delegates_to_buffer() -> None:
    buf = _FakeFrameBuffer()
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT], frame_buffer=buf)

    ts_start = datetime(2026, 5, 25, 14, 0, 0, tzinfo=UTC)
    ts_end = datetime(2026, 5, 25, 14, 0, 10, tzinfo=UTC)
    window = await adapter.get_frame_window("front_door", ts_start, ts_end)

    assert window.camera_id == "front_door"
    assert window.metadata["preprocessing_mode"] == "direct"
    assert buf.get_window_calls == [("front_door", ts_start, ts_end)]


async def test_get_frame_window_unknown_camera_raises() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT])
    with pytest.raises(KeyError):
        await adapter.get_frame_window(
            "nonexistent",
            datetime.now(UTC),
            datetime.now(UTC),
        )


async def test_get_frame_window_no_buffer_returns_empty_with_note() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT])  # no buffer
    window = await adapter.get_frame_window(
        "front_door",
        datetime(2026, 5, 25, tzinfo=UTC),
        datetime(2026, 5, 25, tzinfo=UTC),
    )
    assert window.frames == []
    assert "note" in window.metadata


# ─────────────────────────────────────────────────────────────────────
# get_stream_url
# ─────────────────────────────────────────────────────────────────────


async def test_get_stream_url_default_main() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT])
    url = await adapter.get_stream_url("front_door")
    assert url == "rtsp://192.168.1.10:554/main"


async def test_get_stream_url_substream() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT])
    url = await adapter.get_stream_url("front_door", profile="substream")
    assert url == "rtsp://192.168.1.10:554/sub"


async def test_get_stream_url_falls_back_when_no_substream() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_BACK])  # no substream
    url = await adapter.get_stream_url("backyard", profile="substream")
    assert url == "rtsp://192.168.1.11:554/main"


# ─────────────────────────────────────────────────────────────────────
# Subscribe (motion events)
# ─────────────────────────────────────────────────────────────────────


async def test_subscribe_motion_events_yields_buffer_events() -> None:
    buf = _FakeFrameBuffer()
    buf.events_to_yield = [
        MotionEvent(
            camera_id="front_door",
            timestamp=datetime.now(UTC),
            event_type="motion",
            confidence=0.7,
        ),
        MotionEvent(
            camera_id="backyard",
            timestamp=datetime.now(UTC),
            event_type="person",
            confidence=0.9,
        ),
    ]
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT, CAM_BACK], frame_buffer=buf)

    received = [e async for e in adapter.subscribe_motion_events()]
    assert len(received) == 2


async def test_subscribe_motion_events_filters_by_camera() -> None:
    buf = _FakeFrameBuffer()
    buf.events_to_yield = [
        MotionEvent(camera_id="front_door", timestamp=datetime.now(UTC), event_type="motion"),
        MotionEvent(camera_id="backyard", timestamp=datetime.now(UTC), event_type="motion"),
    ]
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT, CAM_BACK], frame_buffer=buf)

    received = [e async for e in adapter.subscribe_motion_events(camera_id="front_door")]
    assert len(received) == 1
    assert received[0].camera_id == "front_door"


# ─────────────────────────────────────────────────────────────────────
# Capability gating
# ─────────────────────────────────────────────────────────────────────


async def test_slew_ptz_raises_unsupported_by_default() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT])
    with pytest.raises(UnsupportedCapability):
        await adapter.slew_ptz("front_door", "preset_1")


async def test_enrich_frame_raises_unsupported_by_default() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT])
    with pytest.raises(UnsupportedCapability):
        await adapter.enrich_frame("front_door", "frame://test")


# ─────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────


async def test_start_stop_idempotent() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT])
    await adapter.start()
    await adapter.start()  # idempotent
    await adapter.stop()
    await adapter.stop()  # idempotent


async def test_adapter_name_and_mode() -> None:
    adapter = RTSPDirectAdapter(cameras=[CAM_FRONT])
    assert adapter.name == "adapter-rtsp-direct"
    assert adapter.mode == PreprocessingMode.DIRECT
