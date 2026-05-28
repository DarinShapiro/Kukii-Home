"""In-process state: the KnownActor cache.

The preprocessor needs the face embedding (etc.) of every KnownActor
in order to run recognition. The canonical source of truth lives in
the memory service's Neo4j graph; the preprocessor maintains a local
read-only cache kept fresh by NATS subscription
(``SUBJECT_ACTOR_ENROLLED`` etc.).

This module is intentionally tiny — async-safe upsert/remove keyed
by actor_id, plus a snapshot read. No persistence: the cache rebuilds
on restart from the JetStream replay of actor events.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sentihome_shared.preprocessor import ActorEnrollmentEvent


@dataclass
class ActorCache:
    """Async-safe cache of currently-active KnownActors.

    Held as a long-lived singleton in the running process. The
    FastAPI app + the fake-detection loop both read from it; the
    NATS subscriber is the only writer.
    """

    _by_id: dict[str, ActorEnrollmentEvent] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def upsert(self, event: ActorEnrollmentEvent) -> None:
        """Apply an ``enrolled`` or ``updated`` event. Idempotent on
        actor_id — repeated updates simply overwrite."""
        if event.action not in ("enrolled", "updated"):
            # Defensive: callers should route deactivations to
            # :meth:`remove`. Surface mis-routing rather than silently
            # accepting it.
            raise ValueError(
                f"ActorCache.upsert: refusing action={event.action!r}; "
                f"use remove() for deactivations."
            )
        async with self._lock:
            self._by_id[event.actor_id] = event

    async def remove(self, actor_id: str) -> bool:
        """Drop an actor. Returns True if the actor was cached;
        False if the deactivation was a no-op (already gone)."""
        async with self._lock:
            return self._by_id.pop(actor_id, None) is not None

    async def get(self, actor_id: str) -> ActorEnrollmentEvent | None:
        async with self._lock:
            return self._by_id.get(actor_id)

    async def snapshot(self) -> tuple[ActorEnrollmentEvent, ...]:
        """All currently-cached actors. Returns a sorted-by-id
        tuple so callers can compare snapshots deterministically."""
        async with self._lock:
            return tuple(sorted(self._by_id.values(), key=lambda e: e.actor_id))

    async def size(self) -> int:
        async with self._lock:
            return len(self._by_id)
