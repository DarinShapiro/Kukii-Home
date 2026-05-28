"""Detection / identification pipelines for the recognition preprocessor.

Phase 10.1 skeleton (synthetic): :class:`SyntheticFrameBuffer` —
stateless deterministic stub that backs ``/frame_window`` for CI +
unit tests. No RTSP, no models.

Phase 10.1.5 (RTSP-NVR mode): :class:`RTSPFrameBuffer` + the
:class:`RollingBuffer` + per-camera :class:`CameraCaptureTask`.
Continuous H.264-sub-stream ingestion, JPEG-encoded keyframes held
in a rolling time-window buffer, fetched on demand by callers.

Phase 10.3+: real detection + recognition layered on top of the
RTSP buffer (YOLO11x, ArcFace, DINOv2, fastALPR).

The :class:`FrameBufferBackend` Protocol below is the contract every
backend must satisfy. The FastAPI app holds a backend behind this
type and is agnostic to which one is wired in.
"""

from __future__ import annotations

from typing import Protocol

from sentihome_shared.preprocessor import FrameWindow

from sentihome_preprocessor.state import ActorCache


class FrameBufferBackend(Protocol):
    """Contract every frame-buffer backend satisfies.

    ``get_window`` is the read path serving ``GET /frame_window``.
    ``serve_frame`` is the read path serving
    ``GET /frames/{camera_id}/{ts}.jpg`` — returns the raw JPEG bytes
    for a single previously-buffered frame, or ``None`` if the
    backend doesn't actually retain bytes (synthetic mode).
    """

    async def get_window(
        self,
        *,
        camera_id: str,
        ts_start: float,
        ts_end: float,
        enrich: bool,
        cache: ActorCache,
    ) -> FrameWindow: ...

    async def serve_frame(self, camera_id: str, ts: float) -> bytes | None: ...

    async def serve_annotated_frame(
        self, camera_id: str, ts: float
    ) -> bytes | None: ...
