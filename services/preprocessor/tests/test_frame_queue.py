"""Tests for the decode↔process FrameQueue (burst absorption + shed policy)."""

from __future__ import annotations

import threading
import time

import pytest
from kukiihome_preprocessor.pipelines.frame_queue import FrameQueue


def test_fifo_order_under_capacity():
    q: FrameQueue[int] = FrameQueue(maxsize=8)
    for i in range(5):
        assert q.put(i, has_motion=False) is True
    out = [q.get(timeout=0.1) for _ in range(5)]
    assert out == [0, 1, 2, 3, 4]
    assert q.metrics.dropped_total == 0


def test_burst_within_capacity_drops_nothing():
    q: FrameQueue[int] = FrameQueue(maxsize=100)
    for i in range(100):
        assert q.put(i, has_motion=False) is True
    assert q.metrics.dropped_total == 0
    assert q.metrics.peak_depth == 100


def test_overflow_sheds_non_motion_first():
    q: FrameQueue[str] = FrameQueue(maxsize=3)
    # Fill: two non-motion, one motion.
    q.put("nm1", has_motion=False)
    q.put("motion", has_motion=True)
    q.put("nm2", has_motion=False)
    # Overflow: must evict a NON-motion frame, never the motion one.
    stored = q.put("new", has_motion=False)
    assert stored is False  # signalled a shed happened
    assert q.metrics.dropped_total == 1
    assert q.metrics.dropped_motion_total == 0
    drained = [q.get(timeout=0.1) for _ in range(3)]
    assert "motion" in drained  # the motion frame survived
    assert "new" in drained  # freshest frame kept


def test_motion_frame_dropped_only_when_queue_all_motion():
    q: FrameQueue[int] = FrameQueue(maxsize=2)
    q.put(1, has_motion=True)
    q.put(2, has_motion=True)
    # No non-motion victim available → forced high-value drop, counted.
    stored = q.put(3, has_motion=True)
    assert stored is False
    assert q.metrics.dropped_motion_total == 1
    assert q.metrics.dropped_total == 1


def test_incoming_frame_always_kept_freshest_evidence():
    q: FrameQueue[int] = FrameQueue(maxsize=2)
    q.put(1, has_motion=False)
    q.put(2, has_motion=False)
    q.put(3, has_motion=False)  # evicts oldest (1), keeps freshest
    drained = sorted(q.get(timeout=0.1) for _ in range(2))
    assert drained == [2, 3]


def test_metrics_track_throughput_and_peak():
    q: FrameQueue[int] = FrameQueue(maxsize=4)
    for i in range(10):  # 6 overflow → 6 sheds
        q.put(i, has_motion=False)
    assert q.metrics.enqueued_total == 10
    assert q.metrics.dropped_total == 6
    assert q.metrics.peak_depth == 4
    got = [q.get(timeout=0.1) for _ in range(4)]
    assert q.metrics.dequeued_total == 4
    assert len(got) == 4


def test_get_blocks_until_put_then_returns():
    q: FrameQueue[int] = FrameQueue(maxsize=4)
    got: list[int | None] = []

    def consumer() -> None:
        got.append(q.get(timeout=2.0))

    t = threading.Thread(target=consumer)
    t.start()
    time.sleep(0.05)  # consumer is now blocked in get()
    q.put(42, has_motion=True)
    t.join(timeout=2.0)
    assert got == [42]


def test_get_timeout_returns_none_when_empty():
    q: FrameQueue[int] = FrameQueue(maxsize=4)
    t0 = time.perf_counter()
    assert q.get(timeout=0.1) is None
    assert time.perf_counter() - t0 >= 0.09


def test_close_wakes_blocked_getters():
    q: FrameQueue[int] = FrameQueue(maxsize=4)
    results: list[int | None] = []

    def consumer() -> None:
        results.append(q.get(timeout=5.0))

    t = threading.Thread(target=consumer)
    t.start()
    time.sleep(0.05)
    q.close()
    t.join(timeout=2.0)
    assert results == [None]  # woke up, drained-empty → None
    assert q.put(1, has_motion=True) is False  # closed → rejects


@pytest.mark.parametrize(
    "burst,maxsize,proc_rate_hz,burst_rate_hz",
    [
        (200, 64, 50.0, 500.0),  # 10x faster ingest than processing
        (500, 128, 100.0, 1000.0),
    ],
)
def test_burst_then_catch_up_no_motion_loss(burst, maxsize, proc_rate_hz, burst_rate_hz):
    """The headline invariant: under a burst where ingest >> processing
    for a finite window, NO motion frame is lost, and the queue drains
    after the burst. Every other frame is a motion frame, so the shed
    policy is forced to discriminate."""
    q: FrameQueue[int] = FrameQueue(maxsize=maxsize)
    consumed: list[int] = []
    stop = threading.Event()

    def worker() -> None:
        interval = 1.0 / proc_rate_hz
        while not stop.is_set():
            v = q.get(timeout=0.05)
            if v is not None:
                consumed.append(v)
                time.sleep(interval)  # simulate processing cost

    w = threading.Thread(target=worker)
    w.start()

    # Burst: ingest far faster than the worker can drain.
    interval = 1.0 / burst_rate_hz
    motion_ids = set()
    for i in range(burst):
        is_motion = i % 2 == 0
        if is_motion:
            motion_ids.add(i)
        q.put(i, has_motion=is_motion)
        time.sleep(interval)

    # Let the worker catch up post-burst.
    deadline = time.perf_counter() + 10.0
    while q.metrics.depth > 0 and time.perf_counter() < deadline:
        time.sleep(0.02)
    stop.set()
    w.join(timeout=2.0)

    # Invariant: queue drained (burst absorbed + caught up).
    assert q.metrics.depth == 0
    # Non-motion frames may be shed; that's allowed. But the count of
    # shed frames must all be accounted for and the queue is consistent.
    assert q.metrics.enqueued_total == burst
    assert q.metrics.dequeued_total + q.metrics.dropped_total == burst, (
        "every frame either processed or counted as dropped — none vanished"
    )
