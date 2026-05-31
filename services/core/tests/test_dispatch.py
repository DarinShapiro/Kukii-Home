"""Tests for action dispatch (Epic 8 — dispatcher, policy, routing, escalation)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from kukiihome_core.dispatch import (
    AckTracker,
    ActionDispatcher,
    DeeperAssessmentLoop,
    EscalationEngine,
    ExplanationGenerator,
    FeedbackType,
    LastResponderTracker,
    OccupancyRouter,
    OccupancySnapshot,
    PolicyDisposition,
    PolicyGate,
    PreApproval,
    PreApprovalRegistry,
    QuietHours,
    RemediationRegistry,
    ResidentPreferences,
    TierRouter,
)
from kukiihome_core.rules import ResolutionOutcome
from kukiihome_shared.generated.events.action_event import ActionType, Tier

# ─────────────────────────────────────────────────────────────────────
# TierRouter
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "criticality, confidence, severity, urgent, expected",
    [
        ("info", 0.50, "info", False, Tier.tier_0_silent),
        ("info", 0.72, "info", False, Tier.tier_1_in_app),
        ("warning", 0.86, "warning", False, Tier.tier_2_push),
        ("alert", 0.94, "alert", False, Tier.tier_3_wake),
        ("alert", 0.99, "alert", False, Tier.tier_4_emergency),
        ("info", 0.50, "info", True, Tier.tier_4_emergency),
    ],
)
def test_tier_router_maps_to_expected(criticality, confidence, severity, urgent, expected):
    dec = TierRouter().route(
        criticality=criticality,
        confidence=confidence,
        severity=severity,
        urgent_alert=urgent,
    )
    assert dec.tier == expected


# ─────────────────────────────────────────────────────────────────────
# Quiet hours + DND
# ─────────────────────────────────────────────────────────────────────


def test_quiet_hours_silences_tier_2_overnight():
    prefs = ResidentPreferences(resident_id="r1", quiet_hours=(time(23, 0), time(7, 0)))
    midnight = datetime(2026, 5, 25, 2, 0, tzinfo=UTC)
    tier, silent = QuietHours().apply(tier=Tier.tier_2_push, prefs=prefs, now=midnight)
    assert tier == Tier.tier_2_push and silent is True


def test_quiet_hours_force_audio_overrides():
    prefs = ResidentPreferences(resident_id="r1", quiet_hours=(time(23, 0), time(7, 0)))
    midnight = datetime(2026, 5, 25, 2, 0, tzinfo=UTC)
    _tier, silent = QuietHours().apply(
        tier=Tier.tier_2_push, prefs=prefs, now=midnight, force_audio=True
    )
    assert silent is False


def test_quiet_hours_keeps_tier_3_audible():
    prefs = ResidentPreferences(resident_id="r1", quiet_hours=(time(23, 0), time(7, 0)))
    midnight = datetime(2026, 5, 25, 2, 0, tzinfo=UTC)
    _, silent = QuietHours().apply(tier=Tier.tier_3_wake, prefs=prefs, now=midnight)
    assert silent is False


# ─────────────────────────────────────────────────────────────────────
# Occupancy router
# ─────────────────────────────────────────────────────────────────────


def test_occupancy_router_bumps_alert_to_tier3_when_no_one_home():
    r1 = ResidentPreferences(resident_id="r1")
    r2 = ResidentPreferences(resident_id="r2")
    targets = OccupancyRouter().route(
        base_tier=Tier.tier_2_push,
        criticality="alert",
        residents=[r1, r2],
        occupancy=OccupancySnapshot(home=frozenset(), away=frozenset({"r1", "r2"})),
        now=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
    )
    assert all(t.tier == Tier.tier_3_wake for t in targets)


def test_occupancy_router_calls_away_residents_on_alert():
    r1 = ResidentPreferences(resident_id="r1")
    r2 = ResidentPreferences(resident_id="r2")
    targets = OccupancyRouter().route(
        base_tier=Tier.tier_2_push,
        criticality="alert",
        residents=[r1, r2],
        occupancy=OccupancySnapshot(home=frozenset({"r1"}), away=frozenset({"r2"})),
        now=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
    )
    by_id = {t.resident_id: t for t in targets}
    assert by_id["r1"].tier == Tier.tier_2_push
    assert by_id["r2"].tier == Tier.tier_3_wake


def test_occupancy_router_respects_emergency_only_dnd():
    r1 = ResidentPreferences(resident_id="r1", emergency_only=True)
    targets = OccupancyRouter().route(
        base_tier=Tier.tier_2_push,
        criticality="warning",
        residents=[r1],
        occupancy=OccupancySnapshot(home=frozenset({"r1"})),
        now=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
    )
    assert targets == []


def test_occupancy_router_vacation_mode_bumps_tier():
    r1 = ResidentPreferences(resident_id="r1", vacation_mode=True)
    targets = OccupancyRouter().route(
        base_tier=Tier.tier_1_in_app,
        criticality="warning",
        residents=[r1],
        occupancy=OccupancySnapshot(home=frozenset(), away=frozenset({"r1"})),
        now=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
    )
    assert targets[0].tier == Tier.tier_2_push


# ─────────────────────────────────────────────────────────────────────
# Policy gate + pre-approval
# ─────────────────────────────────────────────────────────────────────


def test_policy_gate_allows_lights():
    gate = PolicyGate()
    d = gate.evaluate({"type": "ha_service_call", "service": "light.turn_on"})
    assert d.disposition == PolicyDisposition.auto


def test_policy_gate_gates_locks():
    gate = PolicyGate()
    d = gate.evaluate({"type": "ha_service_call", "service": "lock.unlock"})
    assert d.disposition == PolicyDisposition.gated


def test_policy_gate_blocks_siren_with_fallback():
    gate = PolicyGate()
    d = gate.evaluate({"type": "ha_service_call", "service": "siren.turn_on"})
    assert d.disposition == PolicyDisposition.blocked
    assert d.fallback_action is not None
    assert d.fallback_action["type"] == "notify"


def test_pre_approval_registry_promotes_gated_to_auto():
    reg = PreApprovalRegistry()
    reg.register(PreApproval(rule_id="r42", service="lock.unlock"))
    gate = PolicyGate(reg)
    d = gate.evaluate({"type": "ha_service_call", "service": "lock.unlock"})
    assert d.disposition == PolicyDisposition.auto
    assert d.pre_approval_rule_id == "r42"


def test_pre_approval_respects_conditions():
    reg = PreApprovalRegistry()
    reg.register(
        PreApproval(
            rule_id="r99",
            service="lock.unlock",
            conditions_satisfied=lambda ctx: ctx.get("subject") == "sarah",
        )
    )
    gate = PolicyGate(reg)
    # Wrong subject — falls back to gated.
    d = gate.evaluate(
        {"type": "ha_service_call", "service": "lock.unlock"},
        ctx={"subject": "stranger"},
    )
    assert d.disposition == PolicyDisposition.gated


# ─────────────────────────────────────────────────────────────────────
# Remediation registry + deeper-assessment loop
# ─────────────────────────────────────────────────────────────────────


def test_remediation_proposes_light_for_low_light():
    reg = RemediationRegistry()
    proposed = reg.propose(["low_light"], {"light": ["light.porch"], "ptz": ["camera.front"]})
    assert len(proposed) == 1
    assert proposed[0].action["service"] == "light.turn_on"
    assert proposed[0].action["entity_id"] == "light.porch"


def test_remediation_handles_unmappable_factor():
    reg = RemediationRegistry()
    assert reg.propose(["adverse_weather"], {"light": ["light.porch"]}) == []


@pytest.mark.asyncio
async def test_deeper_assessment_runs_vlm_after_remediation():
    calls: list[dict] = []

    async def fake_vlm(req: dict) -> dict:
        calls.append(req)
        return {"criticality": "alert", "confidence": 0.95}

    loop = DeeperAssessmentLoop(
        RemediationRegistry(),
        vlm_call=fake_vlm,
        wait_seconds=0.0,
    )
    result = await loop.maybe_run(
        vlm_response={
            "criticality": "warning",
            "confidence": 0.6,
            "limiting_factors": ["low_light"],
        },
        area_resources={"light": ["light.porch"]},
        rebuild_request=lambda: {"prompt": "retry"},
    )
    assert result.triggered is True
    assert result.second_response == {"criticality": "alert", "confidence": 0.95}
    assert calls == [{"prompt": "retry"}]


@pytest.mark.asyncio
async def test_deeper_assessment_skips_without_limiting_factors():
    loop = DeeperAssessmentLoop(RemediationRegistry(), vlm_call=None)
    result = await loop.maybe_run(
        vlm_response={"criticality": "alert", "confidence": 0.99, "limiting_factors": []},
        area_resources={},
        rebuild_request=lambda: {},
    )
    assert result.triggered is False
    assert result.remediations == []


# ─────────────────────────────────────────────────────────────────────
# Explanation generator
# ─────────────────────────────────────────────────────────────────────


def test_explanation_cites_rules_and_actors():
    expl = ExplanationGenerator().render(
        criticality="alert",
        confidence=0.92,
        rules_fired=["rule_intruder_night", "rule_unknown_face"],
        identified_actors=[{"name": "stranger", "confidence": 0.85}],
        limiting_factors=["low_light"],
        vlm_explanation="Unfamiliar face in entry zone at 22:45.",
        subject_label="unknown person",
        location_label="front door",
    )
    assert expl.headline == "Alert: unknown person at front door"
    assert len(expl.rules_fired) == 2
    assert any("low light" in w for w in expl.why)
    assert any(p["rule_id"] == "rule_intruder_night" for p in expl.edit_paths)


# ─────────────────────────────────────────────────────────────────────
# Ack tracker
# ─────────────────────────────────────────────────────────────────────


def test_ack_tracker_records_feedback_and_aggregates():
    tracker = AckTracker()
    t0 = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    tracker.issue("alert_1", rule_ids=["r1", "r2"], now=t0)
    rec = tracker.record(
        "alert_1",
        feedback=FeedbackType.false_alarm,
        resident_id="r1",
        now=t0 + timedelta(seconds=20),
    )
    assert rec is not None
    assert rec.response_latency == timedelta(seconds=20)
    stats = tracker.aggregate_rule_stats()
    assert stats["r1"]["false_alarm"] == 1
    assert stats["r2"]["false_alarm"] == 1


# ─────────────────────────────────────────────────────────────────────
# Last-responder tracker
# ─────────────────────────────────────────────────────────────────────


def test_last_responder_escalates_after_timeout():
    tracker = LastResponderTracker(review_timeout=timedelta(minutes=1))
    t0 = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    tracker.open("alert_x")
    tracker.mark_reviewing("alert_x", "r1", now=t0)
    # Before timeout: no escalation.
    assert tracker.escalate_to_others("alert_x", ["r1", "r2"], now=t0 + timedelta(seconds=30)) == []
    # After timeout, unreviewing residents get paged.
    assert tracker.escalate_to_others("alert_x", ["r1", "r2"], now=t0 + timedelta(minutes=2)) == [
        "r2"
    ]


def test_last_responder_no_escalation_when_resolved():
    tracker = LastResponderTracker(review_timeout=timedelta(minutes=1))
    t0 = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    tracker.open("alert_x")
    tracker.mark_reviewing("alert_x", "r1", now=t0)
    tracker.mark_resolved("alert_x", "r1")
    assert tracker.escalate_to_others("alert_x", ["r1", "r2"], now=t0 + timedelta(minutes=2)) == []


# ─────────────────────────────────────────────────────────────────────
# Escalation engine
# ─────────────────────────────────────────────────────────────────────


def test_escalation_engine_schedules_tier3_with_no_one_home():
    eng = EscalationEngine()
    t0 = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    timer = eng.schedule("a1", tier=Tier.tier_2_push, now=t0, no_one_home=True)
    assert timer is not None
    # tick before timeout: nothing.
    assert eng.tick(now=t0 + timedelta(seconds=30)) == []
    # tick after timeout: fired.
    fired = eng.tick(now=t0 + timedelta(seconds=120))
    assert [t.alert_id for t in fired] == ["a1"]


def test_escalation_engine_skips_tier1_without_opt_in():
    eng = EscalationEngine()
    t0 = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    assert eng.schedule("a1", tier=Tier.tier_1_in_app, now=t0) is None


def test_escalation_engine_follow_up_fires_immediately():
    eng = EscalationEngine()
    t0 = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    eng.schedule("a1", tier=Tier.tier_3_wake, now=t0)
    timer = eng.follow_up_detected("a1", now=t0 + timedelta(seconds=10))
    assert timer is not None
    fired = eng.tick(now=t0 + timedelta(seconds=11))
    assert any(t.alert_id == "a1" for t in fired)


def test_escalation_cancel_removes_timer():
    eng = EscalationEngine()
    t0 = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    eng.schedule("a1", tier=Tier.tier_3_wake, now=t0)
    eng.cancel("a1")
    assert eng.tick(now=t0 + timedelta(seconds=300)) == []


# ─────────────────────────────────────────────────────────────────────
# ActionDispatcher.plan()
# ─────────────────────────────────────────────────────────────────────


def _resolution(actions=(), winners=("rule_1",), severity="warning"):
    return ResolutionOutcome(
        winning_rule_ids=tuple(winners),
        severity=severity,
        actions=tuple(actions),
        suppressed_rule_ids=(),
    )


def test_dispatcher_emits_push_for_warning_with_two_residents():
    disp = ActionDispatcher()
    r1 = ResidentPreferences(resident_id="r1", quiet_hours=None)
    r2 = ResidentPreferences(resident_id="r2", quiet_hours=None)
    plan = disp.plan(
        event_id="e1",
        vlm_response={"criticality": "warning", "confidence": 0.88},
        resolution=_resolution(),
        residents=[r1, r2],
        occupancy=OccupancySnapshot(home=frozenset({"r1", "r2"})),
    )
    assert plan.tier_decision.tier == Tier.tier_2_push
    push_actions = [a for a in plan.actions if a.action_type == ActionType.notify_push]
    assert len(push_actions) == 2
    assert plan.escalation is None  # tier 2 + everyone home + no opt-in


def test_dispatcher_routes_gated_action_through_ask():
    disp = ActionDispatcher()
    r1 = ResidentPreferences(resident_id="r1", quiet_hours=None)
    plan = disp.plan(
        event_id="e2",
        vlm_response={"criticality": "alert", "confidence": 0.93},
        resolution=_resolution(
            actions=(
                {"type": "ha_service_call", "service": "lock.unlock", "entity_id": "lock.front"},
            ),
            severity="alert",
        ),
        residents=[r1],
        occupancy=OccupancySnapshot(home=frozenset({"r1"})),
    )
    # The unlock should be present + an ask should accompany it.
    services = [a.ha_service.service for a in plan.actions if a.ha_service is not None]
    assert "lock.unlock" in services
    asks = [a for a in plan.actions if a.action_type == ActionType.ask]
    assert len(asks) == 1
    gated = [a for a in plan.actions if a.policy_gate_required]
    assert len(gated) == 1


def test_dispatcher_blocks_siren_and_emits_fallback_notify():
    disp = ActionDispatcher()
    r1 = ResidentPreferences(resident_id="r1", quiet_hours=None)
    plan = disp.plan(
        event_id="e3",
        vlm_response={"criticality": "alert", "confidence": 0.99},
        resolution=_resolution(
            actions=({"type": "ha_service_call", "service": "siren.turn_on"},),
            severity="alert",
        ),
        residents=[r1],
        occupancy=OccupancySnapshot(home=frozenset({"r1"})),
    )
    siren = [
        a
        for a in plan.actions
        if a.ha_service is not None and a.ha_service.service == "siren.turn_on"
    ]
    assert siren == []  # blocked
    assert plan.policy_blocks
    assert plan.policy_blocks[0].disposition == PolicyDisposition.blocked


def test_dispatcher_records_ack_issue():
    disp = ActionDispatcher()
    plan = disp.plan(
        event_id="e4",
        vlm_response={"criticality": "warning", "confidence": 0.8},
        resolution=_resolution(),
        residents=[ResidentPreferences(resident_id="r1", quiet_hours=None)],
        occupancy=OccupancySnapshot(home=frozenset({"r1"})),
    )
    rec = disp.ack_tracker.get(plan.alert_id)
    assert rec is not None
    assert rec.rule_ids == ("rule_1",)


@pytest.mark.asyncio
async def test_dispatcher_dispatch_no_bus_is_noop():
    disp = ActionDispatcher()
    plan = disp.plan(
        event_id="e5",
        vlm_response={"criticality": "info", "confidence": 0.5},
        resolution=_resolution(severity="info"),
        residents=[ResidentPreferences(resident_id="r1", quiet_hours=None)],
        occupancy=OccupancySnapshot(home=frozenset({"r1"})),
    )
    # Tier 0: no targets emitted.
    assert plan.tier_decision.tier == Tier.tier_0_silent
    await disp.dispatch(plan)  # should not raise


@pytest.mark.asyncio
async def test_dispatcher_publishes_via_bus():
    class FakeBus:
        def __init__(self):
            self.published: list[tuple[str, object]] = []

        async def publish(self, subject, message, **kwargs):
            self.published.append((subject, message))

    bus = FakeBus()
    disp = ActionDispatcher(bus=bus)
    plan = disp.plan(
        event_id="e6",
        vlm_response={"criticality": "alert", "confidence": 0.95},
        resolution=_resolution(severity="alert"),
        residents=[ResidentPreferences(resident_id="r1", quiet_hours=None)],
        occupancy=OccupancySnapshot(home=frozenset({"r1"})),
    )
    await disp.dispatch(plan)
    assert bus.published
    assert all(subj.startswith("actions.") for subj, _ in bus.published)
