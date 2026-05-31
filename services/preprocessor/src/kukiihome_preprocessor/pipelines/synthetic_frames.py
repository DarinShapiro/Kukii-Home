"""Synthetic frame buffer that backs ``GET /frame_window`` in dev.

The production preprocessor will:

1. Continuously ingest RTSP from each configured camera into a
   rolling in-memory buffer (~5 minutes).
2. Continuously run motion gating on incoming frames.
3. On ``/frame_window`` calls, return the buffered frames in the
   asked-for interval, optionally running detection + recognition
   pipelines over them.

For Phase 10.1 skeleton, none of (1) - (3) is implemented for real.
Instead, :class:`SyntheticFrameBuffer` fabricates plausible-looking
frames + detection metadata when asked, so the HA-side can exercise
the full pull path end-to-end without GPU / RTSP / models.

The synthesis is deterministic on ``(camera_id, ts_start, ts_end)``
so tests can assert exact shapes. The frame density is tunable
(``frames_per_second``) and the tag distribution mirrors the rough
mix we'd expect from real traffic (most frames empty, some person,
some dog).
"""

from __future__ import annotations

import hashlib
import math
import random
import time

import structlog
from kukiihome_shared.preprocessor import (
    ActorMatch,
    DetectionTag,
    FrameRef,
    FrameWindow,
)

from kukiihome_preprocessor.state import ActorCache

logger = structlog.get_logger(__name__)


