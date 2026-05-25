"""In-memory metadata cache for frame windows.

Keyed by ``(camera_id, rounded_timestamp)`` — frame windows are deduplicated
within a small time bucket so repeated queries for the same window don't
re-run preprocessing.

This is the in-process backend; a Redis-backed implementation can be added by
implementing the ``MetadataCache`` protocol.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol


class MetadataCache(Protocol):
    """Async cache interface."""

    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def put(self, key: str, value: dict[str, Any], *, ttl_seconds: float) -> None: ...


@dataclass
class InMemoryMetadataCache:
    """LRU + TTL cache. NOT thread-safe; use one per service instance."""

    max_entries: int = 1024
    _store: OrderedDict[str, tuple[float, dict[str, Any]]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._store = OrderedDict()

    async def get(self, key: str) -> dict[str, Any] | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._store[key]
            return None
        # Move to end (most recently used)
        self._store.move_to_end(key)
        return value

    async def put(self, key: str, value: dict[str, Any], *, ttl_seconds: float) -> None:
        expires_at = time.monotonic() + ttl_seconds
        self._store[key] = (expires_at, value)
        self._store.move_to_end(key)
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)

    def size(self) -> int:
        return len(self._store)
