"""kukiihome-adapter-frigate — Frigate NVR adapter (built-in mode).

Frigate already runs motion + YOLO; Kukii-Home augments with VLM reasoning.
"""

from __future__ import annotations

__version__ = "0.1.0"

from kukiihome_adapter_frigate.adapter import FrigateAdapter, FrigateConfig

__all__ = ["FrigateAdapter", "FrigateConfig", "__version__"]
