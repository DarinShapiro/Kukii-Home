"""Integration: end-to-end action dispatch (rule resolution → plan → notify).

Exercises tier escalation, conversational ask flow, and policy enforcement
together without the NATS bus — the worker side is exercised by passing the
emitted ActionEvents through a NotifyWorker with stub HA caller.

Tagged ``integration`` because it spans services/core + services/notify.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sentihome_core.dispatch import (
    ActionDispatcher,
    OccupancySnapshot,
    PolicyDisposition,
    ResidentPreferences,
)
from sentihome_core.rules import ResolutionOutcome
from sentihome_notify.dispatcher import (
    AskFlow,
    AskOutcome,
    NotifyWorker,
    PushDispatcher,
    TTSDispatcher,
)
from sentihome_shared.generated.events.action_event import ActionType, Tier

pytestmark = pytest.mark.integration


def _stub_ha_calls():
    calls: list[tuple[str, dict]] = []

    async def caller(service, data):
        calls.append((service, data))
        return {"ok": True}

    return caller, calls


def _resolution(actions=()):
    return ResolutionOutcome(
        winning_rule_ids=("rule_intruder",),
        severity="alert",
        actions=tuple(actions),
        suppressed_rule_ids=(),
    )


async def test_end_to_end_tier3_alert_pushes_and_speaks_no_one_home():
    """No-one-home + alert → bumped to Tier 3 → push + TTS dispatched."""
    ha, calls = _stub_ha_calls()
    dispatcher = ActionDispatcher()
    push = PushDispatcher(ha_caller=ha, resident_to_service={"r1": "notify.mobile_app_r1"})
    tts = TTSDispatcher(ha_caller=ha, media_player_entities=["media_player.kitchen"])
    worker = NotifyWorker(push=push, tts=tts, ask_flow=AskFlow())

    plan = dispatcher.plan(
        event_id="event_intruder",
        vlm_response={"criticality": "alert", "confidence": 0.95},
        resolution=_resolution(),
        residents=[ResidentPreferences(resident_id="r1", quiet_hours=None)],
        occupancy=OccupancySnapshot(home=frozenset(), away=frozenset({"r1"})),
    )
    assert plan.tier_decision.tier == Tier.tier_3_wake

    for action in plan.actions:
        await worker.handle(action)

    services = [s for s, _ in calls]
    assert any(s.startswith("notify.mobile_app_") for s in services)
    assert any(s == "tts.cloud_say" for s in services)


async def test_end_to_end_gated_unlock_emits_ask_and_user_confirms():
    """A gated unlock triggers an ask; the user confirms; the lock action is
    still in the plan but flagged ``policy_gate_required=True`` so the
    ha-agent (Epic 9) knows to wait for ask resolution."""
    ha, _calls = _stub_ha_calls()
    dispatcher = ActionDispatcher()
    push = PushDispatcher(ha_caller=ha, resident_to_service={"r1": "notify.r1"})
    flow = AskFlow()
    worker = NotifyWorker(push=push, ask_flow=flow)

    plan = dispatcher.plan(
        event_id="event_door",
        vlm_response={"criticality": "warning", "confidence": 0.88},
        resolution=_resolution(
            actions=(
                {"type": "ha_service_call", "service": "lock.unlock", "entity_id": "lock.front"},
            ),
        ),
        residents=[ResidentPreferences(resident_id="r1", quiet_hours=None)],
        occupancy=OccupancySnapshot(home=frozenset({"r1"})),
    )

    asks = [a for a in plan.actions if a.action_type == ActionType.ask]
    locks = [
        a
        for a in plan.actions
        if a.ha_service is not None and a.ha_service.service == "lock.unlock"
    ]
    assert len(asks) == 1
    assert len(locks) == 1
    assert locks[0].policy_gate_required is True

    for action in plan.actions:
        await worker.handle(action)

    flow.respond(asks[0].action_id, outcome=AskOutcome.yes, resident_id="r1")
    outcome = await asyncio.wait_for(flow.wait(asks[0].action_id), timeout=1.0)
    assert outcome == AskOutcome.yes


async def test_end_to_end_siren_blocked_emits_fallback():
    """A rule action requesting siren.turn_on is hard-blocked; a fallback
    notify lands in the plan and reaches the push dispatcher."""
    ha, calls = _stub_ha_calls()
    dispatcher = ActionDispatcher()
    push = PushDispatcher(ha_caller=ha, resident_to_service={"r1": "notify.r1"})
    worker = NotifyWorker(push=push)

    plan = dispatcher.plan(
        event_id="event_break_in",
        vlm_response={"criticality": "alert", "confidence": 0.97},
        resolution=_resolution(
            actions=({"type": "ha_service_call", "service": "siren.turn_on"},),
        ),
        residents=[ResidentPreferences(resident_id="r1", quiet_hours=None)],
        occupancy=OccupancySnapshot(home=frozenset({"r1"})),
    )
    assert plan.policy_blocks
    assert plan.policy_blocks[0].disposition == PolicyDisposition.blocked

    # The lock-style block is absent in plan.actions; the fallback notify is.
    services = [a.ha_service.service for a in plan.actions if a.ha_service is not None]
    assert "siren.turn_on" not in services

    for action in plan.actions:
        await worker.handle(action)
    assert calls, "fallback notify should have been pushed via HA"


async def test_tier_escalation_fires_on_unanswered_alert():
    """Tier 2 alert with no-one-home schedules an escalation; ticker fires
    it after the timeout."""
    dispatcher = ActionDispatcher()
    plan = dispatcher.plan(
        event_id="event_late",
        vlm_response={"criticality": "warning", "confidence": 0.88},
        resolution=_resolution(),
        residents=[ResidentPreferences(resident_id="r1", quiet_hours=None)],
        occupancy=OccupancySnapshot(home=frozenset(), away=frozenset({"r1"})),
        now=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
    )
    # No-one-home auto-bumped the alert criticality? warning stays Tier 2;
    # but the engine still arms a timer because no-one is home.
    assert plan.escalation is not None
    fired = dispatcher.escalation_engine.tick(
        now=datetime(2026, 5, 25, 12, 0, tzinfo=UTC) + timedelta(seconds=120)
    )
    assert [t.alert_id for t in fired] == [plan.alert_id]
