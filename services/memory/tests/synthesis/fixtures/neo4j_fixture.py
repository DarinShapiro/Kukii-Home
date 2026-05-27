"""Ephemeral Neo4j container for integration tests.

Spins up a Neo4j 5.x container once per test session via
testcontainers. Each test gets a fresh, schema-initialized graph
client (data cleared between tests).

Skipped automatically when Docker isn't available, so unit tests on
the in-memory backend still pass on machines without Docker.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


def _docker_available() -> bool:
    """Return True if we can talk to a Docker daemon.

    testcontainers itself swallows the underlying error and surfaces a
    cryptic timeout; pre-checking here gives the user a clean
    SKIPPED reason.
    """
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def neo4j_container() -> Iterator[object]:
    """Session-scoped Neo4j 5.x container.

    One container shared across the whole pytest session. Tests
    clean their own data via :meth:`GraphClient.clear_all` in their
    per-test fixture. This keeps the ~5-10s container-startup cost
    paid once instead of per test.

    Skips cleanly when Docker isn't reachable.
    """
    if not _docker_available():
        pytest.skip(
            "Docker daemon not reachable — integration tests against Neo4j "
            "require Docker Desktop running. Unit tests against the "
            "InMemoryGraphClient continue to run."
        )

    # Lazy import: we don't want to break unit tests on machines that
    # have testcontainers' transitive deps misbehaving.
    from testcontainers.neo4j import Neo4jContainer

    # testcontainers' Neo4jContainer manages auth via constructor args.
    # The `with_env("NEO4J_AUTH", ...)` approach we tried first was
    # ignored by the wait-for-ready strategy + by the container's own
    # internal connection setup, producing AuthError on every query.
    container = Neo4jContainer(
        "neo4j:5.20-community",
        username="neo4j",
        password="testpassword",
    )
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
def neo4j_driver(neo4j_container):
    """Per-test Neo4j driver pointing at the session-scoped container.

    Use the container's own ``get_driver()`` so credentials match what
    testcontainers configured. Avoids hand-coding the password and
    drifting out of sync with the container fixture's setup.
    """
    driver = neo4j_container.get_driver()
    try:
        yield driver
    finally:
        driver.close()


@pytest.fixture
def neo4j_client(neo4j_driver):
    """A fresh Neo4jGraphClient with schema initialized + data cleared."""
    from sentihome_memory.graph import Neo4jGraphClient

    client = Neo4jGraphClient(driver=neo4j_driver)
    client.initialize_schema()
    client.clear_all()
    return client


@pytest.fixture
def in_memory_client():
    """A fresh InMemoryGraphClient."""
    from sentihome_memory.graph import InMemoryGraphClient

    return InMemoryGraphClient()
