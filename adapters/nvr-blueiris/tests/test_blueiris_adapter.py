"""Tests for BlueIrisAdapter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from kukiihome_adapter_blueiris import BlueIrisAdapter, BlueIrisConfig
from kukiihome_adapter_blueiris.adapter import BlueIrisCamera
from kukiihome_shared.adapter import PreprocessingMode

pytestmark = pytest.mark.asyncio


def test_identity() -> None:
    adapter = BlueIrisAdapter(BlueIrisConfig())
    assert adapter.name == "adapter-blueiris"
    assert adapter.mode == PreprocessingMode.SERVICE


async def test_list_cameras_from_config() -> None:
    config = BlueIrisConfig(
        cameras=[
            BlueIrisCamera(
                camera_id="cam1",
                rtsp_url="rtsp://bi/cam1",
                ha_motion_entity="binary_sensor.cam1_motion",
                name="Front",
                ptz=True,
            ),
        ]
    )
    adapter = BlueIrisAdapter(config)
    cams = await adapter.list_cameras()
    assert len(cams) == 1
    assert cams[0].camera_id == "cam1"
    assert cams[0].ptz is True
    assert cams[0].has_on_camera_ai is True


async def test_get_stream_url() -> None:
    adapter = BlueIrisAdapter(
        BlueIrisConfig(cameras=[BlueIrisCamera(camera_id="c1", rtsp_url="rtsp://x")])
    )
    assert await adapter.get_stream_url("c1") == "rtsp://x"


async def test_unknown_camera_raises() -> None:
    adapter = BlueIrisAdapter(BlueIrisConfig())
    with pytest.raises(KeyError):
        await adapter.get_frame_window("nonexistent", datetime.now(UTC), datetime.now(UTC))
