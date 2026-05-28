"""Subscribes to actor-enrollment events; keeps the ActorCache fresh.

Three subjects flow IN to the preprocessor:

* :data:`~sentihome_shared.preprocessor.SUBJECT_ACTOR_ENROLLED`
* :data:`~sentihome_shared.preprocessor.SUBJECT_ACTOR_UPDATED`
* :data:`~sentihome_shared.preprocessor.SUBJECT_ACTOR_DEACTIVATED`

This module wires a NATS subscription per subject and routes each
message to the appropriate :class:`~ActorCache` mutation. JetStream
replay on reconnect would let us rebuild the cache after a restart;
the skeleton uses core NATS for simplicity, JetStream comes in
10.2 when we wire to the production memory service.
"""

from __future__ import annotations

import structlog
from nats.aio.client import Client as NATS
from nats.aio.msg import Msg
from sentihome_shared.preprocessor import (
    SUBJECT_ACTOR_DEACTIVATED,
    SUBJECT_ACTOR_ENROLLED,
    SUBJECT_ACTOR_UPDATED,
    ActorEnrollmentEvent,
)

from sentihome_preprocessor.state import ActorCache

logger = structlog.get_logger(__name__)


class ActorEnrollmentSubscriber:
    """Subscribes to all three actor-event subjects and mutates the
    cache accordingly."""

    def __init__(self, nats_url: str, cache: ActorCache) -> None:
        self._url = nats_url
        self._cache = cache
        self._nc: NATS | None = None

    async def connect(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            return
        nc = NATS()
        await nc.connect(servers=[self._url])
        self._nc = nc

        await nc.subscribe(SUBJECT_ACTOR_ENROLLED, cb=self._on_enroll_or_update)
        await nc.subscribe(SUBJECT_ACTOR_UPDATED, cb=self._on_enroll_or_update)
        await nc.subscribe(SUBJECT_ACTOR_DEACTIVATED, cb=self._on_deactivate)

        logger.info(
            "preprocessor.subscriber.connected",
            url=self._url,
            subjects=[
                SUBJECT_ACTOR_ENROLLED,
                SUBJECT_ACTOR_UPDATED,
                SUBJECT_ACTOR_DEACTIVATED,
            ],
        )

    async def close(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            await self._nc.drain()
        self._nc = None

    # ─── handlers ───────────────────────────────────────────────────

    async def _on_enroll_or_update(self, msg: Msg) -> None:
        try:
            event = ActorEnrollmentEvent.model_validate_json(msg.data)
        except Exception as e:
            logger.warning(
                "preprocessor.subscriber.bad_payload",
                subject=msg.subject,
                error=str(e),
            )
            return

        if event.action == "deactivated":
            # Mis-routed but we can still do the right thing.
            await self._cache.remove(event.actor_id)
            return

        await self._cache.upsert(event)
        logger.info(
            "preprocessor.actor.cached",
            actor_id=event.actor_id,
            action=event.action,
            has_face=event.face_embedding is not None,
        )

    async def _on_deactivate(self, msg: Msg) -> None:
        try:
            event = ActorEnrollmentEvent.model_validate_json(msg.data)
        except Exception as e:
            logger.warning(
                "preprocessor.subscriber.bad_payload",
                subject=msg.subject,
                error=str(e),
            )
            return

        removed = await self._cache.remove(event.actor_id)
        logger.info(
            "preprocessor.actor.deactivated",
            actor_id=event.actor_id,
            was_cached=removed,
        )
