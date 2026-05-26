"""Service entry point — loads topology and idles.

The MOG2 motion + corroboration + cache (``preprocessor/`` modules) are
fully implemented as importable Python. The standalone daemon that
consumes camera frames and emits enriched events wires in alongside the
adapter runtime (Epic 10+).
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
        "preprocessor.idle",
        adapters=[a.name for a in topology.adapters],
        hint="standalone consumer wires in Epic 10+",
    )
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
