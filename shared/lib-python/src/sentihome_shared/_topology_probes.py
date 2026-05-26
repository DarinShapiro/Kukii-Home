"""Bootstrap dependency-ping for :func:`Topology.verify`.

Kept separate from :mod:`sentihome_shared.topology` so importing the model
doesn't pull in nats/httpx/asyncpg/redis clients at parse time.
"""

from __future__ import annotations

import asyncio
import time

import structlog

from sentihome_shared.topology import BootstrapReport, ProbeStatus, Topology

logger = structlog.get_logger(__name__)


async def _probe_nats(
    url: str,
    *,
    timeout: float,  # noqa: ASYNC109
) -> tuple[ProbeStatus, str]:
    try:
        import nats  # type: ignore[import-untyped]
    except ImportError:
        return ProbeStatus.skipped, "nats-py not installed"
    try:
        nc = await asyncio.wait_for(nats.connect(url), timeout=timeout)
        await nc.close()
        return ProbeStatus.ok, ""
    except Exception as e:
        return ProbeStatus.unreachable, str(e)


async def _probe_postgres(
    url: str,
    *,
    timeout: float,  # noqa: ASYNC109
) -> tuple[ProbeStatus, str]:
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError:
        return ProbeStatus.skipped, "sqlalchemy not installed"
    try:
        engine = create_async_engine(url, connect_args={"timeout": timeout})
        async with engine.begin() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=timeout)
        await engine.dispose()
        return ProbeStatus.ok, ""
    except Exception as e:
        return ProbeStatus.unreachable, str(e)


async def _probe_http(
    url: str,
    *,
    timeout: float,  # noqa: ASYNC109
    path: str = "",
) -> tuple[ProbeStatus, str]:
    try:
        import httpx
    except ImportError:
        return ProbeStatus.skipped, "httpx not installed"
    target = url.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(target)
            if resp.status_code >= 500:
                return ProbeStatus.unreachable, f"HTTP {resp.status_code}"
            return ProbeStatus.ok, f"HTTP {resp.status_code}"
    except Exception as e:
        return ProbeStatus.unreachable, str(e)


async def _probe_redis(
    url: str,
    *,
    timeout: float,  # noqa: ASYNC109
) -> tuple[ProbeStatus, str]:
    try:
        from redis import asyncio as redis_asyncio  # type: ignore[import-not-found]
    except ImportError:
        return ProbeStatus.skipped, "redis not installed"
    try:
        r = redis_asyncio.from_url(url, socket_timeout=timeout)
        pong = await asyncio.wait_for(r.ping(), timeout=timeout)
        await r.close()
        if pong:
            return ProbeStatus.ok, ""
        return ProbeStatus.unreachable, "PING returned falsy"
    except Exception as e:
        return ProbeStatus.unreachable, str(e)


async def _probe_ollama(
    url: str,
    *,
    timeout: float,  # noqa: ASYNC109
) -> tuple[ProbeStatus, str]:
    return await _probe_http(url, timeout=timeout, path="/api/tags")


async def _probe_openai_compatible(
    url: str,
    *,
    api_key: str | None,
    timeout: float,  # noqa: ASYNC109
) -> tuple[ProbeStatus, str]:
    try:
        import httpx
    except ImportError:
        return ProbeStatus.skipped, "httpx not installed"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    target = url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            resp = await client.get(target)
            if resp.status_code in (200, 401):
                # 401 still proves reachability; auth is a separate concern.
                return ProbeStatus.ok, f"HTTP {resp.status_code}"
            return ProbeStatus.unreachable, f"HTTP {resp.status_code}"
    except Exception as e:
        return ProbeStatus.unreachable, str(e)


async def probe_topology(
    topology: Topology,
    *,
    timeout: float,  # noqa: ASYNC109
) -> BootstrapReport:
    """Run every probe declared by ``topology`` concurrently."""
    report = BootstrapReport()

    async def run(name: str, coro):
        start = time.monotonic()
        try:
            status, detail = await coro
        except Exception as e:  # defensive — individual probes catch their own
            status, detail = ProbeStatus.error, str(e)
        report.add(name=name, status=status, started_monotonic=start, detail=detail)

    tasks = [
        run("bus.nats", _probe_nats(topology.bus.nats_url, timeout=timeout)),
        run("memory.postgres", _probe_postgres(topology.memory.postgres_url, timeout=timeout)),
        run(
            "memory.qdrant",
            _probe_http(topology.memory.qdrant_url, timeout=timeout, path="/healthz"),
        ),
        run("memory.redis", _probe_redis(topology.memory.redis_url, timeout=timeout)),
    ]
    if topology.ha_agent.ha_token:
        tasks.append(
            run(
                "ha_agent",
                _probe_http(topology.ha_agent.ha_url, timeout=timeout, path="/api/"),
            )
        )
    for backend in topology.vlm_router.backends:
        if backend.kind == "ollama":
            probe = _probe_ollama(backend.base_url, timeout=timeout)
        elif backend.kind in ("openai_compatible", "vllm"):
            probe = _probe_openai_compatible(
                backend.base_url, api_key=backend.api_key, timeout=timeout
            )
        else:
            continue
        tasks.append(run(f"vlm.{backend.name}", probe))

    await asyncio.gather(*tasks)
    return report
