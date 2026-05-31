"""The §19 safe-defaults floor in the policy gate + dispatcher (Epic 15).

When failure modes are active, the safe-defaults matrix tightens (never
loosens) a device action's disposition: HA down blocks device control,
VLM down makes locks conditional, etc. These tests cover the floor logic
in :class:`PolicyGate` and its end-to-end effect through
:class:`ActionDispatcher` when wired to a live :class:`DegradedState`.
"""

from __future__ import annotations

from kukiihome_core.dispatch import (
    ActionDispatcher,
    OccupancySnapshot,
    PolicyDecision,
    PolicyDisposition,
    PolicyGate,
    PreApproval,
    PreApprovalRegistry,
    ResidentPreferences,
)
from kukiihome_core.rules import ResolutionOutcome
from kukiihome_shared.health import DegradedState, FailureMode


def _svc(service: str) -> dict:
    return {"type": "ha_service_call", "service": service, "entity_id": "x.y"}


def _resolution(actions=()):
    return ResolutionOutcome(
        winning_rule_ids=("rule_1",),
        severity="warning",
        actions=tuple(actions),
        suppressed_rule_ids=(),
    )


# ─── PolicyGate floor ───────────────────────────────────────────────


def test_no_active_modes_leaves_base_decision():
    g = PolicyGate()
    d = g.evaluate(_svc("light.turn_on"))
    assert d.disposition == PolicyDisposition.auto


def test_ha_down_blocks_lights_that_were_auto():
    g = PolicyGate()
    d = g.evaluate(_svc("light.turn_on"), active_failure_modes=frozenset({FailureMode.F4_HA_DOWN}))
    assert d.disposition == PolicyDisposition.blocked
    assert "F4" in d.reason
    assert d.fallback_action is not None  # operator notify


def test_ha_down_blocks_even_a_gated_lock():
    g = PolicyGate()
    d = g.evaluate(_svc("lock.lock"), active_failure_modes=frozenset({FailureMode.F4_HA_DOWN}))
    assert d.disposition == PolicyDisposition.blocked


def test_internet_down_does_not_restrict():
    g = PolicyGate()
    d = g.evaluate(
        _svc("light.turn_on"), active_failure_modes=frozenset({FailureMode.F8_INTERNET_DOWN})
    )
    assert d.disposition == PolicyDisposition.auto


def test_vlm_down_allows_lights():
    g = PolicyGate()
    d = g.evaluate(_svc("light.turn_on"), active_failure_modes=frozenset({FailureMode.F7_VLM_DOWN}))
    assert d.disposition == PolicyDisposition.auto


def test_unmapped_service_unaffected_by_floor():
    g = PolicyGate()
    # ptz.slew has no action-class mapping -> floor leaves it (auto).
    d = g.evaluate(_svc("ptz.slew"), active_failure_modes=frozenset({FailureMode.F4_HA_DOWN}))
    assert d.disposition == PolicyDisposition.auto


def test_conditional_downgrades_auto_to_gated():
    g = PolicyGate()
    # Directly exercise the conditional path: a synthetic auto base for a
    # lock-class service under F7 (lock=conditional) -> gated.
    base = PolicyDecision(PolicyDisposition.auto, "synthetic_auto")
    out = g._apply_failure_floor(base, "lock.lock", frozenset({FailureMode.F7_VLM_DOWN}))
    assert out.disposition == PolicyDisposition.gated
    assert "conditional" in out.reason


def test_conditional_honors_pre_approval():
    reg = PreApprovalRegistry()
    reg.register(PreApproval(rule_id="r9", service="lock.lock"))
    g = PolicyGate(pre_approvals=reg)
    # lock.lock with a pre-approval is auto(pre_approved); F6 makes lock
    # conditional, but the rule pre-authorizes -> stays auto.
    d = g.evaluate(
        _svc("lock.lock"), active_failure_modes=frozenset({FailureMode.F6_GPU_SATURATED})
    )
    assert d.disposition == PolicyDisposition.auto
    assert d.pre_approval_rule_id == "r9"


# ─── End-to-end through ActionDispatcher + DegradedState ────────────


def test_dispatcher_blocks_device_action_when_ha_down():
    degraded = DegradedState()
    degraded.activate(FailureMode.F4_HA_DOWN)
    disp = ActionDispatcher(degraded_state=degraded)
    plan = disp.plan(
        event_id="e1",
        vlm_response={"criticality": "warning", "confidence": 0.85},
        resolution=_resolution(actions=(_svc("light.turn_on"),)),
        residents=[ResidentPreferences(resident_id="r1", quiet_hours=None)],
        occupancy=OccupancySnapshot(home=frozenset({"r1"})),
    )
    lights = [
        a
        for a in plan.actions
        if a.ha_service is not None and a.ha_service.service == "light.turn_on"
    ]
    assert lights == []  # blocked by the F4 safe-default floor
    assert plan.policy_blocks
    assert plan.policy_blocks[0].disposition == PolicyDisposition.blocked


def test_dispatcher_allows_device_action_when_healthy():
    disp = ActionDispatcher(degraded_state=DegradedState())  # nothing active
    plan = disp.plan(
        event_id="e2",
        vlm_response={"criticality": "warning", "confidence": 0.85},
        resolution=_resolution(actions=(_svc("light.turn_on"),)),
        residents=[ResidentPreferences(resident_id="r1", quiet_hours=None)],
        occupancy=OccupancySnapshot(home=frozenset({"r1"})),
    )
    lights = [
        a
        for a in plan.actions
        if a.ha_service is not None and a.ha_service.service == "light.turn_on"
    ]
    assert len(lights) == 1
    assert not plan.policy_blocks
