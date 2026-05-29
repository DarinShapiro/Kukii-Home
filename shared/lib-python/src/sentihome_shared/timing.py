"""StepTimings — a tiny, reusable per-operation span timer.

The one timing primitive used across SentiHome so every component
reports *where* its wall-clock went in a consistent shape: a
``{step_name: milliseconds}`` dict that gets surfaced on the
operation's result model (``FrameWindow.step_timings_ms``, an alert's
``timings``, a VLM response, an HA-action result) and emitted as a
structured log line.

Design goals:

* **Zero deps, cheap.** ``time.perf_counter`` deltas, nothing else.
* **Works around ``await``.** ``span()`` is a sync context manager,
  but its body may contain awaits — the timer closes when the block
  completes, so ``with t.span("detect"): x = await detect()`` times
  the whole await. (No need for an async CM.)
* **Accumulates by name.** Repeated spans with the same name sum, so
  per-frame work measured in a loop (e.g. ``"face"`` across N frames)
  rolls up to a single total.

Usage::

    t = StepTimings()
    with t.span("buffer_read"):
        frames = await buffer.get_window(...)
    with t.span("detect"):
        dets = await detector.detect_batch(...)
    model = FrameWindow(..., step_timings_ms=t.as_dict())
    logger.info("frame_window.timings", camera_id=cam, **t.as_dict())

Not a tracing system — no span hierarchy, no propagation across
process boundaries. Each component owns one StepTimings per
operation. If full distributed tracing is ever needed, this is the
seam to swap (callers only touch ``span``/``record``/``as_dict``).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager


class StepTimings:
    """Collects named span durations (milliseconds) for one operation."""

    __slots__ = ("_spans",)

    def __init__(self) -> None:
        self._spans: dict[str, float] = {}

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        """Time the wrapped block, adding its duration (ms) to ``name``.

        The duration is recorded even if the block raises — so a step
        that errors still shows the time it consumed before failing.
        """
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._spans[name] = round(self._spans.get(name, 0.0) + elapsed_ms, 3)

    def record(self, name: str, ms: float) -> None:
        """Record a duration measured elsewhere (e.g. a value returned
        by a downstream service, or a manual perf_counter delta)."""
        self._spans[name] = round(self._spans.get(name, 0.0) + ms, 3)

    def as_dict(self) -> dict[str, float]:
        """The collected ``{step: ms}`` map (a copy)."""
        return dict(self._spans)

    def total_ms(self) -> float:
        """Sum of all recorded spans. Note: overlapping/nested spans
        double-count, so this is a rough sum, not a wall-clock total —
        measure the whole-operation wall-clock separately if needed."""
        return round(sum(self._spans.values()), 3)

    def __bool__(self) -> bool:
        return bool(self._spans)
