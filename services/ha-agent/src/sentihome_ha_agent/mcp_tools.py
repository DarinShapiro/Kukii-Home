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


@dataclass
class HACameraDiscovery:
    """Result of scanning HA for cameras + motion sensors."""

    cameras: list[HACameraEntity]
    unmatched_motion_sensors: list[str] = field(default_factory=list)
    """Motion-like binary_sensor.* entities the heuristic couldn't pair
    with any camera. User can manually wire them into adapter config."""


@dataclass
class HACameraEntity:
    """An HA camera the user could point a SentiHome adapter at."""

    camera_entity: str
    """e.g. ``camera.pool_cam``."""
    friendly_name: str | None
    state: str
    """``idle`` / ``recording`` / ``streaming`` etc."""
    motion_candidates: list[str] = field(default_factory=list)
    """``binary_sensor.*`` entities heuristically matched as motion / AI
    triggers for this camera (substring match on the camera's id-suffix
    + ``motion`` / ``person`` / ``vehicle`` / ``animal`` keywords)."""


_MOTION_KEYWORDS = (
    "motion",
    "person",
    "vehicle",
    "animal",
    "package",
    "pet",
    "occupancy",
    "intrusion",
)

# Tokens we strip when computing the "device tokens" of a camera entity.
# Dahua / ONVIF / Reolink commonly create camera entities with stream-name
# suffixes (camera.dahua_pool_cam_main, .._sub, .._profile000_mainstream)
# but the corresponding motion binary_sensor sits at the device level
# without those suffixes (binary_sensor.dahua_pool_motion_alarm). So we
# strip the suffixes from both sides before token overlap.
_STREAM_STOP_TOKENS = frozenset(
    {
        "camera",
        "cam",
        "stream",
        "mainstream",
        "substream",
        "main",
        "sub",
        "hd",
        "sd",
        "profile",
        "profile000",
        "profile001",
        "profile002",
        "profile003",
        "high",
        "low",
        # Reolink stream names: their integration creates entities
        # like camera.<device>_fluent (sub stream) + camera.<device>_clear
        # (main). Treating these as device tokens splits one physical
        # camera into two "devices" in :func:`discovery.group_by_device`,
        # so strip them at the same level as Dahua's _main / _sub.
        "fluent",
        "clear",
        "alarm",
        "alert",
        "alerts",
        "sensor",
        "binary",
        "detected",
        "detection",
    }
)


