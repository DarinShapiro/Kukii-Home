"""Semantic area → entity-group resolution (Epic 9 #144).

SentiHome talks about areas ("front_door", "backyard", "perimeter") but
HA addresses entities by ID (``light.porch_front``, ``light.porch_side``,
...). The resolver answers ``which lights are in <area>?`` /
``which cameras / locks / media_players / ...?`` so the dispatcher can
issue actions in semantic terms.

Lookup hierarchy:
1. Explicit overrides declared in `topology.notify` or per-area config
2. HA area_registry: entities whose area_id matches the requested area
3. Heuristic: entity_id contains the area slug or area_name word
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentihome_ha_agent.client import HAState


# Entity domains we care about per area.
RESOURCE_DOMAINS: dict[str, tuple[str, ...]] = {
    "light": ("light",),
    "switch": ("switch",),
    "lock": ("lock",),
    "media_player": ("media_player",),
    "camera": ("camera",),
    "ptz": ("camera",),  # PTZ presets are camera entities with services
    "siren": ("siren",),
}


@dataclass
class AreaResources:
    """Resources available in an HA area, keyed by resource kind."""

    area: str
    by_kind: dict[str, list[str]] = field(default_factory=dict)

    def get(self, kind: str) -> list[str]:
        return self.by_kind.get(kind, [])

    def as_dict(self) -> dict[str, list[str]]:
        return dict(self.by_kind)


@dataclass
class AreaRegistry:
    """Mapping area_id → list of entity_ids, populated from HA snapshot.

    HA's area_registry isn't directly exposed via REST; we approximate it
    by reading ``attributes.area_id`` on each entity (HA populates this
    when entities are assigned to areas via the UI). Entities without
    ``area_id`` fall back to entity_id-substring matching.
    """

    explicit_overrides: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    """`{area: {kind: [entity_id, ...]}}` from topology config."""

    def resolve(self, area: str, states: list[HAState]) -> AreaResources:
        # 1. Explicit overrides win outright.
        if area in self.explicit_overrides:
            by_kind = {k: list(v) for k, v in self.explicit_overrides[area].items()}
            return AreaResources(area=area, by_kind=by_kind)

        # 2. HA area_id attribute (when present) is authoritative.
        by_kind: dict[str, list[str]] = defaultdict(list)
        matched_via_area_id: set[str] = set()
        for state in states:
            entity_area = (state.attributes or {}).get("area_id")
            if entity_area == area:
                kind = _kind_for_entity(state.entity_id)
                if kind:
                    by_kind[kind].append(state.entity_id)
                    matched_via_area_id.add(state.entity_id)

        # 3. Heuristic fallback: entity_id contains the area slug.
        area_token = area.lower().replace("_", "")
        for state in states:
            if state.entity_id in matched_via_area_id:
                continue
            eid = state.entity_id.lower().replace("_", "").replace(".", "")
            if area_token and area_token in eid:
                kind = _kind_for_entity(state.entity_id)
                if kind and state.entity_id not in by_kind[kind]:
                    by_kind[kind].append(state.entity_id)

        return AreaResources(area=area, by_kind=dict(by_kind))


def _kind_for_entity(entity_id: str) -> str | None:
    """Pick the SentiHome resource kind for an HA entity_id."""
    domain = entity_id.split(".", 1)[0]
    for kind, domains in RESOURCE_DOMAINS.items():
        if domain in domains:
            return kind
    return None
