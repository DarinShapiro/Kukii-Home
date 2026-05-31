"""Webhook receiver for Agent DVR events.

Agent DVR fires HTTP webhooks on motion, AI detection, alarm trip, etc.
This module exposes a minimal aiohttp endpoint that ingests those webhooks,
normalizes them to :class:`MotionEvent`, and forwards onto the adapter's
internal queue.

For Epic 3 we ship the normalization + an asyncio.Queue-based bridge. The
HTTP server is wired up by the adapter at start() time.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from kukiihome_shared.adapter.base import MotionEvent

logger = structlog.get_logger(__name__)


class AgentDVRWebhookReceiver:
    """Bridges incoming Agent DVR webhook POSTs to a MotionEvent queue."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[MotionEvent] = asyncio.Queue()

    @property
    def queue(self) -> asyncio.Queue[MotionEvent]:
        return self._queue

    async def handle_payload(self, payload: dict[str, Any]) -> None:
        """Accept a webhook payload, normalize, enqueue."""
        try:
            event = self._normalize(payload)
        except Exception:
            logger.exception("agent_dvr_webhook.normalize_failed", payload=payload)
            return
        await self._queue.put(event)
        logger.debug(
            "agent_dvr_webhook.ingested",
            camera_id=event.camera_id,
            event_type=event.event_type,
        )

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> MotionEvent:
        """Convert an Agent DVR webhook payload to a MotionEvent.

        Webhook shape (typical):
            {
              "Type": "Alert", "Name": "Front Door", "ObjectId": 1,
              "Description": "Person detected", "Time": "2026-05-25T14:30:22Z"
            }
        """
        camera_id = str(payload.get("ObjectId") or payload.get("Name") or "unknown")
        event_type = _classify(payload.get("Description", ""))
        ts_str = payload.get("Time")
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else datetime.now(UTC)
        return MotionEvent(
            camera_id=camera_id,
            timestamp=ts,
            event_type=event_type,
            confidence=None,  # Agent DVR webhooks rarely carry confidence
            raw=payload,
        )


def _classify(description: str) -> str:
    desc = description.lower()
    if "person" in desc:
        return "person"
    if "vehicle" in desc or "car" in desc:
        return "vehicle"
    if "animal" in desc or "dog" in desc or "cat" in desc:
        return "animal"
    return "motion"
