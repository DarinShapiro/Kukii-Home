"""Integration tests for kukiihome_shared.bus.Bus against a real NATS instance.

Runs in CI (workflow `integration.yml`) which starts a NATS service.
Locally: `./scripts/dev/up.sh nats && uv run pytest tests/integration -m integration`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from kukiihome_shared.bus import Bus
from kukiihome_shared.generated.events.trigger_event import (
    EventType,
    PrivacyTier,
    Source,
    TriggerEvent,
)

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _make_event(event_id: str | None = None) -> TriggerEvent:
    return TriggerEvent(
        event_id=event_id or f"evt_{uuid.uuid4().hex[:8]}",
        source=Source.adapter_rtsp_direct,
        timestamp=datetime.now(UTC),
        camera_id="test_cam",
        event_type=EventType.motion,
        privacy_tier=PrivacyTier.cloud_eligible,
    )


@pytest.fixture
async def bus():
    """Provide a connected Bus that creates a unique test stream + consumer."""
    stream_name = f"TEST_{uuid.uuid4().hex[:8].upper()}"
    consumer_name = "test_consumer"
    subject = f"test.{stream_name.lower()}"

    async with Bus.connect(NATS_URL, name="test-bus") as b:
        await b.ensure_stream(
            name=stream_name,
            subjects=[subject],
            max_age_seconds=60,
            max_msgs=100,
        )
        await b.ensure_consumer(
            stream=stream_name,
            consumer=consumer_name,
            filter_subject=subject,
            ack_wait_seconds=5,
            max_deliver=2,
            max_ack_pending=10,
        )
        yield b, stream_name, consumer_name, subject

        # Cleanup
        try:
            await b.js.delete_stream(stream_name)
        except Exception:
            pass


async def test_bus_publish_and_subscribe_roundtrip(bus) -> None:
    b, stream, consumer, subject = bus
    received: list[TriggerEvent] = []
    done = asyncio.Event()

    async def handler(event: TriggerEvent) -> None:
        received.append(event)
        done.set()

    await b.subscribe(
        stream=stream,
        consumer=consumer,
        model=TriggerEvent,
        handler=handler,
    )

    sent = _make_event("rt_1")
    await b.publish(subject, sent)

    try:
        await asyncio.wait_for(done.wait(), timeout=10)
    except TimeoutError:
        pytest.fail("did not receive published message within 10s")

    assert len(received) == 1
    assert received[0].event_id == sent.event_id


async def test_bus_propagates_trace_id_via_headers(bus) -> None:
    b, stream, consumer, subject = bus
    captured: list[str | None] = []
    done = asyncio.Event()

    async def handler(event: TriggerEvent) -> None:
        import structlog

        ctx = structlog.contextvars.get_contextvars()
        captured.append(ctx.get("trace_id"))
        done.set()

    await b.subscribe(stream=stream, consumer=consumer, model=TriggerEvent, handler=handler)

    explicit_trace = "a" * 32
    await b.publish(subject, _make_event("rt_2"), trace_id=explicit_trace)

    try:
        await asyncio.wait_for(done.wait(), timeout=10)
    except TimeoutError:
        pytest.fail("did not receive published message within 10s")

    assert captured == [explicit_trace]


async def test_bus_rejects_invalid_payload_via_term(bus) -> None:
    """A garbled payload should be terminated (not redelivered)."""
    b, stream, consumer, subject = bus
    received_count = 0

    async def handler(_event: TriggerEvent) -> None:
        nonlocal received_count
        received_count += 1

    await b.subscribe(stream=stream, consumer=consumer, model=TriggerEvent, handler=handler)

    # Publish raw invalid JSON directly via JetStream.
    await b.js.publish(subject, b'{"not": "a valid trigger event"}')

    # Give the consumer time to process + term.
    await asyncio.sleep(2)
    assert received_count == 0  # validation should have terminated the message
