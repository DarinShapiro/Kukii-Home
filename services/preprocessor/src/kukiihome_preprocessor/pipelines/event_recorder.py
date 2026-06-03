"""Autonomous motion-event recorder + durable sink.

The rolling buffer is ephemeral (a ~5-minute ring); analysis used to be
purely query-driven, so a motion event that wasn't pulled in time simply
aged out and was lost. This component closes that gap.

Design (per the maintainer's spec):

* **Motion is the trigger, not the boundary.** When the upstream MOG2 gate
  flags motion at ``t``, we open an event covering ``[t - PRE_ROLL,
  t + POST_ROLL]``. The pre-roll captures the approach; the post-roll keeps
  the event open *after* motion stops — so a person who triggers the event
  and then **stands still** is still inside the window and gets analyzed.
* **New motion extends the window.** Each fresh motion frame pushes the
  close time to ``last_motion + POST_ROLL`` (capped at MAX_DURATION).
* **On close, enrich the FULL window** (``enrich_motion_only=False`` — every
  frame, not just moving ones) and **persist to disk**: the JPEG frames plus
  an ``event.json`` carrying detections + identity (as probability records).

This is a deliberately thin durable sink, NOT the episodic/session memory
layer (which needs the VLM reasoning to curate meaningfully — parked). The
on-disk shape is forward-compatible: a future memory service can ingest
these event records.

The recorder polls the rolling buffer for ``has_motion`` rather than hooking
the capture loop, so it stays fully decoupled — capture doesn't know it
exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kukiihome_preprocessor.pipelines.rolling_buffer import RollingBuffer
    from kukiihome_preprocessor.pipelines.rtsp_frame_buffer import RTSPFrameBuffer
    from kukiihome_preprocessor.state import ActorCache

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "event.v1"


@dataclass
class EventRecorderConfig:
    pre_roll_s: float = 10.0
    post_roll_s: float = 30.0
    max_duration_s: float = 180.0
    """Hard cap so a long-running scene (sprinklers, flag in wind) can't grow
    an event past what the rolling buffer can still serve at close time."""
    poll_interval_s: float = 1.0
    store_dir: Path = field(default_factory=lambda: Path("events"))


@dataclass
class _CamState:
    last_poll_ts: float = 0.0
    recording: bool = False
    trigger_ts: float = 0.0
    window_start: float = 0.0
    last_motion_ts: float = 0.0


def _to_jsonable(obj: Any) -> Any:
    """Serialize a pydantic model / dataclass / sequence into plain JSON."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "_asdict"):
        return obj._asdict()
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return str(obj)