class SyntheticFrameBuffer:
    """Fake frame buffer + on-demand enrichment.

    Stateless: every ``get_window`` call re-synthesizes from
    ``(camera_id, ts_start, ts_end)``. That's deterministic AND
    matches the "rolling buffer + on-demand enrichment" production
    semantics — the caller can't tell the difference for a window
    that fits inside the configured buffer horizon.
    """

    def __init__(
        self,
        *,
        configured_cameras: list[str],
        node_id: str,
        frames_per_second: float = 2.0,
        buffer_horizon_seconds: float = 300.0,
    ) -> None:
        self._cameras = set(configured_cameras)
        self._node_id = node_id
        self._frames_per_second = frames_per_second
        self._horizon = buffer_horizon_seconds

    # ─── public API the FastAPI route calls ─────────────────────────

    async def serve_frame(
        self,
        camera_id: str,  # noqa: ARG002 — Protocol arg unused in synthetic mode
        ts: float,  # noqa: ARG002 — Protocol arg unused in synthetic mode
    ) -> bytes | None:
        """Synthetic backend doesn't retain frame bytes — the URIs it
        emits are placeholders. Always returns ``None``; the route
        responds 404. Real frame fetches happen against
        :class:`RTSPFrameBuffer`."""
        return None

    async def serve_annotated_frame(
        self,
        camera_id: str,  # noqa: ARG002 — Protocol arg unused in synthetic mode
        ts: float,  # noqa: ARG002 — Protocol arg unused in synthetic mode
    ) -> bytes | None:
        """Synthetic backend doesn't run the annotation pipeline.
        Always returns ``None``; the /annotated.jpg route responds 404."""
        return None

    async def get_window(
        self,
        *,
        camera_id: str,
        ts_start: float,
        ts_end: float,
        enrich: bool,
        cache: ActorCache,
    ) -> FrameWindow:
        """Build a FrameWindow for the requested interval.

        Outside the configured camera list → empty window.
        Outside the buffer horizon → empty window (mirrors prod where
        the buffer has aged out).
        ts_end <= ts_start → empty window.
        """
        t0 = time.perf_counter()

        if (
            camera_id not in self._cameras
            or ts_end <= ts_start
            or _too_old(ts_start, self._horizon)
        ):
            return FrameWindow(
                camera_id=camera_id,
                ts_start=ts_start,
                ts_end=ts_end,
                preprocessor_node_id=self._node_id,
                enrichment_mode="enriched" if enrich else "frames_only",
                enrichment_latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        frame_ts_list = _frame_timestamps(
            ts_start=ts_start,
            ts_end=ts_end,
            fps=self._frames_per_second,
        )
        frames = tuple(
            FrameRef(
                ts=ts,
                uri=f"synthetic://{camera_id}/{ts:.3f}.jpg",
                width=1920,
                height=1080,
                quality_score=_deterministic_quality(camera_id, ts) if enrich else None,
            )
            for ts in frame_ts_list
        )

        detections: tuple[DetectionTag, ...] = ()
        actor_matches: tuple[ActorMatch, ...] = ()

        if enrich:
            detections, actor_matches = await self._enrich(
                camera_id=camera_id,
                frame_ts_list=frame_ts_list,
                cache=cache,
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
            enrichment_mode="enriched" if enrich else "frames_only",
            enrichment_latency_ms=latency_ms,
        )

    # ─── internals ────────────────────────────────────────────────

    async def _enrich(
        self,
        *,
        camera_id: str,
        frame_ts_list: list[float],
        cache: ActorCache,
    ) -> tuple[tuple[DetectionTag, ...], tuple[ActorMatch, ...]]:
        """Per-frame synthetic enrichment.

        Each frame gets 0, 1 or 2 detections drawn from a fixed
        distribution. Person detections have a 40% chance of carrying
        an ActorMatch IF the ActorCache has any KnownActors enrolled
        (otherwise they're "unknown person" detections — matching
        real-world cold-start behavior).
        """
        detections: list[DetectionTag] = []
        actor_matches: list[ActorMatch] = []

        cached_actors = await cache.snapshot()
        for frame_ts in frame_ts_list:
            rng = _deterministic_rng(camera_id, frame_ts)
            roll = rng.random()
            # 50% empty, 35% person, 12% dog, 3% person+dog.
            kinds: list[str] = []
            if roll < 0.50:
                kinds = []
            elif roll < 0.85:
                kinds = ["person"]
            elif roll < 0.97:
                kinds = ["dog"]
            else:
                kinds = ["person", "dog"]

            for i, kind in enumerate(kinds):
                track_id = f"trk-{_stable_hash(camera_id, frame_ts, i):04x}"
                detections.append(
                    DetectionTag(
                        kind=kind,
                        confidence=round(0.7 + rng.random() * 0.25, 3),
                        bbox=(
                            round(rng.random() * 0.4, 3),
                            round(rng.random() * 0.4, 3),
                            round(0.6 + rng.random() * 0.4, 3),
                            round(0.6 + rng.random() * 0.4, 3),
                        ),
                        frame_ts=frame_ts,
                        track_id=track_id,
                    )
                )

                if kind == "person" and cached_actors and rng.random() < 0.40:
                    actor = cached_actors[rng.randint(0, len(cached_actors) - 1)]
                    actor_matches.append(
                        ActorMatch(
                            actor_id=actor.actor_id,
                            confidence=round(0.75 + rng.random() * 0.2, 3),
                            match_method="face_arcface",
                            frame_ts=frame_ts,
                            track_id=track_id,
                        )
                    )

        return tuple(detections), tuple(actor_matches)


# ─── module-level helpers ────────────────────────────────────────────


def _frame_timestamps(*, ts_start: float, ts_end: float, fps: float) -> list[float]:
    """Evenly-spaced timestamps within ``[ts_start, ts_end]`` at
    ``fps`` frames per second. Inclusive of start; exclusive of end."""
    if fps <= 0 or ts_end <= ts_start:
        return []
    step = 1.0 / fps
    n = math.floor((ts_end - ts_start) / step)
    return [round(ts_start + i * step, 3) for i in range(n)]


def _too_old(ts_start: float, horizon_seconds: float) -> bool:
    """Mirrors a production rolling-buffer horizon: requests for
    windows older than ``horizon_seconds`` ago return empty."""
    return (time.time() - ts_start) > horizon_seconds


def _stable_hash(*parts: object) -> int:
    """Deterministic small int from any tuple of components."""
    h = hashlib.sha256()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
    return int(h.hexdigest()[:8], 16)


def _deterministic_rng(*parts: object) -> random.Random:
    """A random.Random seeded deterministically on the input parts.

    Two calls with the same parts produce the same sequence —
    critical so tests can assert exact returned values."""
    # S311 OK: synthetic test data, not security-sensitive.
    return random.Random(_stable_hash(*parts))  # noqa: S311


def _deterministic_quality(camera_id: str, ts: float) -> float:
    """A plausible quality score in [0.6, 0.95] that varies per
    (camera, ts) but never changes between calls."""
    rng = _deterministic_rng("quality", camera_id, ts)
    return round(0.6 + rng.random() * 0.35, 3)
