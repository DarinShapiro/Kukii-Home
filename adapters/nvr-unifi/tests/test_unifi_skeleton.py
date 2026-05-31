"""Skeleton tests confirming the adapter conforms to the NVRAdapter contract."""

from __future__ import annotations

import pytest
from kukiihome_adapter_unifi import UnifiAdapter, UnifiConfig
from kukiihome_shared.adapter import NVRAdapter, PreprocessingMode
from kukiihome_shared.adapter.base import AdapterError


def test_unifi_adapter_is_nvr_adapter() -> None:
    adapter = UnifiAdapter(UnifiConfig())
    assert isinstance(adapter, NVRAdapter)
    assert adapter.name == "adapter-unifi"
    assert adapter.mode == PreprocessingMode.SERVICE


@pytest.mark.asyncio
async def test_unifi_list_cameras_raises_until_implemented() -> None:
    adapter = UnifiAdapter(UnifiConfig())
    with pytest.raises(AdapterError, match="skeleton"):
        await adapter.list_cameras()
