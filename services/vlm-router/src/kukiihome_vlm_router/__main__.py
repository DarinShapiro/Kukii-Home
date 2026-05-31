"""Service entry point — loads topology and idles.

The Router + RoutingPolicy + CircuitBreaker + Telemetry are fully
implemented as importable Python; an in-container HTTP/MCP server
surfaces them once the bus runtime ties services together (Epic 10+).
"""

from __future__ import annotations

import asyncio
import logging
import os

import structlog
from kukiihome_shared.topology import load_topology

logger = structlog.get_logger(__name__)


async def _run() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    topology = load_topology()
    logger.info(
        "vlm_router.idle",
        backends=[b.name for b in topology.vlm_router.backends],
        hint="standalone server wires in Epic 10+; routing is in-process today",
    )
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
