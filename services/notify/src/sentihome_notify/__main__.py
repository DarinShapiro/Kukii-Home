"""Service entry point — loads topology and idles.

The push/TTS/Ask dispatchers (``dispatcher.py``) are fully implemented and
imported directly by the core service's ActionDispatcher in-process. The
standalone NATS-consumer daemon wires in alongside the core runtime in
Epic 10+; until then this entrypoint idles so s6 doesn't crash-loop.
"""

from __future__ import annotations

import asyncio
import logging
import os

import structlog
from sentihome_shared.topology import load_topology

logger = structlog.get_logger(__name__)


async def _run() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    topology = load_topology()
    logger.info(
        "notify.idle",
        residents=list(topology.notify.resident_to_push_service),
        media_players=topology.notify.media_players,
        hint="standalone consumer wires in Epic 10+; dispatchers are in-process today",
    )
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
