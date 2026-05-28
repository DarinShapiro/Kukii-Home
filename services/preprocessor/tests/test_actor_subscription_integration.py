"""End-to-end NATS flow for the inbound actor-enrollment broadcast.

Spins up an ephemeral NATS container via testcontainers, wires a real
:class:`ActorEnrollmentSubscriber`, and verifies that an
``ActorEnrollmentEvent`` published to SUBJECT_ACTOR_ENROLLED lands
in the cache (and a deactivation removes it).

This is the ONLY NATS flow in the corrected preprocessor architecture
— config-state broadcast from memory to preprocessor. All other RPCs
are REST.

Marked ``integration`` so unit-test runs skip it. Run with
``pytest -m integration``. Skips cleanly when Docker isn't available.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from sentihome_preprocessor.nats_subscriber import ActorEnrollmentSubscriber
from sentihome_preprocessor.state import ActorCache
from sentihome_shared.preprocessor import (
    SUBJECT_ACTOR_DEACTIVATED,
    SUBJECT_ACTOR_ENROLLED,
    ActorEnrollmentEvent,
)


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def nats_container():
    if not _docker_available():
        pytest.skip("Docker daemon not reachable; integration tests need Docker.")
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = (
        DockerContainer("nats:2.11-alpine")
        .with_command("-js -m 8222")
        .with_exposed_ports(4222, 8222)
    )
    container.start()
    try:
        wait_for_logs(container, "Server is ready", timeout=30)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(4222)
        yield f"nats://{host}:{port}"
    finally:
        container.stop()


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_actor_enrollment_flows_into_cache(nats_container: str):
    """Publish to SUBJECT_ACTOR_ENROLLED; subscriber should populate cache."""
    import nats

    cache = ActorCache()
    subscriber = ActorEnrollmentSubscriber(nats_container, cache)
    await subscriber.connect()

    publisher_nc = await nats.connect(servers=[nats_container])
    await asyncio.sleep(0.1)  # let subscription register

    event = ActorEnrollmentEvent(
        actor_id="actor_alice",
        action="enrolled",
        name="Alice",
        role="resident",
        access_profile="full",
        face_embedding=tuple(0.01 * i for i in range(8)),
    )
    await publisher_nc.publish(SUBJECT_ACTOR_ENROLLED, event.model_dump_json().encode("utf-8"))
    await publisher_nc.flush()

    try:
        for _ in range(20):
            cached = await cache.get("actor_alice")
            if cached is not None:
                break
            await asyncio.sleep(0.05)
        cached = await cache.get("actor_alice")
        assert cached is not None, "actor never showed up in cache"
        assert cached.name == "Alice"
        assert cached.face_embedding == event.face_embedding
    finally:
        with contextlib.suppress(Exception):
            await publisher_nc.drain()
        await subscriber.close()


@pytest.mark.asyncio
async def test_actor_deactivation_removes_from_cache(nats_container: str):
    """Publish to SUBJECT_ACTOR_DEACTIVATED; subscriber drops actor."""
    import nats

    cache = ActorCache()
    # Seed with an existing actor (bypass NATS for setup).
    await cache.upsert(ActorEnrollmentEvent(actor_id="actor_bob", action="enrolled", name="Bob"))

    subscriber = ActorEnrollmentSubscriber(nats_container, cache)
    await subscriber.connect()

    publisher_nc = await nats.connect(servers=[nats_container])
    await asyncio.sleep(0.1)

    event = ActorEnrollmentEvent(actor_id="actor_bob", action="deactivated")
    await publisher_nc.publish(SUBJECT_ACTOR_DEACTIVATED, event.model_dump_json().encode("utf-8"))
    await publisher_nc.flush()

    try:
        for _ in range(20):
            if await cache.get("actor_bob") is None:
                break
            await asyncio.sleep(0.05)
        assert await cache.get("actor_bob") is None, (
            "actor should have been removed from cache by deactivation"
        )
    finally:
        with contextlib.suppress(Exception):
            await publisher_nc.drain()
        await subscriber.close()
