"""sentihome-adapter-synology — Synology NVR adapter (service mode, v1.x).

Skeleton implementation conforming to the NVRAdapter contract. Platform-specific
client implementation lands in v1.x as demand drives prioritization.
"""

from __future__ import annotations

__version__ = "0.1.0"

from sentihome_adapter_synology.adapter import SynologyAdapter, SynologyConfig

__all__ = ["SynologyAdapter", "SynologyConfig", "__version__"]
