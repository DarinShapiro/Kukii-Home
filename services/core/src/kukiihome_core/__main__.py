"""Service entry point.

The core service's heavy machinery (triage worker, rule engine, action
dispatcher) is fully implemented as importable Python — see ``triage.py``,
``rules.py``, ``dispatch.py``. The long-running daemon that subscribes to
NATS and ties them together lands when the bus is wired into the add-on
runtime (Epic 10+).

Until then, this entrypoint loads the topology (so misconfiguration is
caught at startup, not the first event) and idles. s6 sees a running
process and stops crash-looping.
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
        "core.idle",
        profile=topology.deployment.profile,
        nats=topology.bus.nats_url,
        hint="full runtime wires in Epic 10+",
    )
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
