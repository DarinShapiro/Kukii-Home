"""Graph-backed memory: protocol, types, and two implementations.

This package defines the substrate the SentiHome memory subsystem uses
to store + query the Hebbian-style memory graph. Two implementations
satisfy the same :class:`GraphClient` protocol:

- :class:`InMemoryGraphClient` — pure Python dicts. Fast, no deps,
  used by the harness's default test path. Runs anywhere.
- :class:`Neo4jGraphClient` — real Neo4j 5.x driver. Production target
  + integration tests via testcontainers.

Both are interchangeable behind the protocol. The harness can run
the same scenarios on either backend; differential tests assert the
implementations agree on outcomes for the same operations.
"""

from sentihome_memory.graph.client import (
    GraphClient,
    InMemoryGraphClient,
    Neo4jGraphClient,
)
from sentihome_memory.graph.types import (
    CitedEdge,
    Event,
    KnownActor,
    NodeKind,
    PruneCandidate,
    VLMDecision,
)

__all__ = [
    "CitedEdge",
    "Event",
    "GraphClient",
    "InMemoryGraphClient",
    "KnownActor",
    "Neo4jGraphClient",
    "NodeKind",
    "PruneCandidate",
    "VLMDecision",
]
