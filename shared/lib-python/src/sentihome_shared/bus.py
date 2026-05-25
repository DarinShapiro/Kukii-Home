"""NATS JetStream client wrappers.

Thin, opinionated wrappers around `nats-py` that:

- Encode/decode messages as JSON
- Inject and propagate the current trace ID
- Validate against pydantic models when provided
- Centralize backoff + reconnect behavior

Full implementation lands in Epic 02 (Event Bus). This module currently
exposes type stubs and a minimal connect helper sufficient for downstream
code to import without crashing.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from nats.aio.client import Client as NATSClient


@asynccontextmanager
async def connect(url: str) -> AsyncIterator[NATSClient]:
    """Open a NATS connection. Usage::

        async with connect("nats://localhost:4222") as nc:
            await nc.publish("subject", b"payload")

    Note:
        Full publish/subscribe with schema validation + trace propagation
        is implemented in Epic 02 sub-issues. This wrapper is the minimum
        viable connection helper.
    """
    import nats

    nc = await nats.connect(url)
    try:
        yield nc
    finally:
        await nc.close()


async def publish_json(nc: Any, subject: str, payload: dict[str, Any]) -> None:
    """Publish a JSON payload to ``subject``.

    Convenience wrapper. Real implementation (Epic 02) will validate against
    a JSON schema and inject trace context automatically.
    """
    import json

    await nc.publish(subject, json.dumps(payload).encode())
