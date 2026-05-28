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
from collections import defaultdict
from typing import TYPE_CHECKING

import cv2
import numpy as np
from sentihome_shared.preprocessor import (
    ActorMatch,
    DetectionTag,
    FrameRef,
    FrameWindow,
    IdentifiedEntity,
)

from sentihome_preprocessor.pipelines.markup import annotate_frame, encode_jpeg
from sentihome_preprocessor.pipelines.rolling_buffer import (
    AnnotationCache,
    RollingBuffer,
)
from sentihome_preprocessor.state import ActorCache

if TYPE_CHECKING:
    from sentihome_preprocessor.pipelines.detection import YOLODetector
    from sentihome_preprocessor.pipelines.identity import IdentityRouter


# Min identity confidence below which we don't emit IdentifiedEntity.
# Matches the markup module's threshold so an entity that makes it
# into identified_entities is one the markup pipeline will draw.
_IDENTITY_MIN_CONFIDENCE = 0.6

# Classes the markup contract supports. Detections of other kinds
# (animal in general, etc.) can carry actor matches in principle
# but aren't wired into a kind-typed IdentifiedEntity yet.
_MARKUPABLE_KINDS: frozenset[str] = frozenset({"person", "dog", "cat", "vehicle"})


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
        node_id: str,
        external_base_url: str,
        detector: YOLODetector | None = None,
        identity_router: IdentityRouter | None = None,
        enrich_motion_only: bool = True,
        annotation_cache: AnnotationCache | None = None,
        configured_cameras: list[str] | None = None,  # deprecated, ignored
    ) -> None:
        self._buffer = rolling_buffer
        self._node_id = node_id
        # configured_cameras used to be a static whitelist enforced
        # here. That broke dynamic camera discovery — a camera added
        # via CameraConfigEvent would capture frames but then be
        # rejected at query time. Now: any camera that has entries
        # in the rolling buffer is queryable. The static cameras
        # list is kept as a no-op kwarg for backwards-compat in
        # callers that still pass it (will be cleaned up in #71's
        # ha-agent publisher work where camera config is fully
        # dynamic).
        _ = configured_cameras  # silence unused-arg lint
        # rstrip so /frames doesn't double-slash if caller passes
        # a base ending in /.
        self._base_url = external_base_url.rstrip("/")
        self._detector = detector
        """Optional YOLO detector. When provided AND ``enrich=True``,
        get_window batches every buffered frame in the window through
        it and populates ``FrameWindow.detections``. When None
        (skeleton / unit tests / Phase 10.1.5 era), detections stay
        empty — the wire shape is the same."""
        self._identity_router = identity_router
        """Optional :class:`IdentityRouter` carrying the registered
        identity pipelines (face today; body-ID/pet/plate as they
        land). When provided AND ``enrich=True``, get_window calls
        ``identity_router.identify(...)`` once per request and
        populates ``FrameWindow.actor_matches`` from the merged
        per-pipeline results. ``None`` (default / unit tests /
        synthetic backend): no identity inference; actor_matches
        stays empty."""
        self._enrich_motion_only = enrich_motion_only
        """When True (default for RTSP backend), only frames marked
        by the upstream MOG2 motion detector (``BufferedFrame.has_motion``)
        are sent to YOLO. Empty/quiet frames return frame references
        but no detections — saves ~85% of inference work in steady
        state. Set False for forensic / replay use where every frame
        in the window should be analyzed regardless of motion."""
        self._annotation_cache = annotation_cache
        """Optional cache for marked-up JPEG bytes. When provided AND
        a frame has at least one IdentifiedEntity above the markup
        threshold, get_window writes the annotated bytes here and
        sets ``FrameRef.annotated_uri`` so callers can fetch the
        annotated version via ``/frames/{cam}/{ts}/annotated.jpg``.
        ``None`` (default for unit tests): no annotation rendering."""

    async def serve_annotated_frame(self, camera_id: str, ts: float) -> bytes | None:
        """Read a previously-rendered annotated JPEG out of the
        annotation cache. Returns the bytes for the
        ``GET /frames/{camera_id}/{ts}/annotated.jpg`` route.
        ``None`` when no annotation cache is wired, or when this
        frame had no IdentifiedEntities above the markup threshold
        (the common case until face/pet/plate pipelines land)."""
        if self._annotation_cache is None:
            return None
        return await self._annotation_cache.get(camera_id, ts)

    async def serve_frame(self, camera_id: str, ts: float) -> bytes | None:
        """Read a single JPEG-encoded keyframe out of the rolling
        buffer. Returns the bytes for the
        ``GET /frames/{camera_id}/{ts}.jpg`` route. ``None`` if the
        exact-ts frame isn't buffered (camera unknown OR aged out)."""
        frame = await self._buffer.get_at(camera_id, ts)
        return frame.jpeg_bytes if frame is not None else None

    async def get_window(
        self,
        *,
        camera_id: str,
        ts_start: float,
        ts_end: float,
        enrich: bool,
        cache: ActorCache,
    ) -> FrameWindow:
        """Pull buffered keyframes in ``[ts_start, ts_end]``.

        When enrichment is on, this also:
        1. Runs YOLO on the motion-flagged frames (or all frames
           if ``enrich_motion_only=False``) to produce DetectionTags.
        2. (Phase 10.4+) Runs face/pet/plate pipelines on the
           relevant detections to produce ActorMatches.
        3. Correlates detections + actor_matches by track_id and
           resolves friendly names from the actor cache to produce
           IdentifiedEntity records.
        4. (When an annotation_cache is wired) Renders annotated
           JPEGs for any frame with at least one IdentifiedEntity
           above the markup threshold, and sets FrameRef.annotated_uri
           so callers can fetch them.
        """
        t0 = time.perf_counter()

        if ts_end <= ts_start:
            return FrameWindow(
                camera_id=camera_id,
                ts_start=ts_start,
                ts_end=ts_end,
                preprocessor_node_id=self._node_id,
                enrichment_mode="enriched" if enrich else "frames_only",
                enrichment_latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        # No camera whitelist check: any camera_id with entries in
        # the rolling buffer is queryable. Unknown cameras simply
        # produce empty buffer reads -> empty FrameWindow, no error.

        buffered = await self._buffer.get_window(camera_id, ts_start=ts_start, ts_end=ts_end)

        detections: tuple[DetectionTag, ...] = ()
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

        # Phase 10.4 — identity pipelines (face today; body-ID / pet
        # / plate as they land). The router gates per-pipeline by
        # detection-kind triggers + corpus enrollments, dispatches
        # in parallel across disjoint branches, and merges the
        # ActorMatches into one tuple. See
        # :class:`IdentityRouter` for the dispatch design.
        actor_matches: tuple[ActorMatch, ...] = ()
        if enrich and self._identity_router is not None and detections:
            actor_matches = await self._identity_router.identify(
                buffered=buffered,
                detections=detections,
                cache=cache,
            )

        # Build identified_entities by correlating detections +
        # actor_matches via track_id and resolving names from cache.
        identified_entities = await _correlate_identities(detections, actor_matches, cache)

        # Render annotated JPEGs for any frames that have identities
        # — write into the annotation cache so the /annotated.jpg
        # endpoint can serve them, AND populate FrameRef.annotated_uri
        # so the caller knows the annotated version exists.
        annotated_ts: set[float] = set()
        if self._annotation_cache is not None and identified_entities:
            entities_by_ts: dict[float, list[IdentifiedEntity]] = defaultdict(list)
            for ent in identified_entities:
                entities_by_ts[ent.frame_ts].append(ent)

            jpeg_by_ts = {f.ts: f.jpeg_bytes for f in buffered}
            for frame_ts, ents in entities_by_ts.items():
                raw = jpeg_by_ts.get(frame_ts)
                if raw is None:
                    continue
                rendered = await _render_annotated_jpeg(raw, tuple(ents))
                if rendered is not None:
                    await self._annotation_cache.put(camera_id, frame_ts, rendered)
                    annotated_ts.add(frame_ts)

        # Build FrameRefs last so annotated_uri reflects what
        # actually got rendered.
        frames = tuple(
            FrameRef(
                ts=f.ts,
                uri=f"{self._base_url}/frames/{camera_id}/{f.ts:.3f}.jpg",
                annotated_uri=(
                    f"{self._base_url}/frames/{camera_id}/{f.ts:.3f}/annotated.jpg"
                    if f.ts in annotated_ts
                    else None
                ),
                width=f.width,
                height=f.height,
                # Quality assessment goes here in Phase 10.3 (sharpness +
                # exposure check). For now leave None — the
                # contract permits it.
                quality_score=None,
            )
            for f in buffered
        )

        latency_ms = int((time.perf_counter() - t0) * 1000)
        return FrameWindow(
            camera_id=camera_id,
            ts_start=ts_start,
            ts_end=ts_end,
            preprocessor_node_id=self._node_id,
            frames=frames,
            detections=detections,
            actor_matches=actor_matches,
            identified_entities=identified_entities,
            enrichment_mode="enriched" if enrich else "frames_only",
            enrichment_latency_ms=latency_ms,
        )


# ─── module helpers ─────────────────────────────────────────────────


async def _correlate_identities(
    detections: tuple[DetectionTag, ...],
    actor_matches: tuple[ActorMatch, ...],
    cache: ActorCache,
) -> tuple[IdentifiedEntity, ...]:
    """Join detections + actor_matches by ``track_id`` (the common
    handle the YOLO tracker and the identity pipelines both set) and
    resolve ``actor_id`` to a friendly name via the actor cache.

    Match requires both:
    * Non-None ``track_id`` on both sides (untracked detections can't
      be correlated with identity claims; we drop them rather than
      guessing).
    * Detection kind in ``_MARKUPABLE_KINDS`` — animal-other /
      package / etc. don't have identity pipelines yet.
    * Identity confidence >= ``_IDENTITY_MIN_CONFIDENCE`` — below
      this we don't trust the identity enough to put it in front
      of the VLM as a labeled fact.

    Frames with no satisfying correlation produce zero entities.
    That's the correct quiet behavior — the VLM still receives the
    raw frame.
    """
    if not detections or not actor_matches:
        return ()

    # Index actor_matches by (track_id, frame_ts) — track_id alone
    # could collide across frames in long windows.
    matches_by_key: dict[tuple[str, float], ActorMatch] = {}
    for m in actor_matches:
        if m.track_id is None or m.confidence < _IDENTITY_MIN_CONFIDENCE:
            continue
        matches_by_key[(m.track_id, m.frame_ts)] = m

    out: list[IdentifiedEntity] = []
    for det in detections:
        if det.track_id is None or det.kind not in _MARKUPABLE_KINDS:
            continue
        match = matches_by_key.get((det.track_id, det.frame_ts))
        if match is None:
            continue
        actor = await cache.get(match.actor_id)
        if actor is None or actor.name is None:
            # Identity pipeline matched an actor the cache doesn't
            # know — possible during a race between deactivation and
            # in-flight inference. Skip rather than emit nameless
            # markup.
            continue
        out.append(
            IdentifiedEntity(
                frame_ts=det.frame_ts,
                kind=det.kind,  # type: ignore[arg-type]  # narrowed by _MARKUPABLE_KINDS
                actor_id=match.actor_id,
                actor_name=actor.name,
                bbox=det.bbox,
                detection_confidence=det.confidence,
                identity_confidence=match.confidence,
                identity_method=match.match_method,
                track_id=det.track_id,
            )
        )
    return tuple(out)


async def _render_annotated_jpeg(
    raw_jpeg: bytes, entities: tuple[IdentifiedEntity, ...]
) -> bytes | None:
    """Decode a JPEG, apply markup, re-encode. Returns ``None`` when
    the input is undecodable or every entity is below the markup
    threshold (no boxes would actually be drawn — avoids caching a
    bytewise-equal copy of the input)."""
    if not entities:
        return None
    arr = np.frombuffer(raw_jpeg, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    annotated, stats = annotate_frame(img, entities)
    if stats.entities_annotated == 0:
        # Nothing actually drawn — don't cache a duplicate of raw.
        return None
    return encode_jpeg(annotated)
