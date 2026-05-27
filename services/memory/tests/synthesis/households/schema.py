"""Pydantic schema for the canonical household fixture.

Defines what an authored household YAML must contain. Loading via
:func:`load_household` validates structure + cross-references (e.g.
an area referenced by a camera must exist in the areas list).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    """Reject unknown fields — typos in fixture YAML should fail loudly."""

    model_config = ConfigDict(extra="forbid")


class Geography(_Strict):
    areas: list[str]
    """All area names. Must include every area referenced by a camera
    or by an actor's typical_patterns."""

    adjacencies: dict[str, list[str]] = Field(default_factory=dict)
    """Area → list of adjacent areas. Used by cross-camera correlation
    tests to score "person on adjacent cams within 10s." Asymmetric
    relationships allowed (street → driveway but not vice-versa)."""


class Camera(_Strict):
    id: str
    area: str
    fov_overlaps: list[str] = Field(default_factory=list)
    attention_mode: bool = False
    """Life-safety flag; triggers shorter sanity-check intervals for
    any dismissal policy whose scope includes this camera."""


class TypicalPattern(_Strict):
    kind: str
    cam: str | None = None
    time: str
    """Human-readable like '06:30 ± 5min'. Parsed by event generator."""
    weekdays: list[str] | None = None
    """Sub-set of [mon, tue, wed, thu, fri, sat, sun] or aliases
    like 'mon-fri', 'mon-sat'. None = every day."""
    route: list[str] | None = None
    """Sequence of areas the actor transits through (e.g.
    [front_porch, driveway, street] for departure)."""


class Resident(_Strict):
    id: str
    role: Literal["resident", "resident_minor"]
    age: int | None = None
    access_profile: Literal["full", "partial", "supervised"]
    typical_patterns: list[TypicalPattern] = Field(default_factory=list)


class KnownVisitor(_Strict):
    id: str
    access: str
    """Free-form access description (e.g. 'front_porch_morning'). Used
    by VLM context but not enforced by schema."""
    typical: str
    """Free-form recurrence description (e.g. '06:30 ± 5min, MWF')."""


class KnownVehicle(_Strict):
    id: str
    owner: str
    """Must reference a resident or visitor id."""
    plate: str
    color: str
    type: str


class KnownPet(_Strict):
    id: str
    owner: str
    species: Literal["dog", "cat", "bird", "other"]
    breed: str | None = None
    color: str | None = None
    indoor_outdoor: Literal["indoor", "outdoor", "indoor_outdoor"] = "indoor_outdoor"


class AmbientPattern(_Strict):
    kind: str
    cams: list[str] | Literal["all"]
    rate: str | None = None
    """e.g. '0.4/hr'. None when the pattern is time-scheduled."""
    time: str | None = None
    """e.g. '07:30 ± 8min, mon-fri, school_year'."""
    daylight_only: bool = False
    wind_threshold_mph: float | None = None
    false_motion_rate: str | None = None


class Household(_Strict):
    name: str
    description: str
    geography: Geography
    cameras: list[Camera]
    residents: list[Resident]
    known_visitors: list[KnownVisitor]
    known_vehicles: list[KnownVehicle]
    known_pets: list[KnownPet]
    ambient_patterns: list[AmbientPattern] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_cross_refs(self) -> Household:
        # Camera areas must exist.
        area_set = set(self.geography.areas)
        for cam in self.cameras:
            if cam.area not in area_set:
                raise ValueError(
                    f"Camera {cam.id!r} references unknown area {cam.area!r}; "
                    f"known areas: {sorted(area_set)}"
                )

        # Camera fov_overlaps must reference existing cameras.
        cam_ids = {c.id for c in self.cameras}
        for cam in self.cameras:
            for overlap in cam.fov_overlaps:
                if overlap not in cam_ids:
                    raise ValueError(
                        f"Camera {cam.id!r} has fov_overlap {overlap!r} that "
                        f"isn't a known camera (known: {sorted(cam_ids)})"
                    )

        # Vehicles + pets must have valid owners.
        actor_ids = {r.id for r in self.residents} | {v.id for v in self.known_visitors}
        for veh in self.known_vehicles:
            if veh.owner not in actor_ids:
                raise ValueError(f"Vehicle {veh.id!r} has unknown owner {veh.owner!r}")
        for pet in self.known_pets:
            if pet.owner not in actor_ids:
                raise ValueError(f"Pet {pet.id!r} has unknown owner {pet.owner!r}")

        # Adjacencies should reference declared areas (allowing virtual
        # areas like 'street' that aren't in the camera grid).
        for area, adj_list in self.geography.adjacencies.items():
            if area not in area_set:
                raise ValueError(f"Adjacency declares unknown source area {area!r}")
            # Adjacent areas are allowed to be "virtual" (street, etc.)
            # so we don't strict-check them.
            _ = adj_list

        return self


def load_household(path: str | Path) -> Household:
    """Load + validate a household YAML fixture.

    Raises pydantic ValidationError on any schema or cross-reference
    failure — fixture authoring errors are caught loudly at load time
    rather than producing confusing test failures later.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Household fixture not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return Household.model_validate(raw)
