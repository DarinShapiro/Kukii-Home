"""Verify the canonical household fixture loads + validates cleanly.

If this test fails, the fixture YAML has either a schema error or a
broken cross-reference (a camera pointing at an undeclared area,
a vehicle owned by an unknown actor, etc.). Pydantic raises with
a clear pointer to which field is wrong.
"""

from __future__ import annotations

from pathlib import Path

from synthesis.households.schema import Household, load_household

CANONICAL_YAML = Path(__file__).parent / "synthesis" / "households" / "canonical.yaml"


def test_canonical_household_loads():
    """The canonical household fixture must always be valid."""
    household = load_household(CANONICAL_YAML)
    assert isinstance(household, Household)
    assert household.name == "canonical"


def test_canonical_has_all_expected_cameras():
    h = load_household(CANONICAL_YAML)
    cam_ids = {c.id for c in h.cameras}
    expected = {
        "front_south_cam",
        "driveway_cam",
        "garage_cam",
        "backyard_cam",
        "pool_cam",
    }
    assert expected.issubset(cam_ids)


def test_canonical_pool_cam_has_attention_mode():
    """Life-safety areas must carry the AttentionMode flag."""
    h = load_household(CANONICAL_YAML)
    pool = next(c for c in h.cameras if c.id == "pool_cam")
    assert pool.attention_mode is True


def test_canonical_other_cams_dont_have_attention_mode():
    """Default-off — only explicitly-flagged cameras should have it."""
    h = load_household(CANONICAL_YAML)
    for cam in h.cameras:
        if cam.id != "pool_cam":
            assert cam.attention_mode is False, (
                f"camera {cam.id} should not have attention_mode set by default"
            )


def test_canonical_actors():
    h = load_household(CANONICAL_YAML)
    resident_ids = {r.id for r in h.residents}
    assert {"alice", "bob", "charlie", "diana"} == resident_ids


def test_canonical_pets():
    h = load_household(CANONICAL_YAML)
    pet_ids = {p.id for p in h.known_pets}
    assert {"rex", "whiskey"} == pet_ids


def test_canonical_vehicle_owners_resolve():
    """The model_validator should have already caught any unresolved
    vehicle owner. This test asserts the canonical fixture passes that
    validation."""
    h = load_household(CANONICAL_YAML)
    actor_ids = {r.id for r in h.residents} | {v.id for v in h.known_visitors}
    for veh in h.known_vehicles:
        assert veh.owner in actor_ids
