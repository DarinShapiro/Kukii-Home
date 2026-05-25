"""sentihome-adapter-blueiris — Blue Iris (Windows NVR) adapter.

Service mode. Consumes events via the ha-blueiris HACS integration and pulls
RTSP from Blue Iris's built-in RTSP server.
"""

from __future__ import annotations

__version__ = "0.1.0"

from sentihome_adapter_blueiris.adapter import BlueIrisAdapter, BlueIrisConfig

__all__ = ["BlueIrisAdapter", "BlueIrisConfig", "__version__"]
