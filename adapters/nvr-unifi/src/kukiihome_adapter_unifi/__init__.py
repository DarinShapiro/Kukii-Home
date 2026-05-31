"""kukiihome-adapter-unifi — Unifi NVR adapter (service mode, v1.x).

Skeleton implementation conforming to the NVRAdapter contract. Platform-specific
client implementation lands in v1.x as demand drives prioritization.
"""

from __future__ import annotations

__version__ = "0.1.0"

from kukiihome_adapter_unifi.adapter import UnifiAdapter, UnifiConfig

__all__ = ["UnifiAdapter", "UnifiConfig", "__version__"]
