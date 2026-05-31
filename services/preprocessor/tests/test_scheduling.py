"""Tests for the identity DAG ResourcePool + router budget (10.11.3a)."""

from __future__ import annotations

import asyncio
import time

import pytest
from kukiihome_preprocessor.pipelines.identity.scheduling import ResourcePool


@pytest.mark.asyncio
async def test_pool_size_one_serializes_same_class():
    """A pool of size 1 forces same-class work to run one at a time."""
    pool = ResourcePool({"gpu": 1})
    order: list[str] = []

    async def task(name: str) -> None:
        async with pool.slot("gpu"):
            order.append(f"{name}:start")
            await asyncio.sleep(0.02)
            order.append(f"{name}:end")

    await asyncio.gather(task("a"), task("b"))
    # Serialized: one fully completes before the other starts.
    assert order in (
        ["a:start", "a:end", "b:start", "b:end"],
        ["b:start", "b:end", "a:start", "a:end"],
    )


@pytest.mark.asyncio
async def test_pool_size_two_allows_overlap():
    """A pool of size 2 lets two same-class tasks overlap."""
    pool = ResourcePool({"gpu": 2})
    active = 0
    peak = 0

    async def task() -> None:
        nonlocal active, peak
        async with pool.slot("gpu"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(task(), task())
    assert peak == 2


@pytest.mark.asyncio
async def test_unknown_class_uses_default_size():
    pool = ResourcePool(default_size=3)
    assert pool.size("something_new") == 3


@pytest.mark.asyncio
async def test_slot_releases_on_exception():
    """A raising body must still release the slot (else deadlock)."""
    pool = ResourcePool({"gpu": 1})
    with pytest.raises(ValueError, match="boom"):
        async with pool.slot("gpu"):
            raise ValueError("boom")
    # Slot is free again — this acquire returns promptly.
    t0 = time.perf_counter()
    async with pool.slot("gpu"):
        pass
    assert (time.perf_counter() - t0) < 0.5
