"""Contract conformance tests for all NVR adapters.

Verifies every adapter:
1. Subclasses NVRAdapter
2. Has a stable name and PreprocessingMode
3. Can be instantiated with default config
4. ``list_cameras`` either returns a list[CameraCapability] or raises AdapterError
   (the skeleton-stub behavior for adapters whose full client is deferred)

Marked as integration tests but run without external dependencies — they
exercise the in-memory contract surface across all adapter packages.
"""

from __future__ import annotations

import pytest
from kukiihome_shared.adapter import NVRAdapter, PreprocessingMode
from kukiihome_shared.adapter.base import AdapterError, CameraCapability

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _all_adapter_classes() -> list[tuple[str, NVRAdapter]]:
    """Instantiate every adapter with default config."""
    from kukiihome_adapter_agent_dvr import AgentDVRAdapter, AgentDVRConfig
    from kukiihome_adapter_blueiris import BlueIrisAdapter, BlueIrisConfig
    from kukiihome_adapter_frigate import FrigateAdapter, FrigateConfig
    from kukiihome_adapter_qnap import QnapAdapter, QnapConfig
    from kukiihome_adapter_rtsp_direct import RTSPDirectAdapter
    from kukiihome_adapter_synology import SynologyAdapter, SynologyConfig
    from kukiihome_adapter_unifi import UnifiAdapter, UnifiConfig

    return [
        ("rtsp-direct", RTSPDirectAdapter(cameras=[])),
        ("agent-dvr", AgentDVRAdapter(AgentDVRConfig())),
        ("frigate", FrigateAdapter(FrigateConfig())),
        ("blueiris", BlueIrisAdapter(BlueIrisConfig())),
        ("synology", SynologyAdapter(SynologyConfig())),
        ("qnap", QnapAdapter(QnapConfig())),
        ("unifi", UnifiAdapter(UnifiConfig())),
    ]


def test_all_adapters_subclass_nvr_adapter() -> None:
    for _label, adapter in _all_adapter_classes():
        assert isinstance(adapter, NVRAdapter), f"{_label} not an NVRAdapter"


def test_all_adapters_have_stable_name() -> None:
    seen: set[str] = set()
    for _label, adapter in _all_adapter_classes():
        assert adapter.name.startswith("adapter-"), f"{adapter.name} invalid"
        assert adapter.name not in seen, f"duplicate name: {adapter.name}"
        seen.add(adapter.name)


def test_all_adapters_declare_preprocessing_mode() -> None:
    for _label, adapter in _all_adapter_classes():
        assert isinstance(adapter.mode, PreprocessingMode), f"{adapter.name} bad mode"


async def test_all_adapters_list_cameras_safely() -> None:
    """Every adapter either returns a list or raises AdapterError (skeleton case)."""
    for label, adapter in _all_adapter_classes():
        try:
            cams = await adapter.list_cameras()
        except AdapterError:
            continue  # skeleton stubs are OK
        except RuntimeError:
            continue  # adapters requiring start() also OK
        assert isinstance(cams, list), f"{label}: list_cameras did not return a list"
        for c in cams:
            assert isinstance(c, CameraCapability), f"{label}: bad CameraCapability"
