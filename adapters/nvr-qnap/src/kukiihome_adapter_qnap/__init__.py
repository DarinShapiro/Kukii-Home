"""kukiihome-adapter-qnap — Qnap NVR adapter (service mode, v1.x).

Skeleton implementation conforming to the NVRAdapter contract. Platform-specific
client implementation lands in v1.x as demand drives prioritization.
"""

from __future__ import annotations

__version__ = "0.1.0"

from kukiihome_adapter_qnap.adapter import QnapAdapter, QnapConfig

__all__ = ["QnapAdapter", "QnapConfig", "__version__"]