class EventRecorder:
    """Per-camera motion-event state machine + durable persistence.

    Wire one instance for the whole service; call :meth:`run` as a background
    task. It polls the rolling buffer for each configured camera.
    """

    def __init__(
        self,
        *,
        rolling_buffer: RollingBuffer,
        frame_buffer: RTSPFrameBuffer,
        cache: ActorCache,
        cameras: list[str],
        node_id: str = "default",
        config: EventRecorderConfig | None = None,
    ) -> None:
        self._rolling = rolling_buffer
        self._frame_buffer = frame_buffer
        self._cache = cache
        self._cameras = cameras
        self._node_id = node_id
        self._cfg = config or EventRecorderConfig()
        self._state: dict[str, _CamState] = {c: _CamState() for c in cameras}
        self._events_written = 0
        self._pending: set[asyncio.Task] = set()
        self._cfg.store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def events_written(self) -> int:
        return self._events_written

    async def drain(self) -> None:
        """Await all in-flight close/enrich tasks. For shutdown + tests."""
        if self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """Poll loop. Runs until ``stop`` is set (or forever)."""
        logger.info(
            "event_recorder.start cameras=%s pre=%.0f post=%.0f store=%s",
            self._cameras, self._cfg.pre_roll_s, self._cfg.post_roll_s, self._cfg.store_dir,
        )
        while stop is None or not stop.is_set():
            try:
                for cam in self._cameras:
                    await self._tick(cam, now=time.time())
            except Exception:  # never let the loop die on one bad tick
                logger.exception("event_recorder.tick_error")
            with contextlib.suppress(asyncio.TimeoutError):
                if stop is not None:
                    await asyncio.wait_for(stop.wait(), timeout=self._cfg.poll_interval_s)
                else:
                    await asyncio.sleep(self._cfg.poll_interval_s)
        await self.drain()  # let in-flight closes finish persisting on shutdown

    async def _tick(self, cam: str, *, now: float) -> None:
        st = self._state[cam]
        if st.last_poll_ts == 0.0:
            st.last_poll_ts = now - self._cfg.poll_interval_s

        new = await self._rolling.get_window(cam, ts_start=st.last_poll_ts, ts_end=now)
        st.last_poll_ts = now
        motion = [f for f in new if f.has_motion]

        if motion:
            if not st.recording:
                st.recording = True
                st.trigger_ts = motion[0].ts
                st.window_start = st.trigger_ts - self._cfg.pre_roll_s
                logger.info("event_recorder.open cam=%s trigger=%.3f", cam, st.trigger_ts)
            st.last_motion_ts = max(st.last_motion_ts, motion[-1].ts)

        if st.recording:
            quiet_for = now - st.last_motion_ts
            duration = now - st.trigger_ts
            if quiet_for >= self._cfg.post_roll_s or duration >= self._cfg.max_duration_s:
                window_end = st.last_motion_ts + self._cfg.post_roll_s
                capped = duration >= self._cfg.max_duration_s
                # Spawn the close OFF the poll loop: persist + enrich can take
                # minutes on CPU, and the loop must stay free to detect the
                # next event. State resets immediately so a new motion event
                # can open right away.
                task = asyncio.create_task(
                    self._close_event(cam, st.window_start, window_end, st.trigger_ts, capped)
                )
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)
                self._state[cam] = _CamState(last_poll_ts=now)

    async def _close_event(
        self, cam: str, window_start: float, window_end: float, trigger_ts: float, capped: bool
    ) -> None:
        # STEP 1 — capture frame bytes FIRST and persist durably, before the
        # slow enrich. On CPU, enriching the full window can take minutes; if
        # we pulled bytes after that, the rolling buffer could evict them mid-
        # enrich (the exact data-loss we already hit). Frames safe first.
        buffered = await self._rolling.get_window(cam, ts_start=window_start, ts_end=window_end)
        if not buffered:
            logger.warning(
                "event_recorder.empty cam=%s window=[%.1f,%.1f] (rolled out before close?)",
                cam, window_start, window_end,
            )
            return

        event_id = f"{cam}_{int(trigger_ts * 1000)}"
        event_dir = self._cfg.store_dir / cam / event_id
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "camera_id": cam,
            "node_id": self._node_id,
            "trigger_ts": trigger_ts,
            "window_start": window_start,
            "window_end": window_end,
            "pre_roll_s": self._cfg.pre_roll_s,
            "post_roll_s": self._cfg.post_roll_s,
            "duration_capped": capped,
            "frame_count": len(buffered),
            "motion_frame_count": sum(1 for f in buffered if f.has_motion),
            "enriched": False,
            "detections": [],
            "identified_entities": [],
            "created_at": time.time(),
        }
        await asyncio.to_thread(self._write_event, event_dir, buffered, manifest)
        self._events_written += 1
        logger.info(
            "event_recorder.persisted cam=%s id=%s frames=%d motion=%d -> %s",
            cam, event_id, len(buffered), manifest["motion_frame_count"], event_dir,
        )

        # STEP 2 — enrich the FULL window (every frame, incl. the stationary
        # subject) and merge detections into the manifest. Best-effort: the
        # frames are already durable, so an enrich failure/slowness never
        # loses data.
        try:
            fw = await self._frame_buffer.get_window(
                camera_id=cam, ts_start=window_start, ts_end=window_end,
                enrich=True, cache=self._cache, enrich_motion_only=False,
            )
            det = getattr(fw, "detections", ()) or ()
            ents = getattr(fw, "identified_entities", ()) or ()
            await asyncio.to_thread(
                self._merge_enrichment, event_dir, _to_jsonable(det), _to_jsonable(ents)
            )
            kinds = sorted({getattr(d, "kind", "?") for d in det})
            logger.info(
                "event_recorder.enriched cam=%s id=%s detections=%d kinds=%s",
                cam, event_id, len(det), kinds,
            )
        except Exception:
            logger.exception("event_recorder.enrich_failed cam=%s id=%s (frames safe)", cam, event_id)

    @staticmethod
    def _write_event(event_dir: Path, buffered: Any, manifest: dict) -> None:
        event_dir.mkdir(parents=True, exist_ok=True)
        ts_to_name: dict[float, str] = {}
        for i, f in enumerate(buffered):
            name = f"frame_{i:05d}.jpg"
            (event_dir / name).write_bytes(f.jpeg_bytes)
            ts_to_name[f.ts] = name
        manifest["frame_index"] = [
            {"name": ts_to_name[f.ts], "ts": f.ts, "has_motion": f.has_motion} for f in buffered
        ]
        (event_dir / "event.json").write_text(json.dumps(manifest, indent=2))

    @staticmethod
    def _merge_enrichment(event_dir: Path, detections: Any, identified_entities: Any) -> None:
        path = event_dir / "event.json"
        manifest = json.loads(path.read_text())
        manifest["detections"] = detections
        manifest["identified_entities"] = identified_entities
        manifest["enriched"] = True
        path.write_text(json.dumps(manifest, indent=2))
