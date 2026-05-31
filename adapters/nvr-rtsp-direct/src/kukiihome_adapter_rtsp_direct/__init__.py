"""kukiihome-adapter-rtsp-direct — direct RTSP from ONVIF cameras (no NVR).

The long-term recommended path (see §03.5). Kukii-Home's internal preprocessing
runs effectively in native mode: single decode, in-process.
"""

from __future__ import annotations

__version__ = "0.1.0"

from kukiihome_adapter_rtsp_direct.adapter import CameraConfig, RTSPDirectAdapter

__all__ = ["CameraConfig", "RTSPDirectAdapter", "__version__"]
