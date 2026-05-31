"""Service entry point — loads topology and idles.

The MemoryStore facade (``store.py``) is fully implemented and used
in-process by the core service. The standalone MCP server that exposes
memory.* tools over the network wires in alongside Epic 10+.
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
        "memory.idle",
        postgres=topology.memory.postgres_url.split("@")[-1],
        qdrant=topology.memory.qdrant_url,
        hint="standalone MCP server wires in Epic 10+; facade is in-process today",
    )
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
