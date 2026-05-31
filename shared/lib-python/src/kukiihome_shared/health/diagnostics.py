"""Bounded diagnostic ring — the queryable failure trail (§19).

§19 requires every failure logged with timestamp, component, message,
impact, and recovery action, queryable as "the last 100 entries". This
is that store: an in-memory bounded ring the watchdog writes to on every
health transition, and the ``/diagnostics`` endpoint + in-app logs UI
read from.

In-memory + bounded by design — it's a rolling diagnostic window, not a
durable audit log (that's the append-only event log in the memory
service). Survives nothing; repopulates as the watchdog observes.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict

# info = recovered / normal transitions; warning = degraded / recoverable
# failure; critical = critical-class component down (§19 operator alerts).
DiagnosticLevel = Literal["info", "warning", "critical"]

_LEVEL_RANK: dict[DiagnosticLevel, int] = {"info": 0, "warning": 1, "critical": 2}


class DiagnosticEntry(BaseModel):
    """One diagnostic record, mirroring §19's log format."""

    model_config = ConfigDict(extra="forbid")

    ts: float
    level: DiagnosticLevel
    component: str
    message: str
    impact: str | None = None
    """What degraded as a result (e.g. "world state using cached snapshot")."""
    recovery: str | None = None
    """What the system did / will do (e.g. "retry reconnect every 5s")."""
    duration_s: float | None = None
    """For resolved intermittent issues: how long it lasted."""


class DiagnosticRing:
    """Async-safe bounded ring of :class:`DiagnosticEntry` (default 100,
    per §19's "last 100 entries")."""

    def __init__(self, *, maxlen: int = 100) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self._entries: deque[DiagnosticEntry] = deque(maxlen=maxlen)
        self._lock = asyncio.Lock()

    async def record(self, entry: DiagnosticEntry) -> None:
        async with self._lock:
            self._entries.append(entry)

    async def recent(
        self,
        *,
        limit: int = 100,
        min_level: DiagnosticLevel | None = None,
    ) -> tuple[DiagnosticEntry, ...]:
        """Most-recent entries first, optionally filtered to ``min_level``
        and above (``warning`` → warnings + criticals)."""
        async with self._lock:
            items = list(self._entries)
        if min_level is not None:
            floor = _LEVEL_RANK[min_level]
            items = [e for e in items if _LEVEL_RANK[e.level] >= floor]
        items.reverse()  # newest first
        return tuple(items[:limit])

    async def size(self) -> int:
        async with self._lock:
            return len(self._entries)
