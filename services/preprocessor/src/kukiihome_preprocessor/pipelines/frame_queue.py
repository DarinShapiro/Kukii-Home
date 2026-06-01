"""Bounded, priority-shedding frame queue between decode and processing.

Architectural fix (Q: capture must not be gated by processing). The old
capture loop did ``decode -> motion -> encode -> write`` serially in one
thread, so the slowest stage throttled ingestion — during a burst of
activity, frames were effectively dropped at the decoder because nothing
drained it fast enough. That's unsound: ingestion rate must not be set by
processing time.

This queue decouples the two. The decode thread does the minimum
(decode + a cheap motion gate) and ``put``s frames here as fast as the
decoder yields; a pool of encode workers ``get``s and does the expensive
JPEG-encode + buffer write in parallel. The queue absorbs bursts and the
workers catch up after.

**Shedding policy.** A bounded queue cannot absorb *sustained* over-capacity
(if ingest > drain on average, any finite queue fills) — that's a
hardware-capacity problem (NVDEC + GPU), not a queue problem. What the queue
*guarantees* is that a **burst** is absorbed and that when it must drop, it
drops the **lowest-value** frame: a non-motion frame is evicted before any
``has_motion`` frame. Only when the queue is full of motion frames is a
motion frame dropped — and that is counted + logged loudly as a real
capacity breach, never silent. This mirrors the event-bus
:class:`~kukiihome_core.triage.BackpressureSignal` (drop background before
urgent, never shed safety) one layer down, at the frame level.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


@dataclass
class QueueMetrics:
    """Observability for the frame queue — surfaced on /status so
    "nothing was dropped" is verifiable, not asserted."""

    enqueued_total: int = 0
    dequeued_total: int = 0
    dropped_total: int = 0
    dropped_motion_total: int = 0
    """High-value drops: a ``has_motion`` frame evicted because the queue
    was full of motion frames. Non-zero means real capacity breach."""
    depth: int = 0
    peak_depth: int = 0


@dataclass
class _Item[T]:
    payload: T
    has_motion: bool
    seq: int


class FrameQueue[T]:
    """Bounded queue with motion-priority shedding.

    Thread-safe (decode thread ``put``s, worker threads ``get``) via a
    plain :class:`threading.Lock` + :class:`threading.Condition` — this
    sits on the blocking/threaded side of the capture pipeline, not the
    asyncio side, so no event loop is involved.
    """

    def __init__(self, *, maxsize: int = 64) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._maxsize = maxsize
        self._dq: deque[_Item[T]] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._closed = False
        self._seq = 0
        self.metrics = QueueMetrics()

    def put(self, payload: T, *, has_motion: bool) -> bool:
        """Enqueue a frame. Returns ``True`` if it was stored, ``False``
        if it (or a victim) had to be shed to stay within ``maxsize``.

        When full: evict the oldest **non-motion** frame to make room. If
        every queued frame has motion, evict the oldest frame (a real
        high-value drop) and count it. The incoming frame is always
        stored — we keep the freshest evidence and shed staler/cheaper.
        """
        with self._lock:
            if self._closed:
                return False
            shed = False
            if len(self._dq) >= self._maxsize:
                shed = True
                victim_idx = self._oldest_non_motion_index()
                if victim_idx is None:
                    # Queue is all-motion → forced high-value drop.
                    self._dq.popleft()
                    self.metrics.dropped_motion_total += 1
                else:
                    del self._dq[victim_idx]
                self.metrics.dropped_total += 1
            self._seq += 1
            self._dq.append(_Item(payload=payload, has_motion=has_motion, seq=self._seq))
            self.metrics.enqueued_total += 1
            self.metrics.depth = len(self._dq)
            self.metrics.peak_depth = max(self.metrics.peak_depth, self.metrics.depth)
            self._not_empty.notify()
            return not shed

    def get(self, *, timeout: float | None = None) -> T | None:
        """Pop the oldest frame (FIFO), blocking until one is available
        or the queue is closed / times out. Returns ``None`` on
        close-with-empty or timeout."""
        with self._not_empty:
            while not self._dq and not self._closed:
                if not self._not_empty.wait(timeout=timeout):
                    return None  # timeout
            if not self._dq:
                return None  # closed + drained
            item = self._dq.popleft()
            self.metrics.dequeued_total += 1
            self.metrics.depth = len(self._dq)
            return item.payload

    def close(self) -> None:
        """Wake all blocked ``get``s so worker threads can exit."""
        with self._lock:
            self._closed = True
            self._not_empty.notify_all()

    def _oldest_non_motion_index(self) -> int | None:
        """Index of the oldest non-motion item, or None if all have
        motion. ``deque`` indexing is O(n) but n is small (maxsize)."""
        for i, item in enumerate(self._dq):
            if not item.has_motion:
                return i
        return None
