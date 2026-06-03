"""``/identity/*`` REST surface — the backend the operator Review UI calls.

Registered onto the preprocessor's FastAPI app (which already owns the frames +
the detections.db). Exposes the always-embed → persist → resolve loop as HTTP:
list the un-named tracks the cameras stored, show a crop of each, label one (→
template → retroactive resolve), and browse the resulting people/pets.

Thin: every endpoint is a few lines over :class:`IdentityStore` +
:class:`DetectionStore`. Registered only when the app is wired with those
stores (i.e. a real detections.db is configured); absent in the synthetic CI
backend, which has no persisted observations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

if TYPE_CHECKING:
    from fastapi import FastAPI

    from kukiihome_preprocessor.app import AppState
    from kukiihome_preprocessor.identity_store import TrackSummary

logger = structlog.get_logger(__name__)


class LabelRequest(BaseModel):
    event_id: str
    track_id: str
    name: str
    kind: str | None = None          # person | pet; derived from the track if omitted
    species: str | None = None       # pet only
    owner_id: str | None = None      # pet only
    modalities: list[str] | None = None  # restrict which to enroll; default all on the track


class ResolveRequest(BaseModel):
    event_id: str | None = None      # None = re-resolve every embedded event


class RejectRequest(BaseModel):
    event_id: str
    track_id: str


class MergeRequest(BaseModel):
    from_id: str
    into_id: str


def _summary_dict(s: TrackSummary) -> dict:
    return {
        "event_id": s.event_id,
        "camera_id": s.camera_id,
        "track_id": s.track_id,
        "kind": s.kind,
        "n_frames": s.n_frames,
        "t0": s.t0,
        "t1": s.t1,
        "modalities": s.modalities,
        "status": s.status,
        "subject_id": s.subject_id,
        "subject_name": s.subject_name,
        "confidence": s.confidence,
        "verdict": s.verdict,
        "thumb_url": f"identity/tracks/{s.event_id}/{s.track_id}/thumb.jpg",
    }


def register_identity_routes(app: FastAPI, state: AppState) -> None:
    """Attach the ``/identity/*`` routes. No-op caller responsibility: only
    call when ``state.identity_store`` + ``state.detection_store`` are set."""
    identity = state.identity_store
    detections = state.detection_store
    assert identity is not None and detections is not None

    @app.get("/identity/tracks")
    async def list_tracks(
        status: str | None = Query(default=None),
        kind: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> JSONResponse:
        tracks = identity.track_summaries(status=status, kind=kind, limit=limit)
        return JSONResponse({"tracks": [_summary_dict(t) for t in tracks]})

    @app.get("/identity/tracks/{event_id}/{track_id}/thumb.jpg")
    async def track_thumb(event_id: str, track_id: str) -> Response:
        src = identity.crop_source(event_id, track_id)
        if src is None:
            raise HTTPException(status_code=404, detail="no crop for track")
        data = _crop_jpeg(state.event_store_dir or "events", event_id, src)
        if data is None:
            raise HTTPException(status_code=404, detail="frame not on disk")
        return Response(content=data, media_type="image/jpeg")

    @app.get("/identity/subjects")
    async def list_subjects() -> JSONResponse:
        subs = identity.list_subjects()
        return JSONResponse({"subjects": [
            {
                "subject_id": s.subject_id, "kind": s.kind, "display_name": s.display_name,
                "species": s.species, "owner_id": s.owner_id,
                "modalities": s.modalities, "appearances": s.appearances,
            }
            for s in subs
        ]})

    @app.post("/identity/label")
    async def label_track(req: LabelRequest) -> JSONResponse:
        """Label a stored track → (re)build the subject's template(s) from its
        embeddings → retroactively resolve every embedded event. The one
        action the Review UI performs."""
        kind = req.kind
        if kind is None:
            src = identity.crop_source(req.event_id, req.track_id)
            kind = src["kind"] if src else "person"
        subject_id = identity.upsert_subject(
            display_name=req.name, kind=kind, species=req.species, owner_id=req.owner_id,
        )
        enrolled = identity.enroll_from_track(
            detections, subject_id=subject_id, event_id=req.event_id,
            track_id=req.track_id, modalities=req.modalities,
        )
        if not enrolled:
            raise HTTPException(status_code=400, detail="track has no embeddings to enroll")
        # An explicit label overrides any prior verdict on THIS track (e.g. a
        # reject from fixing a false-merge) so it re-resolves to the new subject.
        identity.clear_track_resolutions(req.event_id, req.track_id)
        matched = identity.resolve_all(detections)
        await _refresh_live_cache(state, identity, subject_id)
        logger.info(
            "identity.labelled", subject_id=subject_id, modalities=enrolled, matched=matched,
        )
        return JSONResponse({
            "subject_id": subject_id, "enrolled_modalities": enrolled, "matched": matched,
        })

    @app.post("/identity/resolve")
    async def resolve(req: ResolveRequest) -> JSONResponse:
        if req.event_id:
            matched = identity.resolve_persist(detections, event_id=req.event_id)
        else:
            matched = identity.resolve_all(detections)
        return JSONResponse({"matched": matched})

    @app.post("/identity/reject")
    async def reject(req: RejectRequest) -> JSONResponse:
        """Split-to-unknown: drop a wrong resolution → track returns to the
        queue (the fix for an over-merged track)."""
        n = identity.reject_track(req.event_id, req.track_id)
        return JSONResponse({"rejected": n})

    @app.post("/identity/subjects/merge")
    async def merge(req: MergeRequest) -> JSONResponse:
        """Merge two labels that are the same subject."""
        try:
            ok = identity.merge_subjects(req.from_id, req.into_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not ok:
            raise HTTPException(status_code=404, detail="unknown subject or self-merge")
        matched = identity.resolve_all(detections)
        # keep the live cache consistent: drop the merged-away actor, refresh the survivor.
        cache = getattr(state, "cache", None)
        if cache is not None:
            await cache.remove(req.from_id)
        await _refresh_live_cache(state, identity, req.into_id)
        return JSONResponse({"ok": True, "matched": matched})


async def _refresh_live_cache(state: AppState, identity, subject_id: str) -> None:
    """Fold a subject's current templates into the in-process recognition
    cache so the live ``/frame_window`` path matches it immediately. No-op if
    the app has no cache (shouldn't happen in the wired service)."""
    cache = getattr(state, "cache", None)
    if cache is None:
        return
    event = identity.build_enrollment_event(subject_id)
    if event is not None:
        await cache.upsert(event)


def _crop_jpeg(event_store_dir: str, event_id: str, src: dict) -> bytes | None:
    """Load ``<store>/<camera>/<event_id>/<frame_name>``, crop to the track's
    normalized bbox, JPEG-encode. ``None`` if the frame is missing/unreadable."""
    from pathlib import Path

    import cv2

    p = Path(event_store_dir) / src["camera_id"] / event_id / src["frame_name"]
    if not p.is_file():
        return None
    img = cv2.imread(str(p))
    if img is None:
        return None
    bbox = src.get("bbox")
    if bbox:
        h, w = img.shape[:2]
        x1 = max(0, int(bbox[0] * w))
        y1 = max(0, int(bbox[1] * h))
        x2 = min(w, int(bbox[2] * w))
        y2 = min(h, int(bbox[3] * h))
        if x2 > x1 and y2 > y1:
            img = img[y1:y2, x1:x2]
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes() if ok else None
