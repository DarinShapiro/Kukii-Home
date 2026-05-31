"""Tests for the bounded diagnostic ring."""

from __future__ import annotations

import pytest
from kukiihome_shared.health import DiagnosticEntry, DiagnosticRing


def _e(ts: float, level: str, component: str = "c", message: str = "m") -> DiagnosticEntry:
    return DiagnosticEntry(ts=ts, level=level, component=component, message=message)


@pytest.mark.asyncio
async def test_ring_is_bounded():
    ring = DiagnosticRing(maxlen=3)
    for i in range(5):
        await ring.record(_e(float(i), "info"))
    assert await ring.size() == 3
    recent = await ring.recent()
    # Newest first; oldest two evicted (ts 0,1 gone).
    assert [e.ts for e in recent] == [4.0, 3.0, 2.0]


@pytest.mark.asyncio
async def test_recent_respects_limit():
    ring = DiagnosticRing()
    for i in range(10):
        await ring.record(_e(float(i), "info"))
    assert len(await ring.recent(limit=3)) == 3


@pytest.mark.asyncio
async def test_min_level_filter():
    ring = DiagnosticRing()
    await ring.record(_e(1.0, "info"))
    await ring.record(_e(2.0, "warning"))
    await ring.record(_e(3.0, "critical"))
    warns = await ring.recent(min_level="warning")
    assert [e.level for e in warns] == ["critical", "warning"]
    crits = await ring.recent(min_level="critical")
    assert [e.level for e in crits] == ["critical"]


def test_maxlen_must_be_positive():
    with pytest.raises(ValueError, match="maxlen"):
        DiagnosticRing(maxlen=0)
