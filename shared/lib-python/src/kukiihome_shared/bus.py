"""NATS JetStream client wrappers.

Schema-validated publish/subscribe primitives. Handles:

- JSON encoding/decoding
- Pydantic model validation on publish + on receive
- Trace ID propagation via headers
- Async-friendly subscribe with handler registration
- Connection lifecycle + backoff

Example::

    from kukiihome_shared.bus import Bus
    from kukiihome_shared.generated.events.trigger_event import TriggerEvent

    async with Bus.connect("nats://localhost:4222") as bus:
        await bus.publish("vlm.normal", trigger_event)

        async def handle(event: TriggerEvent) -> None:
            ...

        await bus.subscribe(
            stream="EVENTS",
            consumer="triage",
            model=TriggerEvent,
            handler=handle,
        )
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Self

import nats
import structlog
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext
from nats.js.api import ConsumerConfig, StreamConfig
from pydantic import BaseModel, ValidationError

from kukiihome_shared.tracing import new_trace_id, trace_context

if TYPE_CHECKING:
    from nats.aio.msg import Msg

logger = structlog.get_logger(__name__)


class BusError(Exception):
    """Base error for bus operations."""


class SchemaValidationError(BusError):
    """Raised when a published or received message fails schema validation."""


class Bus:
    """High-level async wrapper around a NATS JetStream connection.

    Use as an async context manager::

        async with Bus.connect("nats://localhost:4222") as bus:
            ...

    On exit, all subscriptions are cancelled and the connection drained.
    """

    def __init__(self, nc: NATSClient, js: JetStreamContext) -> None:
        self._nc = nc
        self._js = js
        self._tasks: list[asyncio.Task[None]] = []

    @classmethod
    @asynccontextmanager
    async def connect(
        cls,
        url: str = "nats://localhost:4222",
        *,
        name: str | None = None,
    ) -> AsyncIterator[Self]:
        """Open a JetStream-enabled NATS connection."""
        nc = await nats.connect(url, name=name or "kukiihome")
        js = nc.jetstream()
        bus = cls(nc, js)
        try:
            yield bus
        finally:
            await bus._drain_and_close()

    @property
    def js(self) -> JetStreamContext:
        """Direct access to the JetStream context for advanced operations."""
        return self._js

    async def publish(
        self,
        subject: str,
        message: BaseModel,
        *,
        trace_id: str | None = None,
        stream: str | None = None,
    ) -> None:
        """Publish a pydantic-validated message to ``subject``.

        Args:
            subject: NATS subject (e.g. "vlm.urgent").
            message: A pydantic ``BaseModel`` instance. Serialized as JSON.
            trace_id: Optional trace ID for the header. Falls back to context vars
                or generates a fresh one.
            stream: Optional stream name hint.
        """
        if trace_id is None:
            ctx = structlog.contextvars.get_contextvars()
            trace_id = ctx.get("trace_id") or new_trace_id()

        try:
            payload = message.model_dump_json().encode("utf-8")
        except ValueError as e:  # pragma: no cover
            raise SchemaValidationError(f"Failed to serialize message: {e}") from e

        headers = {"trace_id": trace_id, "model": type(message).__name__}
        try:
            await self._js.publish(subject, payload, headers=headers, stream=stream)
        except Exception as e:
            logger.error(
                "bus.publish_failed",
                subject=subject,
                error=str(e),
                model=type(message).__name__,
            )
            raise

        logger.debug(
            "bus.published",
            subject=subject,
            model=type(message).__name__,
            trace_id=trace_id,
        )

    async def subscribe[T: BaseModel](
        self,
        *,
        stream: str,
        consumer: str,
        model: type[T],
        handler: Callable[[T], Awaitable[None]],
        max_in_flight: int = 1,
    ) -> None:
        """Subscribe to a durable JetStream consumer with schema validation.

        Messages are decoded into ``model``; a fresh trace_context is bound for
        the duration of the handler call; ack on success, nak on raised exception
        (JetStream re-delivers up to ``max_deliver`` per consumer config).

        Returns immediately — consumption runs as a background task tied to
        the Bus's lifecycle.
        """
        sub = await self._js.pull_subscribe_bind(consumer=consumer, stream=stream)

        async def consume_loop() -> None:
            while True:
                try:
                    msgs = await sub.fetch(batch=max_in_flight, timeout=5)
                except TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        "bus.fetch_failed",
                        stream=stream,
                        consumer=consumer,
                        error=str(e),
                    )
                    await asyncio.sleep(1)
                    continue

                for msg in msgs:
                    await self._handle_one(msg, model, handler)

        task = asyncio.create_task(consume_loop(), name=f"bus-sub-{stream}-{consumer}")
        self._tasks.append(task)

    async def _handle_one[T: BaseModel](
        self,
        msg: Msg,
        model: type[T],
        handler: Callable[[T], Awaitable[None]],
    ) -> None:
        headers = msg.headers or {}
        trace_id = headers.get("trace_id") or new_trace_id()

        try:
            obj = model.model_validate_json(msg.data)
        except ValidationError as e:
            logger.error(
                "bus.schema_validation_failed",
                subject=msg.subject,
                model=model.__name__,
                error=str(e),
                trace_id=trace_id,
            )
            # Don't redeliver schema errors — they aren't retryable
            await msg.term()
            return

        with trace_context(trace_id=trace_id, subject=msg.subject):
            try:
                await handler(obj)
                await msg.ack()
            except Exception:
                logger.exception(
                    "bus.handler_failed",
                    subject=msg.subject,
                    model=model.__name__,
                )
                await msg.nak()

    async def ensure_stream(
        self,
        name: str,
        subjects: list[str],
        *,
        max_age_seconds: int | None = None,
        max_msgs: int | None = None,
        max_msg_size: int | None = None,
        duplicate_window_seconds: int | None = None,
    ) -> None:
        """Idempotently ensure a stream exists with the given config.

        Used by bootstrap code to apply the streams declared in
        ``infrastructure/nats/streams.yaml``.
        """
        kwargs: dict[str, Any] = {
            "name": name,
            "subjects": subjects,
        }
        # nats-py StreamConfig expresses durations in SECONDS and
        # converts to nanoseconds itself when serializing (see
        # nats.js.api._to_nanoseconds). Passing pre-multiplied ns here
        # double-converts → e.g. 60s became 6e19 ns, overflowing the
        # server's int64 time.Duration (BadRequestError 10025).
        if max_age_seconds is not None:
            kwargs["max_age"] = max_age_seconds
        if max_msgs is not None:
            kwargs["max_msgs"] = max_msgs
        if max_msg_size is not None:
            kwargs["max_msg_size"] = max_msg_size
        if duplicate_window_seconds is not None:
            kwargs["duplicate_window"] = duplicate_window_seconds

        config = StreamConfig(**kwargs)
        try:
            await self._js.add_stream(config=config)
            logger.info("bus.stream_created", name=name, subjects=subjects)
        except Exception as e:
            if "already in use" in str(e).lower() or "name already" in str(e).lower():
                logger.debug("bus.stream_exists", name=name)
            else:
                raise

    async def ensure_consumer(
        self,
        stream: str,
        consumer: str,
        *,
        filter_subject: str | None = None,
        ack_wait_seconds: int = 30,
        max_deliver: int = 3,
        max_ack_pending: int = 100,
    ) -> None:
        """Idempotently ensure a durable pull consumer exists."""
        config = ConsumerConfig(
            durable_name=consumer,
            filter_subject=filter_subject,
            # ack_wait is in SECONDS — nats-py converts to ns itself
            # (same reasoning as max_age in ensure_stream).
            ack_wait=ack_wait_seconds,
            max_deliver=max_deliver,
            max_ack_pending=max_ack_pending,
        )
        try:
            await self._js.add_consumer(stream=stream, config=config)
            logger.info("bus.consumer_created", stream=stream, consumer=consumer)
        except Exception as e:
            if "already in use" in str(e).lower():
                logger.debug("bus.consumer_exists", stream=stream, consumer=consumer)
            else:
                raise

    async def _drain_and_close(self) -> None:
        for task in self._tasks:
            task.cancel()
        # Wait for tasks to settle (best-effort)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._nc.drain()


# ─────────────────────────────────────────────────────────────────────
# Legacy low-level helpers (retained for compatibility — prefer ``Bus``)
# ─────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def connect(url: str) -> AsyncIterator[NATSClient]:
    """Legacy low-level connect helper. Prefer ``Bus.connect`` for new code."""
    nc = await nats.connect(url)
    try:
        yield nc
    finally:
        await nc.close()


async def publish_json(nc: NATSClient, subject: str, payload: dict[str, Any]) -> None:
    """Legacy low-level publish. Prefer ``Bus.publish`` for new code."""
    await nc.publish(subject, json.dumps(payload).encode())
