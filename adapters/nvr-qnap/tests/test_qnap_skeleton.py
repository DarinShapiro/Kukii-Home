"""Skeleton tests confirming the adapter conforms to the NVRAdapter contract."""

from __future__ import annotations

import pytest
from kukiihome_adapter_qnap import QnapAdapter, QnapConfig
from kukiihome_shared.adapter import NVRAdapter, PreprocessingMode
from kukiihome_shared.adapter.base import AdapterError


def test_qnap_adapter_is_nvr_adapter() -> None:
    adapter = QnapAdapter(QnapConfig())
    assert isinstance(adapter, NVRAdapter)
    assert adapter.name == "adapter-qnap"
    assert adapter.mode == PreprocessingMode.SERVICE


@pytest.mark.asyncio
async def test_qnap_list_cameras_raises_until_implemented() -> None:
    adapter = QnapAdapter(QnapConfig())
    with pytest.raises(AdapterError, match="skeleton"):
        await adapter.list_cameras()
