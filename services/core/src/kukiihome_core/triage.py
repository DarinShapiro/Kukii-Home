"""Triage worker — dedup, score, and route events to priority tiers.

The triage worker is the entry point of the hot path. It consumes
``TriggerEvent``s from any NVR adapter (via ``vlm.*`` subjects), decides:

1. Is this a dup of a recent event? (skip)
2. Is it tier-1 safety (smoke, CO, flood)? (bypass to ``sensor.bypass``)
3. What priority tier? (``vlm.urgent``, ``vlm.normal``, ``vlm.background``)
4. Should the priority be downgraded due to backpressure? (load shedding)

This module implements §03 (event bus + load shedding) and §06 (triage scoring).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from kukiihome_shared.bus import Bus
    from kukiihome_shared.generated.events.trigger_event import TriggerEvent

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Priority tiers
# ─────────────────────────────────────────────────────────────────────


class Tier:
    """Routing tiers (NATS subjects)."""

    SENSOR_BYPASS = "sensor.bypass"  # Tier-1 safety, no VLM
    VLM_URGENT = "vlm.urgent"  # alert criticality
    VLM_NORMAL = "vlm.normal"  # standard
    VLM_BACKGROUND = "vlm.background"  # low priority

    # Order matters: downgrade direction in load shedding
    DOWNGRADE_ORDER: tuple[str, ...] = (VLM_URGENT, VLM_NORMAL, VLM_BACKGROUND)


# ─────────────────────────────────────────────────────────────────────
# Dedup
# ─────────────────────────────────────────────────────────────────────


@dataclass
class DedupCache:
    """Sliding-window dedup over (camera_id, event_type) signatures.

    Two events with the same signature within ``window_seconds`` are considered
    duplicates and only the first is forwarded.
    """

    window_seconds: float = 5.0
    max_entries: int = 1000
    _entries: deque[tuple[str, float]] = field(default_factory=deque)
    _index: dict[str, float] = field(default_factory=dict)

    def is_dup(self, signature: str, *, now: float | None = None) -> bool:
        """Returns True if this signature was seen within the window."""
        now = now if now is not None else time.monotonic()
        self._evict(now)
        if signature in self._index:
            return True
        self._index[signature] = now
        self._entries.append((signature, now))
        if len(self._entries) > self.max_entries:
            sig, _ = self._entries.popleft()
            self._index.pop(sig, None)
        return False

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._entries and self._entries[0][1] < cutoff:
            sig, _ = self._entries.popleft()
            self._index.pop(sig, None)


# ─────────────────────────────────────────────────────────────────────
# Backpressure / load shedding
# ─────────────────────────────────────────────────────────────────────


@dataclass
class BackpressureSignal:
    """Tracks queue depth per tier so we can downgrade priority under load.

    Per §03: when ``vlm.urgent`` queue depth exceeds ``urgent_threshold``,
    new urgents are downgraded to ``vlm.normal``; same logic cascades to
    background. Background events past their threshold are dropped entirely
    rather than queued indefinitely.
    """

    urgent_threshold: int = 10
    normal_threshold: int = 50
    background_threshold: int = 200
    _depths: dict[str, int] = field(default_factory=lambda: dict.fromkeys(Tier.DOWNGRADE_ORDER, 0))

    def observe(self, tier: str, depth: int) -> None:
        """Record current queue depth for a tier."""
        self._depths[tier] = depth

    def shed(self, requested: str) -> str | None:
        """Apply load shedding policy.

        Returns the (possibly downgraded) tier the event should go to,
        or ``None`` if the event should be dropped entirely.
        """
        if requested == Tier.SENSOR_BYPASS:
            return Tier.SENSOR_BYPASS  # safety never gets shed

        thresholds = {
            Tier.VLM_URGENT: self.urgent_threshold,
            Tier.VLM_NORMAL: self.normal_threshold,
            Tier.VLM_BACKGROUND: self.background_threshold,
        }

        # Walk the downgrade chain from requested tier.
        chain = list(Tier.DOWNGRADE_ORDER)
        try:
            start = chain.index(requested)
        except ValueError:
            return requested  # unknown tier, pass through

        for tier in chain[start:]:
            if self._depths.get(tier, 0) < thresholds[tier]:
                return tier

        # Even background is full — drop.
        return None


# ─────────────────────────────────────────────────────────────────────
# Scoring (priority decision)
# ─────────────────────────────────────────────────────────────────────


# Tier-1 safety event types bypass the VLM queue entirely.
# These come from HA-polled sensors (smoke, CO, flood, etc.) per §03.
SAFETY_BYPASS_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "smoke_alarm",
        "co_alarm",
        "flood_alarm",
        "fire_alarm",
    }
)


# Event types that are inherently urgent (person at door, doorbell ring).
URGENT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "doorbell",
        "package",  # may indicate theft
    }
)


def score_event(event: TriggerEvent) -> str:
    """Determine the initial priority tier for an event.

    This is the pre-load-shedding tier — backpressure may downgrade it.

    Args:
        event: A validated TriggerEvent.

    Returns:
        One of the ``Tier.*`` constants.
    """
    # event_type may be the enum or a raw string depending on source.
    etype = event.event_type.value if event.event_type else None

    if etype in SAFETY_BYPASS_EVENT_TYPES:
        return Tier.SENSOR_BYPASS

    if etype in URGENT_EVENT_TYPES:
        return Tier.VLM_URGENT

    # On-camera AI with high confidence + relevant label → urgent
    opinion = event.on_camera_ai_opinion
    if opinion is not None:
        conf = opinion.confidence if opinion.confidence is not None else 0.0
        label = (opinion.label or "").lower()
        if conf >= 0.85 and label in {"person", "vehicle"}:
            return Tier.VLM_URGENT

    if etype in {"person", "vehicle"}:
        return Tier.VLM_NORMAL

    return Tier.VLM_BACKGROUND


# ─────────────────────────────────────────────────────────────────────
# Triage worker
# ─────────────────────────────────────────────────────────────────────


def _signature(event: TriggerEvent) -> str:
    """Compute a dedup signature for an event."""
    parts = [
        event.camera_id,
        event.event_type.value if event.event_type else "none",
    ]
    if event.on_camera_ai_opinion is not None:
        parts.append(event.on_camera_ai_opinion.label or "")
    return hashlib.md5("|".join(parts).encode(), usedforsecurity=False).hexdigest()


class Triage:
    """Triage worker: subscribes to ingress, routes to priority tiers.

    Run as a long-lived async service::

        bus = ...
        triage = Triage(bus=bus)
        await triage.run()
    """

    def __init__(
        self,
        *,
        bus: Bus,
        dedup_window_seconds: float = 5.0,
        backpressure: BackpressureSignal | None = None,
    ) -> None:
        self._bus = bus
        self._dedup = DedupCache(window_seconds=dedup_window_seconds)
        self._backpressure = backpressure or BackpressureSignal()

    @property
    def backpressure(self) -> BackpressureSignal:
        return self._backpressure

    async def handle(self, event: TriggerEvent) -> None:
        """Process one trigger event: dedup → score → shed → publish."""
        sig = _signature(event)
        if self._dedup.is_dup(sig):
            logger.debug("triage.deduped", event_id=event.event_id, signature=sig[:8])
            return

        requested_tier = score_event(event)
        shed_tier = self._backpressure.shed(requested_tier)

        if shed_tier is None:
            logger.warning(
                "triage.dropped_overload",
                event_id=event.event_id,
                requested_tier=requested_tier,
            )
            return

        if shed_tier != requested_tier:
            logger.info(
                "triage.downgraded",
                event_id=event.event_id,
                from_tier=requested_tier,
                to_tier=shed_tier,
            )

        await self._bus.publish(shed_tier, event)

    async def run(self) -> None:
        """Subscribe + consume forever. Cancel the surrounding task to stop."""
        from kukiihome_shared.generated.events.trigger_event import TriggerEvent as _TE

        await self._bus.subscribe(
            stream="EVENTS",
            consumer="triage",
            model=_TE,
            handler=self.handle,
        )
        # Block forever — Bus subscriptions are background tasks; this just keeps
        # the service alive.
        await asyncio.Event().wait()
