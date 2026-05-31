"""Regression guard: bus duration fields are passed to nats-py in
SECONDS, not nanoseconds.

nats-py's StreamConfig/ConsumerConfig express durations in seconds and
convert to nanoseconds themselves when serializing. ensure_stream /
ensure_consumer once pre-multiplied by 1e9, which double-converted —
e.g. a 60s max_age became 6e19 ns and overflowed the server's int64
time.Duration (JetStream BadRequestError 10025). These tests lock the
contract without needing a live NATS.
"""

from __future__ import annotations

from kukiihome_shared.bus import Bus


class _CapturingJS:
    """Minimal JetStream stand-in that records the config it's handed."""

    def __init__(self) -> None:
        self.stream_config = None
        self.consumer_config = None

    async def add_stream(self, *, config):
        self.stream_config = config

    async def add_consumer(self, *, stream, config):
        self.consumer_config = config


async def test_ensure_stream_passes_durations_in_seconds():
    js = _CapturingJS()
    bus = Bus(nc=None, js=js)
    await bus.ensure_stream(
        name="s",
        subjects=["x.>"],
        max_age_seconds=60,
        duplicate_window_seconds=2,
    )
    # Seconds, NOT 60 * 1e9 — nats-py converts to ns itself.
    assert js.stream_config.max_age == 60
    assert js.stream_config.duplicate_window == 2


async def test_ensure_consumer_passes_ack_wait_in_seconds():
    js = _CapturingJS()
    bus = Bus(nc=None, js=js)
    await bus.ensure_consumer(stream="s", consumer="c", ack_wait_seconds=30)
    assert js.consumer_config.ack_wait == 30
