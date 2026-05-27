"""Scenario runner — drives a loaded Scenario through a GraphClient.

Expands recurring-event templates into individual events, sorts by
simulated timestamp, walks them in order, writes graph nodes + edges,
optionally invokes the (inline) VLM oracle response, then evaluates
assertions against the resulting graph state.

This is **Phase 1B**: assertions operate on graph nodes + edges
directly. No noise generator (the ``none`` profile is hard-wired);
no separate oracle file; no scenario-level rule timeline. Those
land in Phase 2.

Designed to be fast: a 30-day scenario with ~5 events/day takes
milliseconds end-to-end against the InMemoryGraphClient and a few
hundred ms against a live Neo4j testcontainer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sentihome_memory.graph import (
    CitedEdge,
    Event,
    GraphClient,
    KnownActor,
    NodeKind,
    Policy,
    VLMDecision,
)

from synthesis.scenarios.schema import (
    AssertEdgeWeightAtLeast,
    AssertEventCount,
    AssertPolicyCount,
    AssertPruningCandidate,
    AssertVLMInvocationCount,
    AssertVLMInvocationsBelow,
    DeclaredEvent,
    PreprocessorOutput,
    RecurringEvent,
    Scenario,
    TruthLabel,
    VLMResponseSpec,
)
from synthesis.time_provider import TimeProvider

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class _ResolvedEvent:
    """A declared or recurring event flattened to an absolute timestamp."""

    ts: float
    day: int
    camera: str
    preprocessor_output: PreprocessorOutput
    vlm_response: VLMResponseSpec | None
    truth: TruthLabel


@dataclass
class ScenarioResult:
    """Outcome of running one scenario.

    Returned by :func:`run_scenario` so tests can introspect counts,
    assertion failures, and timing.
    """

    scenario_name: str
    elapsed_simulated_seconds: float
    events_written: int
    vlm_decisions_written: int
    citations_written: int
    policies_written: int
    events_dismissed_by_policy: int
    assertion_failures: list[str]

    @property
    def passed(self) -> bool:
        return len(self.assertion_failures) == 0


def run_scenario(scenario: Scenario, client: GraphClient) -> ScenarioResult:
    """Execute a scenario end-to-end against ``client``.

    Idempotent on a freshly-cleared client: callers should
    ``client.clear_all()`` first to ensure assertions reflect only
    this scenario's effects.
    """
    tp = TimeProvider(start_ts=scenario.start_ts)

    # 1. Bootstrap initial graph state.
    for actor in scenario.bootstrap.known_actors:
        client.write_known_actor(
            KnownActor(
                id=actor.id,
                name=actor.name,
                role=actor.role,
                access_profile=actor.access_profile,
            )
        )

    # 2. Resolve all events to a flat, time-sorted list.
    resolved = _resolve_events(scenario)
    resolved.sort(key=lambda e: e.ts)

    # 3. Walk the timeline, writing graph state.
    events_written = 0
    vlm_decisions_written = 0
    citations_written = 0
    policies_written = 0
    events_dismissed_by_policy = 0

    for i, ev in enumerate(resolved):
        tp.advance_to(ev.ts)
        now = tp.now()

        event_id = f"evt_{ev.camera}_{int(ev.ts)}_{i:04d}"
        tag_set = tuple(sorted(ev.preprocessor_output.tag_set))
        event = Event(
            id=event_id,
            ts=now,
            camera_id=ev.camera,
            tag_set=tag_set,
            matched_actor_ids=tuple(ev.preprocessor_output.matched_actor_ids),
            metadata={"intent": ev.truth.intent} if ev.truth.intent else {},
        )
        client.write_event(event)
        events_written += 1

        # Check active dismissal policies BEFORE invoking VLM. This is
        # the content-based throttle from Epic 10: an event whose tag
        # set fits inside an active dismissal scope is short-circuited,
        # recorded but not analyzed.
        dismissal_match = _find_dismissal_match(
            client, now=now, camera_id=ev.camera, tag_set=tag_set
        )
        if dismissal_match is not None:
            events_dismissed_by_policy += 1
            continue  # skip VLM invocation entirely

        # No active dismissal — invoke the oracle if the scenario
        # supplied one. (An event without an oracle response and
        # without a matching dismissal is just a recorded observation
        # the VLM didn't consider — also a real scenario shape.)
        if ev.vlm_response is None:
            continue

        decision = VLMDecision(
            id=ev.vlm_response.id,
            ts=now,
            triggered_by_event_id=event_id,
            findings_summary=ev.vlm_response.findings_summary,
        )
        client.write_vlm_decision(decision)
        vlm_decisions_written += 1

        for cited_memory_id in ev.vlm_response.citations:
            client.write_cited_edge(
                CitedEdge(
                    decision_id=decision.id,
                    memory_id=cited_memory_id,
                    weight=ev.vlm_response.citation_weight,
                    created_ts=now,
                    last_reinforced_ts=now,
                )
            )
            citations_written += 1

        # The VLM may author one or more policies. Each becomes an
        # active Policy node from this moment forward (until TTL).
        for ap in ev.vlm_response.authored_policies:
            client.write_policy(
                Policy(
                    id=ap.id,
                    kind=ap.kind,
                    scope_camera=ap.scope_camera,
                    match_tag_subset=tuple(sorted(ap.match_tag_subset)),
                    ttl_seconds=ap.ttl_seconds,
                    created_ts=now,
                    rationale=ap.rationale,
                )
            )
            policies_written += 1

    # 4. Evaluate assertions.
    failures = _evaluate_assertions(scenario, client, vlm_decisions_written)

    return ScenarioResult(
        scenario_name=scenario.name,
        elapsed_simulated_seconds=tp.elapsed_seconds,
        events_written=events_written,
        vlm_decisions_written=vlm_decisions_written,
        citations_written=citations_written,
        policies_written=policies_written,
        events_dismissed_by_policy=events_dismissed_by_policy,
        assertion_failures=failures,
    )


def _find_dismissal_match(
    client: GraphClient, *, now: float, camera_id: str, tag_set: tuple[str, ...]
) -> Policy | None:
    """First active dismissal policy whose scope + tag-subset match.

    Phase 1B: only ``dismissal`` policies short-circuit. Phase 2 will
    add ``transient_intent`` policies that escalate instead.
    """
    for policy in client.list_active_policies(now_ts=now):
        if policy.kind != "dismissal":
            continue
        if policy.matches_event(camera_id, tag_set):
            return policy
    return None


# ─── Internal: event resolution ──────────────────────────────────────


def _resolve_events(scenario: Scenario) -> list[_ResolvedEvent]:
    """Flatten declared + recurring events to absolute timestamps."""
    out: list[_ResolvedEvent] = []

    for de in scenario.events:
        out.append(_resolve_declared(scenario, de))

    for re in scenario.recurring_events:
        out.extend(_expand_recurring(scenario, re))

    return out


def _resolve_declared(scenario: Scenario, e: DeclaredEvent) -> _ResolvedEvent:
    ts = _compute_ts(scenario.start_ts, e.day, e.time)
    return _ResolvedEvent(
        ts=ts,
        day=e.day,
        camera=e.camera,
        preprocessor_output=e.preprocessor_output,
        vlm_response=e.vlm_response,
        truth=e.truth,
    )


def _expand_recurring(scenario: Scenario, r: RecurringEvent) -> list[_ResolvedEvent]:
    out: list[_ResolvedEvent] = []
    for day in range(r.from_day, r.to_day + 1):
        if r.weekdays is not None:
            weekday_idx = _weekday_for_day(scenario.start_ts, day)
            if _WEEKDAYS[weekday_idx] not in r.weekdays:
                continue
        ts = _compute_ts(scenario.start_ts, day, r.time)
        # Substitute {day} in the response id template.
        vlm_response: VLMResponseSpec | None = None
        if r.vlm_response_template is not None:
            tmpl = r.vlm_response_template
            vlm_response = VLMResponseSpec(
                id=tmpl.id.replace("{day}", str(day)),
                citations=list(tmpl.citations),
                findings_summary=tmpl.findings_summary,
                citation_weight=tmpl.citation_weight,
            )
        out.append(
            _ResolvedEvent(
                ts=ts,
                day=day,
                camera=r.camera,
                preprocessor_output=r.preprocessor_output,
                vlm_response=vlm_response,
                truth=r.truth,
            )
        )
    return out


def _compute_ts(start_ts: float, day: int, hhmm: str) -> float:
    """Compute Unix ts for ``day`` (1-indexed) at ``HH:MM`` of the scenario."""
    hh, mm = hhmm.split(":", 1)
    seconds_into_day = int(hh) * 3600 + int(mm) * 60
    return start_ts + (day - 1) * 86_400 + seconds_into_day


def _weekday_for_day(start_ts: float, day: int) -> int:
    """0=mon, 6=sun for the given simulated day."""
    dt = datetime.fromtimestamp(start_ts + (day - 1) * 86_400, tz=UTC)
    return dt.weekday()


# ─── Internal: assertion evaluation ──────────────────────────────────


def _evaluate_assertions(
    scenario: Scenario, client: GraphClient, vlm_invocations: int
) -> list[str]:
    failures: list[str] = []
    for assertion in scenario.assertions:
        try:
            err = _eval_one(assertion, client, vlm_invocations)
        except Exception as e:
            err = f"{assertion.kind}: evaluator raised: {e}"
        if err is not None:
            failures.append(err)
    return failures


def _eval_one(assertion, client: GraphClient, vlm_invocations: int) -> str | None:
    """Return None if the assertion holds, an error message if it fails."""
    if isinstance(assertion, AssertEventCount):
        actual = client.count_events(camera_id=assertion.camera)
        if actual != assertion.expected:
            return (
                f"event_count[camera={assertion.camera}]: "
                f"expected {assertion.expected}, got {actual}"
                + (f" — {assertion.description}" if assertion.description else "")
            )
        return None

    if isinstance(assertion, AssertVLMInvocationCount):
        actual = client.count_vlm_decisions()
        if actual != assertion.expected:
            return f"vlm_invocation_count: expected {assertion.expected}, got {actual}" + (
                f" — {assertion.description}" if assertion.description else ""
            )
        return None

    if isinstance(assertion, AssertEdgeWeightAtLeast):
        edges = client.get_citations_from(assertion.decision_id)
        match = next((e for e in edges if e.memory_id == assertion.memory_id), None)
        if match is None:
            return (
                f"edge_weight_at_least: edge "
                f"{assertion.decision_id} -> {assertion.memory_id} not found"
                + (f" — {assertion.description}" if assertion.description else "")
            )
        if match.weight < assertion.min_weight:
            return (
                f"edge_weight_at_least: edge "
                f"{assertion.decision_id} -> {assertion.memory_id} "
                f"has weight {match.weight}, expected >= {assertion.min_weight}"
                + (f" — {assertion.description}" if assertion.description else "")
            )
        return None

    if isinstance(assertion, AssertPruningCandidate):
        kind = NodeKind.EVENT if assertion.node_kind == "Event" else NodeKind.KNOWN_ACTOR
        candidates = client.candidates_for_pruning(threshold=assertion.threshold, kind=kind)
        ids = {c.node_id for c in candidates}
        if assertion.must_include not in ids:
            return (
                f"pruning_candidate_exists: expected {assertion.must_include} "
                f"to be a pruning candidate (threshold {assertion.threshold}, "
                f"kind {assertion.node_kind}), but it isn't. "
                f"Candidates found: {sorted(ids)}"
                + (f" — {assertion.description}" if assertion.description else "")
            )
        return None

    if isinstance(assertion, AssertPolicyCount):
        actual = client.count_policies(kind=assertion.policy_kind)
        if actual != assertion.expected:
            return (
                f"policy_count[kind={assertion.policy_kind}]: "
                f"expected {assertion.expected}, got {actual}"
                + (f" — {assertion.description}" if assertion.description else "")
            )
        return None

    if isinstance(assertion, AssertVLMInvocationsBelow):
        if vlm_invocations > assertion.max_invocations:
            return (
                f"vlm_invocations_below: expected at most "
                f"{assertion.max_invocations}, got {vlm_invocations}"
                + (f" — {assertion.description}" if assertion.description else "")
            )
        return None

    return f"unknown assertion kind: {type(assertion).__name__}"
