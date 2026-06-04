"""Keyframe selection — decouple tracking-fps from VLM-fps.

Tracking + embedding want *dense* frames (continuity: every frame, so detections
are continuous and association just works — see the tracking finding, design
§7.6). The VLM wants *sparse* keyframes (cost + context window). So capture +
persist dense, but downsample to a handful of evenly-spaced keyframes at the
**VLM hop** (the ``frame_window`` RPC). The event recorder pulls frames straight
from the buffer, so it stays dense — only the VLM-facing endpoint thins.

Pure + tiny so it's trivially testable and lives independent of the FrameRef
type (duck-typed on ``.ts``).
"""

from __future__ import annotations


def select_keyframes[T](frames: tuple[T, ...] | list[T], max_frames: int) -> tuple[T, ...]:
    """Return ``<= max_frames`` frames, evenly spaced across time and always
    including the first and last (so the VLM sees the full arc — approach
    through departure — not a clipped middle).

    ``max_frames <= 0`` or a window already at/under the cap returns everything.
    Items are assumed to carry a ``.ts`` (sorted ascending on output)."""
    items = sorted(frames, key=lambda f: f.ts)
    n = len(items)
    if max_frames <= 0 or n <= max_frames:
        return tuple(items)
    if max_frames == 1:
        return (items[n // 2],)
    # Evenly-spaced indices spanning [0, n-1] inclusive of both endpoints.
    idxs = sorted({round(i * (n - 1) / (max_frames - 1)) for i in range(max_frames)})
    return tuple(items[i] for i in idxs)
