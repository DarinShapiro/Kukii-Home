"""Clip writer — mux persisted JPEG frames into a playable MP4 (stop-gap).

Task 1 / Path 3: legacy events on disk have ``frame_NNNNN.jpg`` + a
``frame_index`` in the manifest. Until the event recorder writes
``clip.mp4`` at event-close (Design A) or a stream-tap produces native
H.264 segments (Design B), we mux on demand and cache the result next to
the frames.

The result is a browser-playable H.264 MP4, audio-less, with frame timing
derived from the actual frame timestamps so motion plays at the rate it
was captured (not a uniform fake fps). PyAV handles the encode through
libx264; the function is synchronous and `asyncio.to_thread`-friendly.

Output is cached at ``event_dir/clip.mp4``; subsequent calls return the
cached path without re-encoding. Concurrent callers racing on the same
event are de-duped via an in-process lock keyed on event_id.
"""

from __future__ import annotations

import asyncio
import fractions
import json
import logging
from collections import defaultdict
from pathlib import Path

import av
import numpy as np

logger = logging.getLogger(__name__)

# Per-event encoder locks. Two simultaneous /clip.mp4 fetches for the same
# event share one mux pass; later fetches block briefly then read the
# cached file. Module-global so it works across handler tasks.
_MUX_LOCKS: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# Default encoder configuration. H.264 is the universal browser codec;
# yuv420p is the only pixel format Safari accepts on MP4.
DEFAULT_CODEC = "libx264"
DEFAULT_PIX_FMT = "yuv420p"
DEFAULT_CRF = "23"  # libx264 default (visually lossless ~18, distinguishable >28)
DEFAULT_PRESET = "veryfast"  # encoder speed/quality tradeoff; veryfast is fine for stop-gap


# ─── Public sync mux function ───────────────────────────────────────


def mux_jpegs_to_mp4(
    *,
    event_dir: Path,
    output: Path | None = None,
    target_fps: float = 4.0,
) -> Path:
    """Mux all frame_*.jpg files in ``event_dir`` into a playable MP4.

    Returns the output path. Caches: if ``output`` already exists,
    returns it without re-encoding (callers must delete to force rebuild).

    ``target_fps`` sets the encoder's container fps. Frame durations are
    computed from manifest timestamps when available so the playback rate
    matches real-world timing — target_fps is only the metadata hint.

    Raises FileNotFoundError when no manifest / no frames.
    """
    output = output or (event_dir / "clip.mp4")
    if output.exists() and output.stat().st_size > 0:
        return output

    manifest_path = event_dir / "event.json"
    frames = _resolve_frame_paths(event_dir, manifest_path)
    if not frames:
        raise FileNotFoundError(
            f"no frames to mux in {event_dir} (manifest missing or empty)"
        )

    # We need image dimensions to set up the encoder. Pull from frame 0.
    with av.open(str(frames[0][0]), mode="r") as probe:
        v0 = probe.streams.video[0]
        width = int(v0.width)
        height = int(v0.height)

    # Output container. Use a high time-base resolution (1/90000) so we can
    # encode arbitrary frame timing precisely (browser convention for video).
    output.parent.mkdir(parents=True, exist_ok=True)
    out_container = av.open(str(output), mode="w")
    try:
        # PyAV 13: rate must be Fraction (or int), not float — bare float
        # raises AttributeError in av.utils.to_avrational.
        stream = out_container.add_stream(
            DEFAULT_CODEC,
            rate=fractions.Fraction(round(target_fps * 1000), 1000),
        )
        stream.width = width
        stream.height = height
        stream.pix_fmt = DEFAULT_PIX_FMT
        stream.codec_context.time_base = fractions.Fraction(1, 90000)
        stream.codec_context.options = {
            "crf": DEFAULT_CRF,
            "preset": DEFAULT_PRESET,
        }

        first_ts = frames[0][1]
        for jpg_path, ts in frames:
            pts = round((ts - first_ts) * 90000)
            with av.open(str(jpg_path), mode="r") as img_container:
                img_frame = next(img_container.decode(video=0))
            # Convert to yuv420p (libx264's input format) at the output
            # dimensions. If a frame has a different size from frame 0
            # (rare; would be a config change mid-event) we resize.
            new_frame = img_frame.reformat(
                width=width, height=height, format=DEFAULT_PIX_FMT,
            )
            new_frame.pts = pts
            new_frame.time_base = stream.codec_context.time_base
            for packet in stream.encode(new_frame):
                out_container.mux(packet)
        # Flush encoder.
        for packet in stream.encode(None):
            out_container.mux(packet)
    finally:
        out_container.close()
    logger.info(
        "clip_writer.muxed event_dir=%s frames=%d output=%s size=%d",
        event_dir, len(frames), output, output.stat().st_size,
    )
    return output


# ─── Async wrapper with per-event de-duplication ────────────────────


async def get_or_build_clip(
    *, event_dir: Path, target_fps: float = 4.0,
) -> Path:
    """Async-friendly version of :func:`mux_jpegs_to_mp4` with per-event
    coalescing. Two concurrent calls for the same event_id share one
    encode; the second await just sees the cached file."""
    event_id = event_dir.name or str(event_dir)
    lock = _MUX_LOCKS[event_id]
    async with lock:
        return await asyncio.to_thread(
            mux_jpegs_to_mp4, event_dir=event_dir, target_fps=target_fps,
        )


# ─── Helpers ────────────────────────────────────────────────────────


def _resolve_frame_paths(
    event_dir: Path, manifest_path: Path,
) -> list[tuple[Path, float]]:
    """Return [(jpg_path, ts), ...] in chronological order.

    Reads ``event.json`` for ``frame_index`` (ts-stamped entries). Falls
    back to globbing ``frame_*.jpg`` and assigning a synthetic timestamp
    sequence at 4 fps when no manifest is present — keeps callers robust
    to older event dirs that pre-date the manifest format."""
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            idx = manifest.get("frame_index", [])
            out: list[tuple[Path, float]] = []
            for entry in idx:
                name = entry.get("name")
                ts = entry.get("ts")
                if name and ts is not None:
                    p = event_dir / name
                    if p.exists():
                        out.append((p, float(ts)))
            if out:
                out.sort(key=lambda x: x[1])
                return out
        except (json.JSONDecodeError, KeyError):
            pass
    # Fallback: glob and fake timestamps.
    paths = sorted(event_dir.glob("frame_*.jpg"))
    return [(p, float(i) / 4.0) for i, p in enumerate(paths)]


def frame_count_in_event(event_dir: Path) -> int:
    """How many JPEG frames are on disk for this event. Used by the
    /clip.mp4 endpoint to short-circuit with a clearer error when an event
    has none (vs. PyAV raising deep in the encoder)."""
    return sum(1 for _ in event_dir.glob("frame_*.jpg"))


def clip_dimensions(event_dir: Path) -> tuple[int, int] | None:
    """(width, height) of the cached clip, or None if missing."""
    clip = event_dir / "clip.mp4"
    if not clip.exists():
        return None
    try:
        with av.open(str(clip), mode="r") as c:
            v = c.streams.video[0]
            return int(v.width), int(v.height)
    except av.AVError:
        return None


# numpy not actually used in the mux path today; kept imported in case
# the next iteration wants per-frame numpy operations (overlays, etc.).
_ = np
