"""Motion + on-camera AI corroboration logic.

When a camera supplies on-camera AI events (Dahua, Reolink, etc.) we fuse
those signals with our own motion detection per §08:

- Both agree → high confidence
- Only on-camera AI fires → "trust but verify" (still process, flag)
- Only our motion fires → "investigate" (process at normal tier)
- Neither fires → no event (no processing)

We OR the signals at this stage; the triage worker handles priority.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CorroboratedSignal:
    """Fused result of motion detection + on-camera AI."""

    should_process: bool
    confidence: float
    sources: tuple[str, ...]
    """Tuple of contributing sources, e.g. ``("motion", "onvif_ai")``."""


def corroborate(
    *,
    own_motion: bool,
    own_confidence: float = 0.0,
    on_camera_label: str | None = None,
    on_camera_confidence: float = 0.0,
) -> CorroboratedSignal:
    """Fuse our motion signal with an on-camera AI opinion.

    Args:
        own_motion: Did our motion detector flag this frame?
        own_confidence: Our heuristic motion confidence (0-1).
        on_camera_label: Camera-side label (e.g. "person") or None.
        on_camera_confidence: Camera-side confidence (0-1).

    Returns:
        A ``CorroboratedSignal`` describing the fused decision.
    """
    sources: list[str] = []
    if own_motion:
        sources.append("motion")
    if on_camera_label:
        sources.append(f"on_camera:{on_camera_label}")

    should_process = own_motion or bool(on_camera_label)

    # Fusion confidence: max of the inputs, boosted slightly when both agree.
    base = max(own_confidence, on_camera_confidence)
    both_agree = own_motion and bool(on_camera_label)
    if both_agree:
        base = min(1.0, base + 0.10)

    return CorroboratedSignal(
        should_process=should_process,
        confidence=base,
        sources=tuple(sources),
    )
