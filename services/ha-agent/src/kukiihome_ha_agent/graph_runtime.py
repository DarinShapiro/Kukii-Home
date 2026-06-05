"""Graph substrate runtime for the add-on (Epic 10.2, Phase 1+2).

The memory graph (`kukiihome_memory.graph`) has two interchangeable
backends behind one Protocol:

- :class:`InMemoryGraphClient` — process-local dicts. No persistence, no
  external dependency. This is the **Phase 1** shadow backend: the add-on
  mirrors events + policies into it to prove the integration seam without
  standing up any infrastructure.
- :class:`Neo4jGraphClient` — real Neo4j 5.x via the bolt driver. The
  **Phase 2** backend: a Neo4j sidecar (s6 process in the add-on
  container, persisting to ``/data``) or any reachable bolt URL. Durable +
  native vector index.

:func:`make_graph_client` decides which to use from configuration, with
ONE inviolable rule: **a graph backend never breaks boot.** If Neo4j is
configured but unreachable / mis-credentialed / slow to start, we log it
and fall back to the in-memory backend. The add-on keeps running with a
shadow graph; the operator sees the degraded backend in /diagnostics.
That mirrors how the LLM dispatcher degrades to the heuristic provider.

The bolt URL is configuration, not a constant, precisely so a future
placement optimizer can repoint the graph at wherever it decides to run
Neo4j (localhost sidecar today; the inference box tomorrow) by changing
one env var.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def make_graph_client(
    *,
    neo4j_url: str = "",
    neo4j_user: str = "neo4j",
    neo4j_password: str = "",
) -> tuple[Any, str]:
    """Build the graph client + return ``(client, backend_name)``.

    ``backend_name`` is ``"neo4j"`` or ``"in_memory"`` — surfaced in
    /diagnostics so the operator can see which substrate is live.

    Selection:
    - ``neo4j_url`` empty → in-memory (Phase 1 default; no infra).
    - ``neo4j_url`` set → attempt Neo4j: open driver, verify
      connectivity, initialize schema. On ANY failure, log + fall back
      to in-memory. Boot never fails because of the graph.
    """
    from kukiihome_memory.graph.client import InMemoryGraphClient

    url = (neo4j_url or "").strip()
    if not url:
        logger.info("graph.backend.in_memory", reason="no_neo4j_url")
        return InMemoryGraphClient(), "in_memory"

    try:
        from kukiihome_memory.graph.client import Neo4jGraphClient
        from neo4j import GraphDatabase

        auth = (neo4j_user, neo4j_password) if neo4j_password else None
        driver = GraphDatabase.driver(url, auth=auth)
        # verify_connectivity raises if the server isn't reachable / auth
        # is wrong. Doing it here (not lazily on first query) means a
        # misconfig surfaces at boot as a clean fallback, not as a stream
        # of per-write exceptions later.
        driver.verify_connectivity()
        client = Neo4jGraphClient(driver=driver)
        client.initialize_schema()
        logger.info("graph.backend.neo4j", url=_redact(url))
        return client, "neo4j"
    except Exception as e:
        logger.warning(
            "graph.backend.neo4j_unavailable.fallback_in_memory",
            url=_redact(url),
            error=str(e),
        )
        return InMemoryGraphClient(), "in_memory"


def _redact(url: str) -> str:
    """Strip any embedded credentials from a bolt URL before logging.

    ``bolt://user:pass@host:7687`` → ``bolt://host:7687``. Defensive —
    we don't construct URLs with inline creds, but a misconfigured option
    could, and the URL lands in logs.
    """
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _creds, _, host = rest.rpartition("@")
    return f"{scheme}://{host}" if scheme else host
