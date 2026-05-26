"""HA-agent MCP tools (Epic 9 #136-#142).

The tool surface SentiHome services call to read + write HA. Modeled after
``docs/architecture/07-tool-layer-mcp.md``.

Read tools (auto-allowed):
- ``ha.get_snapshot``  — full entity state cache
- ``ha.get_changes``   — entities changed since a given timestamp
- ``ha.get_area_resources`` — semantic area → resource lists
- ``ha.list_capabilities`` — what HA integrations are connected
- ``ha.get_calendar_events`` — upcoming calendar events (skeleton)
- ``ha.query``         — NL synthesis over HA state (skeleton)

Write tools:
- Auto-allowed: ``ha.illuminate_area``, ``ha.darken_area``, ``ha.set_scene``
- Policy-gated: ``ha.lock``, ``ha.unlock``
- General-purpose: ``ha.call_service`` (policy gate applied by caller)

Policy enforcement uses the same :class:`PolicyGate` from
``services/core/dispatch.py`` so SentiHome's autonomous-action policy is
consistent across the rule engine and the ha-agent surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from sentihome_ha_agent.area_resolver import AreaRegistry, AreaResources
from sentihome_ha_agent.client import HAClient

if TYPE_CHECKING:
    from sentihome_ha_agent.client import HAState

logger = structlog.get_logger(__name__)


# Domains we surface as "capabilities" so SentiHome knows what's hooked up.
CAPABILITY_DOMAINS = (
    "light",
    "switch",
    "lock",
    "camera",
    "media_player",
    "person",
    "binary_sensor",
    "siren",
    "alarm_control_panel",
    "calendar",
    "weather",
    "device_tracker",
    "tts",
)


@dataclass
class ChangedEntity:
    entity_id: str
    state: str
    last_changed: str | None


@dataclass
class CapabilitySummary:
    domain: str
    entity_count: int
    sample_entities: list[str] = field(default_factory=list)


class HATools:
    """The MCP-shaped surface SentiHome services interact with."""

    def __init__(
        self,
        client: HAClient,
        *,
        area_registry: AreaRegistry | None = None,
    ) -> None:
        self._client = client
        self._areas = area_registry or AreaRegistry()

    # ─── read tools ────────────────────────────────────────────────

    async def get_snapshot(self) -> list[HAState]:
        """Return all entity states (cache-first; fetches once at startup)."""
        return await self._client.get_states()

    async def get_changes(self, since: datetime) -> list[ChangedEntity]:
        """Entities whose ``last_changed`` is at or after ``since``."""
        states = await self._client.get_states()
        since_iso = since.isoformat()
        return [
            ChangedEntity(
                entity_id=s.entity_id,
                state=s.state,
                last_changed=s.last_changed,
            )
            for s in states
            if s.last_changed and s.last_changed >= since_iso
        ]

    async def get_area_resources(self, area: str) -> AreaResources:
        """Resources available in ``area``, keyed by kind."""
        states = await self._client.get_states()
        return self._areas.resolve(area, states)

    async def list_capabilities(self) -> list[CapabilitySummary]:
        """Histogram of entity counts per capability domain."""
        states = await self._client.get_states()
        buckets: dict[str, list[str]] = {d: [] for d in CAPABILITY_DOMAINS}
        for state in states:
            domain = state.entity_id.split(".", 1)[0]
            if domain in buckets:
                buckets[domain].append(state.entity_id)
        return [
            CapabilitySummary(
                domain=d,
                entity_count=len(entities),
                sample_entities=entities[:5],
            )
            for d, entities in buckets.items()
            if entities
        ]

    async def get_calendar_events(
        self, calendar_entity: str, *, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Calendar API integration. Returns raw HA calendar events.

        v1 ships the REST passthrough; richer parsing lands when the
        rule-engine learns to consume calendar context (Epic 11/13).
        """
        # HA exposes /api/calendars/<entity>?start=ISO&end=ISO
        resp = await self._client._http.get(
            f"/api/calendars/{calendar_entity}",
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        body = resp.json()
        return body if isinstance(body, list) else []

    async def query(self, _natural_language: str) -> dict[str, Any]:
        """NL synthesis over HA state. Stub — wires to vlm-router in Epic 11."""
        # The real implementation will:
        # 1. Take a snapshot
        # 2. Build a compact state digest
        # 3. Pass user query + digest to vlm-router
        # 4. Return the structured answer
        return {"status": "not_implemented", "reason": "wires_in_epic_11"}

    # ─── write tools ───────────────────────────────────────────────

    async def illuminate_area(self, area: str, *, brightness: int | None = None) -> dict[str, Any]:
        """Turn on every light in ``area``. Auto-allowed action."""
        resources = await self.get_area_resources(area)
        lights = resources.get("light")
        if not lights:
            return {"called": [], "skipped_reason": "no_lights_in_area"}
        data: dict[str, Any] = {}
        if brightness is not None:
            data["brightness"] = brightness
        await self._client.call_service("light", "turn_on", entity_id=lights, data=data)
        return {"called": lights}

    async def darken_area(self, area: str) -> dict[str, Any]:
        """Turn off every light in ``area``. Auto-allowed action."""
        resources = await self.get_area_resources(area)
        lights = resources.get("light")
        if not lights:
            return {"called": [], "skipped_reason": "no_lights_in_area"}
        await self._client.call_service("light", "turn_off", entity_id=lights)
        return {"called": lights}

    async def set_scene(self, scene_entity: str) -> dict[str, Any]:
        """Activate an HA scene. Auto-allowed."""
        await self._client.call_service("scene", "turn_on", entity_id=scene_entity)
        return {"called": [scene_entity]}

    async def lock(self, entity_id: str) -> dict[str, Any]:
        """Lock a door. Policy-gated — caller must have approval."""
        await self._client.call_service("lock", "lock", entity_id=entity_id)
        return {"called": [entity_id]}

    async def unlock(self, entity_id: str) -> dict[str, Any]:
        """Unlock a door. Policy-gated — caller must have approval."""
        await self._client.call_service("lock", "unlock", entity_id=entity_id)
        return {"called": [entity_id]}

    async def call_service(
        self,
        domain: str,
        service: str,
        *,
        entity_id: str | list[str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """General-purpose passthrough. Policy enforcement is the caller's job
        (see :class:`sentihome_core.dispatch.PolicyGate`)."""
        return await self._client.call_service(domain, service, entity_id=entity_id, data=data)
