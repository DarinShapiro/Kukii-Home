"""NVR adapter contract — see §03.5.

Every adapter (`adapters/nvr-*`) implements `NVRAdapter` from this module.
Adapters subclass it, declare their capabilities, and translate between a
platform-specific API and the unified contract that Kukii-Home's core consumes.

Usage::

    from kukiihome_shared.adapter import NVRAdapter

    class MyNVRAdapter(NVRAdapter):
        async def list_cameras(self) -> list[NVRCapability]:
            ...

        async def get_frame_window(...) -> FrameWindow:
            ...

        async def subscribe_motion_events(...) -> AsyncIterator[MotionEvent]:
            ...
"""

from kukiihome_shared.adapter.base import (
    AdapterError,
    NVRAdapter,
    PreprocessingMode,
    UnsupportedCapability,
)

__all__ = [
    "AdapterError",
    "NVRAdapter",
    "PreprocessingMode",
    "UnsupportedCapability",
]