def _meaningful_tokens(slug: str) -> set[str]:
    """Tokenize an entity slug into device-identifying tokens.

    Drops stream-name suffixes (main, sub, mainstream, profile000…),
    pure numerics, motion keywords (so we don't accidentally use 'motion'
    itself as a matching token), and the entity-kind words.
    """
    out: set[str] = set()
    for raw in slug.split("_"):
        if not raw or raw.isdigit():
            continue
        if raw in _STREAM_STOP_TOKENS:
            continue
        if raw in _MOTION_KEYWORDS:
            continue
        out.add(raw)
    return out


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

    async def find_motion_switches(self, camera_entity: str) -> list[dict[str, str]]:
        """Return HA ``switch.*motion*`` entities that belong to a camera.

        Uses the same token-overlap heuristic as
        :meth:`list_ha_cameras` motion matching: a switch is a match
        when its slug shares at least one meaningful token with the
        camera slug AND its name contains ``motion`` or ``detection``.

        Returned list of ``{entity_id, friendly_name, state}`` is
        sorted alphabetically for deterministic UI rendering. State
        is the live state ("on" / "off" / "unavailable" / etc.).

        Used by v0.3.16's per-device card to surface a Turn-on button
        when the camera's HA motion-detection switch is off (common
        misconfig — sensors don't fire if the parent switch is off).
        """
        slug = camera_entity.removeprefix("camera.")
        cam_tokens = _meaningful_tokens(slug)
        if not cam_tokens:
            return []
        states = await self._client.get_states()
        matches: list[dict[str, str]] = []
        for s in states:
            if not s.entity_id.startswith("switch."):
                continue
            eid_lower = s.entity_id.lower()
            if "motion" not in eid_lower and "detection" not in eid_lower:
                continue
            switch_tokens = _meaningful_tokens(s.entity_id.removeprefix("switch."))
            if not (switch_tokens & cam_tokens):
                continue
            matches.append(
                {
                    "entity_id": s.entity_id,
                    "friendly_name": s.attributes.get("friendly_name", "") or s.entity_id,
                    "state": s.state,
                }
            )
        matches.sort(key=lambda m: m["entity_id"])
        return matches

    async def list_notify_services(self) -> list[str]:
        """Return every ``notify.*`` service HA exposes, sorted.

        Powers the Notifications card on the Web UI: the user picks
        from this list via checkboxes (no typing service names).

        On any HA error, returns an empty list — the UI shows
        "No notify services available" and falls back gracefully.
        """
        try:
            services = await self._client.list_services()
        except Exception:
            return []
        notify_block = next(
            (s for s in services if s.get("domain") == "notify"),
            None,
        )
        if notify_block is None:
            return []
        names = notify_block.get("services", {})
        if not isinstance(names, dict):
            return []
        return sorted(f"notify.{n}" for n in names)

    async def list_ha_cameras(self) -> list[HACameraEntity]:
        """Return every ``camera.*`` entity HA has, with heuristically-matched
        motion / AI binary sensors.

        Matching uses token overlap (not prefix): the camera slug and the
        motion-sensor slug are each broken into ``_``-delimited tokens, stream
        suffixes (main / sub / profile000 / etc.) and entity-kind words
        (camera / sensor / detected / etc.) are dropped, and a sensor is
        paired with the camera when:
          * the sensor slug contains a motion keyword, AND
          * at least one meaningful token overlaps with the camera slug.

        Why this is loose:
          camera.dahua_pool_cam_main → meaningful tokens {dahua, pool}
          binary_sensor.dahua_pool_motion_alarm → meaningful tokens {dahua, pool}
          overlap = {dahua, pool} → match
        With the old startswith() heuristic this never matched because
        ``dahua_pool_motion_alarm`` doesn't start with ``dahua_pool_cam_main``.

        Unmatched motion sensors land on the discovery payload via
        :meth:`discover_ha_cameras` (which wraps this and adds them).
        """
        discovery = await self.discover_ha_cameras()
        return discovery.cameras

    async def discover_ha_cameras(self) -> HACameraDiscovery:
        """Cameras + unmatched motion sensors, in one pass.

        The unmatched list is what the Web UI uses to show "we saw these
        motion-like sensors but couldn't auto-pair them" — useful when
        the user has motion entities under non-obvious names that need
        manual configuration.
        """
        states = await self._client.get_states()
        cameras: list[HAState] = []
        binary_sensors: list[HAState] = []
        for s in states:
            domain = s.entity_id.split(".", 1)[0]
            if domain == "camera":
                cameras.append(s)
            elif domain == "binary_sensor":
                binary_sensors.append(s)

        # Pre-compute slug + tokens once per binary_sensor.
        motion_bs: list[tuple[HAState, str, set[str]]] = []
        for bs in binary_sensors:
            bs_slug = bs.entity_id.split(".", 1)[1]
            if not any(kw in bs_slug for kw in _MOTION_KEYWORDS):
                continue
            motion_bs.append((bs, bs_slug, _meaningful_tokens(bs_slug)))

        matched_bs_ids: set[str] = set()
        cam_entries: list[HACameraEntity] = []
        for cam in cameras:
            cam_slug = cam.entity_id.split(".", 1)[1]
            cam_tokens = _meaningful_tokens(cam_slug)
            motion: list[str] = []
            for bs, _bs_slug, bs_tokens in motion_bs:
                if not cam_tokens & bs_tokens:
                    continue
                motion.append(bs.entity_id)
                matched_bs_ids.add(bs.entity_id)
            cam_entries.append(
                HACameraEntity(
                    camera_entity=cam.entity_id,
                    friendly_name=(cam.attributes or {}).get("friendly_name"),
                    state=cam.state,
                    motion_candidates=motion,
                )
            )

        unmatched = [bs.entity_id for bs, _, _ in motion_bs if bs.entity_id not in matched_bs_ids]
        return HACameraDiscovery(cameras=cam_entries, unmatched_motion_sensors=unmatched)

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
