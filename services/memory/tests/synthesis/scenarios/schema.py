"""Pydantic schema for scenario YAML files.

A scenario authors a deterministic synthetic timeline the harness
replays: which events fire when, what the (mocked) VLM decided about
each, what assertions to verify at the end. The schema is strict
(``extra=forbid``) so typos in fixtures fail at load time, not at
the wrong assertion three minutes into a scenario run.

Phase 1B scope: declared events + optional recurring events + inline
oracle VLM responses + assertion DSL. Adversarial generators, noise
profiles beyond `none`/`minimal`, and recorded-VLM responses are
Phase 2+.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ─── Bootstrap (initial graph state) ─────────────────────────────────


class BootstrapActor(_Strict):
    id: str
    name: str
    role: str
    access_profile: str = "none"


class Bootstrap(_Strict):
    known_actors: list[BootstrapActor] = Field(default_factory=list)


# ─── Events ──────────────────────────────────────────────────────────


class PreprocessorOutput(_Strict):
    tag_set: list[str] = Field(default_factory=list)
    matched_actor_ids: list[str] = Field(default_factory=list)


class TruthLabel(_Strict):
    """Ground-truth annotation — used only by assertions, never visible
    to the system under test."""

    actor_id: str | None = None
    intent: str = ""
    should_fire_alert: bool = False
    expected_tier: str | None = None
    notes: str = ""


class AuthoredPolicySpec(_Strict):
    """A policy the VLM authors as part of its response.

    Becomes a Policy node in the graph. Future events whose
    ``preprocessor_output.tag_set`` is a subset of
    ``match_tag_subset`` (and that fire on ``scope_camera``) short-
    circuit the VLM — they're recorded but not analyzed.
    """

    id: str
    kind: Literal["dismissal", "transient_intent"] = "dismissal"
    scope_camera: str | None = None
    """None = any camera. Most policies scope to one camera."""

    match_tag_subset: list[str] = Field(default_factory=list)
    """The allowed tag set. An event matches when its tag_set is a
    subset of this set."""

    ttl_seconds: float
    rationale: str = ""


class VLMResponseSpec(_Strict):
    """Inline VLM oracle response for one event.

    Phase 1B uses inline responses; Phase 2 will support
    ``ref: oracle:canonical_milkman/day1`` to pull from a shared
    oracle YAML.
    """

    id: str
    """The VLMDecision node id this response should produce."""

    citations: list[str] = Field(default_factory=list)
    """Memory node ids the VLM cited as pertinent. Each becomes a
    CITED edge with default weight."""

    findings_summary: str = ""
    citation_weight: float = 0.5
    """Initial weight for CITED edges. Phase 2+ this will be
    computed by the dispatcher from tier + outcome quality;
    Phase 1B uses a flat default."""

    authored_policies: list[AuthoredPolicySpec] = Field(default_factory=list)
    """Dismissal policies + TransientIntents the VLM authored in
    this response. Each becomes a Policy node; active policies
    short-circuit future matching events."""


class DeclaredEvent(_Strict):
    """One event the scenario asserts must fire at a specific simulated
    time."""

    day: int
    """1-indexed day within the scenario."""

    time: str
    """HH:MM (24h). Combined with the scenario's start_ts + day to
    produce a Unix timestamp."""

    camera: str

    preprocessor_output: PreprocessorOutput = Field(default_factory=PreprocessorOutput)

    vlm_response: VLMResponseSpec | None = None
    """If present, the mocked VLM is invoked on this event with this
    response. If absent, the event is recorded but no VLM call."""

    truth: TruthLabel = Field(default_factory=TruthLabel)


class RecurringEvent(_Strict):
    """Compact way to author N similar events across days."""

    from_day: int
    to_day: int
    """Inclusive."""

    time: str
    weekdays: list[str] | None = None
    """If set, only fire on these weekdays (mon, tue, ...). None = every
    day in the range."""

    camera: str

    preprocessor_output: PreprocessorOutput = Field(default_factory=PreprocessorOutput)

    vlm_response_template: VLMResponseSpec | None = None
    """Template — ``{day}`` in the ``id`` field is substituted with the
    1-indexed day."""

    truth: TruthLabel = Field(default_factory=TruthLabel)


# ─── Assertions ──────────────────────────────────────────────────────


class AssertEventCount(_Strict):
    kind: Literal["event_count"]
    camera: str | None = None
    expected: int
    description: str = ""


class AssertVLMInvocationCount(_Strict):
    kind: Literal["vlm_invocation_count"]
    expected: int
    description: str = ""


class AssertEdgeWeightAtLeast(_Strict):
    kind: Literal["edge_weight_at_least"]
    decision_id: str
    memory_id: str
    min_weight: float
    description: str = ""


class AssertPruningCandidate(_Strict):
    kind: Literal["pruning_candidate_exists"]
    node_kind: Literal["Event", "KnownActor"] = "KnownActor"
    threshold: float = 0.5
    must_include: str
    description: str = ""


class AssertPolicyCount(_Strict):
    kind: Literal["policy_count"]
    policy_kind: Literal["dismissal", "transient_intent"] | None = None
    expected: int
    description: str = ""


class AssertVLMInvocationsBelow(_Strict):
    """Dismissal-policy scenarios: assert the VLM was invoked FEWER
    than this many times despite there being more events. Proves the
    policy short-circuited subsequent VLM calls."""

    kind: Literal["vlm_invocations_below"]
    max_invocations: int
    description: str = ""


Assertion = (
    AssertEventCount
    | AssertVLMInvocationCount
    | AssertEdgeWeightAtLeast
    | AssertPruningCandidate
    | AssertPolicyCount
    | AssertVLMInvocationsBelow
)


# ─── Scenario root ───────────────────────────────────────────────────


class Scenario(_Strict):
    name: str
    description: str = ""
    household: str = "canonical"
    seed: int = 1
    duration_days: int
    noise_profile: Literal["none", "minimal", "moderate", "realistic"] = "minimal"
    start_ts: float = 1_767_225_600.0
    """Unix seconds of simulated T0. Default: 2026-01-01 00:00 UTC."""

    bootstrap: Bootstrap = Field(default_factory=Bootstrap)
    events: list[DeclaredEvent] = Field(default_factory=list)
    recurring_events: list[RecurringEvent] = Field(default_factory=list)
    assertions: list[Assertion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_days(self) -> Scenario:
        if self.duration_days <= 0:
            raise ValueError("duration_days must be positive")
        for e in self.events:
            if not 1 <= e.day <= self.duration_days:
                raise ValueError(f"declared event day={e.day} outside [1, {self.duration_days}]")
        for r in self.recurring_events:
            if not (1 <= r.from_day <= r.to_day <= self.duration_days):
                raise ValueError(
                    f"recurring [from_day={r.from_day}, to_day={r.to_day}] "
                    f"outside [1, {self.duration_days}]"
                )
        return self


def load_scenario(path: str | Path) -> Scenario:
    """Load + validate a scenario YAML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Scenario file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return Scenario.model_validate(raw)
