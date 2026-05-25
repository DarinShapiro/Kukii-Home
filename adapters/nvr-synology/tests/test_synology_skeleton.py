"""Skeleton tests confirming the adapter conforms to the NVRAdapter contract."""

from __future__ import annotations

import pytest
from sentihome_adapter_synology import SynologyAdapter, SynologyConfig
from sentihome_shared.adapter import NVRAdapter, PreprocessingMode
from sentihome_shared.adapter.base import AdapterError


def test_synology_adapter_is_nvr_adapter() -> None:
    adapter = SynologyAdapter(SynologyConfig())
    assert isinstance(adapter, NVRAdapter)
    assert adapter.name == "adapter-synology"
    assert adapter.mode == PreprocessingMode.SERVICE


@pytest.mark.asyncio
async def test_synology_list_cameras_raises_until_implemented() -> None:
    adapter = SynologyAdapter(SynologyConfig())
    with pytest.raises(AdapterError, match="skeleton"):
        await adapter.list_cameras()
