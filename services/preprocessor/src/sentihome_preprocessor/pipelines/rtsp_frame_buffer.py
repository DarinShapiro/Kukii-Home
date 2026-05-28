"""Frame buffer backed by the RTSP rolling buffer.

Same ``get_window`` interface as :class:`SyntheticFrameBuffer`, so the
FastAPI ``/frame_window`` route is unchanged regardless of which
backend is wired in.

Phase 10.1.5: detections + actor matches are NOT yet computed from
real frames — the enrichment fields are returned empty. Wiring
YOLO11x / ArcFace / DINOv2 lands in Phase 10.3 / 10.4 / 10.5.

Each ``FrameRef.uri`` returned points at the preprocessor's own
``GET /frames/{camera_id}/{ts}.jpg`` endpoint, so callers fetch the
JPEG bytes on demand instead of inlining them into the
``FrameWindow`` response.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sentihome_shared.preprocessor import FrameRef, FrameWindow

from sentihome_preprocessor.pipelines.rolling_buffer import RollingBuffer
from sentihome_preprocessor.state import ActorCache

if TYPE_CHECKING:
    from sentihome_preprocessor.pipelines.detection import YOLODetector


class RTSPFrameBuffer:
    """Reads from a :class:`RollingBuffer` filled by RTSP capture tasks.

    Honors the same contract as :class:`SyntheticFrameBuffer` so
    :func:`app.create_app` can hold either behind a Protocol-shaped
    attribute without caring which one is running.
    """

    def __init__(
        self,
        *,
        rolling_buffer: RollingBuffer,
        configured_cameras: list[str],
        node_id: str,
        external_base_url: str,
        detector: YOLODetector | None = None,
        enrich_motion_only: bool = True,
    ) -> None:
        self._buffer = rolling_buffer
        self._cameras = set(configured_cameras)
        self._node_id = node_id
        # rstrip so /frames doesn't double-slash if caller passes
        # a base ending in /.
        self._base_url = external_base_url.rstrip("/")
        self._detector = detector
        """Optional YOLO detector. When provided AND ``enrich=True``,
        get_window batches every buffered frame in the window through
        it and populates ``FrameWindow.detections``. When None
        (skeleton / unit tests / Phase 10.1.5 era), detections stay
        empty — the wire shape is the same."""
        self._enrich_motion_only = enrich_motion_only
        """When True (default for RTSP backend), only frames marked
        by the upstream MOG2 motion detector (``BufferedFrame.has_motion``)
        are sent to YOLO. Empty/quiet frames return frame references
        but no detections — saves ~85% of inference work in steady
        state. Set False for forensic / replay use where every frame
        in the window should be analyzed regardless of motion."""

    async def serve_frame(self, camera_id: str, ts: float) -> bytes | None:
        """Read a single JPEG-encoded keyframe out of the rolling
        buffer. Returns the bytes for the
        ``GET /frames/{camera_id}/{ts}.jpg`` route. ``None`` if the
        camera is unknown or the exact-ts frame has already aged out."""
        if camera_id not in self._cameras:
            return None
        frame = await self._buffer.get_at(camera_id, ts)
        return frame.jpeg_bytes if frame is not None else None

    async def get_window(
        self,
        *,
        camera_id: str,
        ts_start: float,
        ts_end: float,
        enrich: bool,
        cache: ActorCache,  # noqa: ARG002 — used in Phase 10.3+ enrichment
    ) -> FrameWindow:
        """Pull buffered keyframes in ``[ts_start, ts_end]``."""
        t0 = time.perf_counter()

        if camera_id not in self._cameras or ts_end <= ts_start:
            return FrameWindow(
                camera_id=camera_id,
                ts_start=ts_start,
                ts_end=ts_end,
                preprocessor_node_id=self._node_id,
                enrichment_mode="enriched" if enrich else "frames_only",
                enrichment_latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        buffered = await self._buffer.get_window(
            camera_id, ts_start=ts_start, ts_end=ts_end
        )

        frames = tuple(
            FrameRef(
                ts=f.ts,
                uri=f"{self._base_url}/frames/{camera_id}/{f.ts:.3f}.jpg",
                width=f.width,
                height=f.height,
                # Quality assessment goes here in Phase 10.3 (sharpness +
                # exposure check). For now leave None — the
                # contract permits it.
                quality_score=None,
            )
            for f in buffered
        )

        detections: tuple = ()
        if enrich and self._detector is not None and buffered:
            # Pick which frames actually go through YOLO. With
            # enrich_motion_only=True (default), skip frames the MOG2
            # detector flagged as quiet — typically ~85% of frames in
            # a residential setup. The frames still appear in the
            # response's FrameRef list; they just don't carry
            # detections. Callers needing forensic / replay analysis
            # override via enrich_motion_only=False at construction.
            candidates = (
                [f for f in buffered if f.has_motion]
                if self._enrich_motion_only
                else list(buffered)
            )
            if candidates:
                detections = await self._detector.detect_batch(
                    [(f.jpeg_bytes, f.ts) for f in candidates]
                )

        # Actor matches (face / pet / plate) land in Phase 10.4+ —
        # they branch on DetectionTag.kind to dispatch to vendor
        # pipelines. Until then this stays empty even when
        # detections is populated.
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return FrameWindow(
            camera_id=camera_id,
            ts_start=ts_start,
            ts_end=ts_end,
            preprocessor_node_id=self._node_id,
            frames=frames,
            detections=detections,
            actor_matches=(),
            enrichment_mode="enriched" if enrich else "frames_only",
            enrichment_latency_ms=latency_ms,
        )
