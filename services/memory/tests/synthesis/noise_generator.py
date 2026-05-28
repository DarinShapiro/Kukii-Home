"""Synthetic ambient-noise generator for scenarios.

Given a Household's ``ambient_patterns`` and a scenario's
``noise_profile``, samples a deterministic, seeded sequence of noise
events — squirrels in the backyard, leaves on a windy day, the
school bus rolling past — that get mixed into the runner's timeline
alongside declared and recurring events.

Properties the harness depends on:

* **Deterministic given (seed, profile, duration, household)**. Same
  inputs → same noise events. Lets scenarios assert exact counts
  rather than fuzzy ranges.
* **Noise events carry no ``vlm_response``**, so the runner records
  them as observations but never invokes the VLM oracle on them.
  This encodes the architectural premise: routine ambient motion
  doesn't burn VLM budget.
* **Noise events carry no ``matched_actor_ids``**, so no KnownActor
  citation can possibly form from a noise event — only declared
  events with explicit oracle responses create citations.

Phase 1B+ scope:

* Supports **rate-based** ambient patterns (``rate: 'X/hr'``,
  ``false_motion_rate: 'X/hr'``) with poisson sampling.
* Time-scheduled patterns (``time: '07:30 ± 8min, mon-fri'``) are
  deferred to Phase 2 — they need a richer time-spec parser and
  aren't needed for the noise-floor negative-property proof.
* The noise multiplier per profile is fixed: see ``_PROFILE_MULTIPLIER``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from synthesis.households.schema import AmbientPattern, Household
from synthesis.runner import ResolvedEvent
from synthesis.scenarios.schema import PreprocessorOutput, TruthLabel

# Maps an ambient_pattern.kind to the tag set its events carry. Anything
# not listed defaults to an empty tag set (a motion blip with no
# classified object — e.g. wind on leaves).
_PATTERN_TAGS: dict[str, list[str]] = {
    "squirrel": ["animal"],
    "rabbit": ["animal"],
    "cat": ["animal"],
    "dog": ["animal"],  # stray dog, not the household's known pet
    "school_bus": ["vehicle"],
    "trash_truck": ["vehicle"],
    "delivery_truck": ["vehicle"],
    "passing_vehicle": ["vehicle"],
    "leaves_wind": [],
    "shadow": [],
    "lighting_change": [],
}

# Scenario profile → multiplier applied to each pattern's rate. Lets
# tests pick a noise floor without re-authoring fixtures.
_PROFILE_MULTIPLIER: dict[str, float] = {
    "none": 0.0,
    "minimal": 0.2,
    "moderate": 0.6,
    "realistic": 1.0,
}

# Daylight window for ``daylight_only`` patterns, in seconds-into-day
# (UTC; canonical household lives in a tz-agnostic synthetic world).
_DAYLIGHT_START_S = 6 * 3600  # 06:00
_DAYLIGHT_END_S = 20 * 3600  # 20:00
_DAY_SECONDS = 86_400


@dataclass(frozen=True)
class NoiseStats:
    """Diagnostic counts the noise generator produces.

    The runner can include these in its result for tests that want to
    sanity-check 'noise actually fired' separately from 'no citations
    formed.'
    """

    events_generated: int
    patterns_used: int


def generate_noise_events(
    *,
    household: Household,
    noise_profile: str,
    duration_days: int,
    start_ts: float,
    seed: int,
) -> tuple[list[ResolvedEvent], NoiseStats]:
    """Produce noise events for the (household, profile, duration) combo.

    Returns the events plus a small stats record. Empty for
    ``noise_profile == "none"``.

    Determinism: the seed is augmented per-pattern so adding a new
    ambient pattern to a household doesn't reshuffle existing
    patterns' event timing.
    """
    multiplier = _PROFILE_MULTIPLIER.get(noise_profile)
    if multiplier is None:
        raise ValueError(
            f"Unknown noise_profile {noise_profile!r}; expected one of "
            f"{sorted(_PROFILE_MULTIPLIER)}"
        )
    if multiplier == 0.0:
        return [], NoiseStats(events_generated=0, patterns_used=0)

    all_camera_ids = [c.id for c in household.cameras]
    out: list[ResolvedEvent] = []
    patterns_used = 0

    for idx, pattern in enumerate(household.ambient_patterns):
        # Augment seed with pattern index so order changes don't
        # reshuffle prior patterns.
        pattern_rng = random.Random(seed * 1_000_003 + idx)

        rate_per_hour = _resolve_rate_per_hour(pattern)
        if rate_per_hour is None:
            # Time-scheduled pattern — Phase 2. Skip for now.
            continue
        effective_rate = rate_per_hour * multiplier
        if effective_rate <= 0:
            continue

        cameras = _expand_cameras(pattern, all_camera_ids)
        if not cameras:
            continue

        patterns_used += 1
        tag_set = _PATTERN_TAGS.get(pattern.kind, [])

        # Hour window for sampling. daylight_only → 14h; otherwise 24h.
        window_start_s, window_end_s = (
            (_DAYLIGHT_START_S, _DAYLIGHT_END_S) if pattern.daylight_only else (0, _DAY_SECONDS)
        )
        window_hours = (window_end_s - window_start_s) / 3600.0

        for day in range(1, duration_days + 1):
            # Per-camera, draw a poisson count of events for the day.
            # We use the per-camera rate (each camera sees the full
            # pattern rate; in real life squirrels are independent
            # across cameras).
            for camera in cameras:
                count = _poisson_sample(pattern_rng, effective_rate * window_hours)
                for _ in range(count):
                    sec_into_day = pattern_rng.uniform(window_start_s, window_end_s)
                    ts = start_ts + (day - 1) * _DAY_SECONDS + sec_into_day
                    out.append(
                        ResolvedEvent(
                            ts=ts,
                            day=day,
                            camera=camera,
                            preprocessor_output=PreprocessorOutput(
                                tag_set=list(tag_set),
                                matched_actor_ids=[],
                            ),
                            vlm_response=None,
                            truth=TruthLabel(
                                actor_id=None,
                                intent=f"noise_{pattern.kind}",
                                should_fire_alert=False,
                                expected_tier="tier_0",
                                notes=f"Ambient noise from pattern {pattern.kind!r}.",
                            ),
                        )
                    )

    return out, NoiseStats(events_generated=len(out), patterns_used=patterns_used)


# ─── Internal helpers ────────────────────────────────────────────────


def _resolve_rate_per_hour(pattern: AmbientPattern) -> float | None:
    """Pull the events-per-hour number out of a pattern, or None if
    the pattern is time-scheduled (no rate)."""
    raw = pattern.rate or pattern.false_motion_rate
    if raw is None:
        return None
    return _parse_rate(raw)


def _parse_rate(rate_str: str) -> float:
    """Parse 'X/hr' or 'X/min' into events-per-hour."""
    if "/" not in rate_str:
        raise ValueError(f"Unparseable rate {rate_str!r}; expected 'X/hr' or 'X/min'")
    val_str, unit = rate_str.split("/", 1)
    val = float(val_str.strip())
    unit = unit.strip().lower()
    if unit == "hr":
        return val
    if unit == "min":
        return val * 60.0
    raise ValueError(f"Unknown rate unit {unit!r} in {rate_str!r}")


def _expand_cameras(pattern: AmbientPattern, all_camera_ids: list[str]) -> list[str]:
    """Resolve ``cams: all`` to every camera; otherwise pass through."""
    if pattern.cams == "all":
        return list(all_camera_ids)
    return list(pattern.cams)


def _poisson_sample(rng: random.Random, lam: float) -> int:
    """Knuth's poisson sampler. Sufficient for the small λ values
    (typically <20) that ambient patterns produce per day.

    Falls back to a normal approximation for large λ to keep runtime
    bounded — not currently exercised by canonical patterns but
    cheap insurance.
    """
    if lam <= 0:
        return 0
    if lam < 30:
        l_exp = math.exp(-lam)
        k = 0
        p = 1.0
        while True:
            k += 1
            p *= rng.random()
            if p < l_exp:
                return k - 1
    # Normal approximation for large λ.
    return max(0, round(rng.gauss(lam, math.sqrt(lam))))
