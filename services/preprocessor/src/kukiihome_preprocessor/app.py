"""FastAPI app exposing the preprocessor's REST surface.

Endpoints:

* ``GET /healthz``         — fast liveness probe
* ``GET /status``          — :class:`PreprocessorStatus` snapshot
* ``GET /frame_window``    — pull frames + (optional) enrichment for
                             ``[ts_start, ts_end]`` on ``camera_id``;
                             returns :class:`FrameWindow`. This is
                             the primary RPC.
* ``POST /tune``           — apply a :class:`KnobAdjustment`
* ``POST /actors/enroll``  — fall-back enrollment (canonical path is
                             NATS broadcast from memory service)

The app is wire-only: pipelines, frame buffer, and NATS subscriber
are owned by ``__main__.py``. The app reads them via :class:`AppState`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from kukiihome_shared.preprocessor import (
    ActorEnrollmentEvent,
    FrameWindow,
    KnobAdjustment,
    PreprocessorStatus,
)

from kukiihome_preprocessor.config import PreprocessorConfig
from kukiihome_preprocessor.pipelines import FrameBufferBackend
from kukiihome_preprocessor.state import ActorCache

logger = structlog.get_logger(__name__)


@dataclass
class AppState:
    """Process-singleton state that the FastAPI routes read.

    Built by ``__main__.py`` after wiring the cache + frame buffer,
    then passed into :func:`create_app`. Tests construct their own
    AppState directly and bypass the lifecycle.
    """

    config: PreprocessorConfig
    cache: ActorCache
    frame_buffer: FrameBufferBackend
    """Either a :class:`SyntheticFrameBuffer` (CI / unit tests) or
    :class:`RTSPFrameBuffer` (real RTSP-NVR mode). Both satisfy the
    :class:`FrameBufferBackend` Protocol."""

    started_ts: float

    # Cumulative counter — incremented on every /frame_window served.
    # Exposed via /status for traffic / sizing visibility.
    frame_windows_served_total: int = 0

    # Knob registry: latest applied value per (knob_id[@scope]).
    # Pure storage for Phase 10.1; real handlers in Phase 10.2+.
    applied_knobs: dict[str, KnobAdjustment] | None = None

    def __post_init__(self) -> None:
        if self.applied_knobs is None:
            self.applied_knobs = {}


def create_app(state: AppState) -> FastAPI:
    """Build the FastAPI app, attaching the shared state. Factory
    rather than module-singleton so tests can spin isolated apps."""
    app = FastAPI(
        title="Kukii-Home Preprocessor",
        description=(
            "Recognition preprocessor service. Lives on the inference "
            "box; HA-side services pull frames + enrichment from this "
            "via GET /frame_window."
        ),
        version="0.1.0",
    )
    app.state.app_state = state

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status", response_model=PreprocessorStatus)
    async def status() -> PreprocessorStatus:
        cameras_total = len(state.config.cameras)
        # Skeleton: every configured camera is "active". Phase 10.2
        # will track per-camera RTSP connection state.
        cameras_active = cameras_total

        return PreprocessorStatus(
            healthy=True,
            uptime_seconds=time.time() - state.started_ts,
            model_versions={"synthetic_frame_buffer": "v1-skeleton"},
            cameras_active=cameras_active,
            cameras_total=cameras_total,
            frame_windows_served_total=state.frame_windows_served_total,
            actors_cached=await state.cache.size(),
        )

    @app.get("/frames/{camera_id}/{ts}.jpg")
    async def get_frame(camera_id: str, ts: float) -> Response:
        """Serve one JPEG keyframe that was previously buffered.

        The URI for a buffered frame is emitted as
        ``FrameRef.uri`` in a ``FrameWindow`` response — clients
        fetch frames on demand instead of getting them inlined.

        Synthetic-backend deployments respond 404 here: their
        FrameRef URIs are placeholders that don't resolve.
        """
        data = await state.frame_buffer.serve_frame(camera_id, ts)
        if data is None:
            raise HTTPException(status_code=404, detail="frame not available")
        return Response(content=data, media_type="image/jpeg")

    @app.get("/frames/{camera_id}/{ts}/annotated.jpg")
    async def get_annotated_frame(camera_id: str, ts: float) -> Response:
        """Serve the markup-annotated version of a frame.

        Produced during /frame_window enrichment when the frame
        contained at least one identified entity above the markup
        threshold. The annotation only draws boxes around RECOGNIZED
        entities (face / pet / plate match >= 0.6) — anonymous
        detections never get annotated, since labeled "unknown"
        boxes pollute VLM grounding without adding signal.

        Returns 404 when no annotation exists for this frame —
        either because the frame was wholly anonymous, was older
        than the annotation cache horizon, or the backend doesn't
        run annotation at all (synthetic mode).

        The URI for a frame that DOES have an annotation is emitted
        as :attr:`FrameRef.annotated_uri` in the ``FrameWindow``
        response. Callers should check that field first rather than
        blindly probing this endpoint.
        """
        data = await state.frame_buffer.serve_annotated_frame(camera_id, ts)
        if data is None:
            raise HTTPException(status_code=404, detail="annotated frame not available")
        return Response(content=data, media_type="image/jpeg")

    @app.get("/frame_window", response_model=FrameWindow)
    async def frame_window(
        camera_id: Annotated[str, Query(min_length=1)],
        ts_start: Annotated[float, Query()],
        ts_end: Annotated[float, Query()],
        enrich: Annotated[bool, Query()] = True,
    ) -> FrameWindow:
        """Primary RPC: pull buffered frames + (optional) detection
        + identity enrichment for one camera over ``[ts_start, ts_end]``.

        The preprocessor doesn't know about the calling
        TriggerEvent — only the camera + time interval. Callers add
        event context on the HA side when mapping the returned
        FrameWindow into an EnrichedEvent.

        Out-of-buffer windows return an empty FrameWindow rather
        than an error: that's expected when the caller asks for
        something older than the rolling-buffer horizon, and
        clients have to handle empty windows anyway (camera was
        offline, no frames captured, etc.).
        """
        result = await state.frame_buffer.get_window(
            camera_id=camera_id,
            ts_start=ts_start,
            ts_end=ts_end,
            enrich=enrich,
            cache=state.cache,
        )
        state.frame_windows_served_total += 1
        return result

    @app.post("/tune")
    async def tune(adjustment: KnobAdjustment) -> dict[str, str]:
        key = adjustment.knob_id
        if adjustment.scope_camera_id is not None:
            key = f"{adjustment.knob_id}@{adjustment.scope_camera_id}"
        assert state.applied_knobs is not None
        state.applied_knobs[key] = adjustment
        logger.info(
            "preprocessor.knob.applied",
            knob_id=adjustment.knob_id,
            scope=adjustment.scope_camera_id,
            new_value=adjustment.new_value,
        )
        return {"status": "applied", "knob_id": adjustment.knob_id}

    @app.post("/actors/enroll")
    async def enroll_actor(event: ActorEnrollmentEvent) -> dict[str, str]:
        # Fall-back path. Canonical is the NATS subject — but
        # production may need REST backfill during bootstrap.
        if event.action == "deactivated":
            removed = await state.cache.remove(event.actor_id)
            return {
                "status": "deactivated" if removed else "noop",
                "actor_id": event.actor_id,
            }
        if event.action not in ("enrolled", "updated"):
            raise HTTPException(
                status_code=400,
                detail=f"unknown action {event.action!r}",
            )
        await state.cache.upsert(event)
        return {"status": "cached", "actor_id": event.actor_id}

    return app
