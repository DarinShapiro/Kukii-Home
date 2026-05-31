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

from kukiihome_shared.preprocessor import ActorEnrollmentEvent

# Per-modality + metadata fields that carry forward across partial
# enrollment events. Face / body / pet are enrolled independently
# (separate scripts, separate cameras, different times), so a body
# enrollment must NOT wipe a previously-cached face embedding. An
# incoming non-None value overlays; a None leaves the cached value
# intact. NOTE: this is delta-merge semantics — a field cannot be
# *cleared* by sending None (deactivation drops the whole actor via
# remove()). The future model is full per-actor snapshot events from
# the memory service, where plain overwrite is correct again.
_MERGE_FIELDS = (
    "name",
    "role",
    "access_profile",
    "face_embedding",
    "body_embedding",
    "body_shape_embedding",
    "gait_embedding",
    "pet_dinov2_centroid",
    "plate_text",
)


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
        """Apply an ``enrolled`` or ``updated`` event, MERGING per
        modality.

        Face, body, and pet are enrolled by independent publishers, so
        each event typically carries only one embedding. Merging means
        a body enrollment overlays the body embedding while preserving
        an already-cached face embedding (and vice versa) — letting one
        actor accumulate face + body + pet across separate events.
        Incoming non-None fields win; None falls back to the cached
        value. First-seen actors are stored as-is.
        """
        if event.action not in ("enrolled", "updated"):
            # Defensive: callers should route deactivations to
            # :meth:`remove`. Surface mis-routing rather than silently
            # accepting it.
            raise ValueError(
                f"ActorCache.upsert: refusing action={event.action!r}; "
                f"use remove() for deactivations."
            )
        async with self._lock:
            existing = self._by_id.get(event.actor_id)
            if existing is None:
                self._by_id[event.actor_id] = event
                return
            updates = {f: getattr(event, f) for f in _MERGE_FIELDS if getattr(event, f) is not None}
            updates["action"] = event.action
            self._by_id[event.actor_id] = existing.model_copy(update=updates)

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
