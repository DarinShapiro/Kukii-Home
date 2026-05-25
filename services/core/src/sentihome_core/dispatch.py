"""Action dispatch — turn rule firings into tiered actions.

Architecture: docs/architecture/15-alerting-and-actions.md
Epic 8 (#117). Wires together:

- :class:`TierRouter`       — criticality x confidence to Tier 0-4
- :class:`EscalationEngine` — timeouts, follow-ups, unanswered escalations
- :class:`QuietHours`       — per-resident quiet windows
- :class:`OccupancyRouter`  — who's home routing
- :class:`PolicyGate`       — auto-allowed / policy-gated / hard-blocked
- :class:`PreApprovalRegistry` — rule-based pre-approvals for gated actions
- :class:`RemediationRegistry` — limiting_factor + resources → env actions
- :class:`DeeperAssessmentLoop` — re-VLM after remediation
- :class:`ExplanationGenerator` — render alert "why" payload
- :class:`AckTracker`       — dismiss/confirm/forward feedback loop
- :class:`ActionDispatcher` — orchestrator that ties them together

The dispatcher reads the VLM decision + rule resolution outcome, walks the
policy gate, applies routing, and emits :class:`ActionEvent` messages on the
``actions.*`` subjects of the bus. Per §15, downstream HA-agent (Epic 9)
actually invokes HA services; this module produces the *plan*.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

import structlog
from sentihome_shared.generated.events.action_event import ActionEvent, ActionType, Tier

if TYPE_CHECKING:
    from sentihome_shared.bus import Bus

    from sentihome_core.rules import ResolutionOutcome

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Tier model
# ─────────────────────────────────────────────────────────────────────


TIER_ORDER: tuple[Tier, ...] = (
    Tier.tier_0_silent,
    Tier.tier_1_in_app,
    Tier.tier_2_push,
    Tier.tier_3_wake,
    Tier.tier_4_emergency,
)


def _bump(tier: Tier, levels: int = 1) -> Tier:
    """Move tier up by ``levels`` (capped at tier_4_emergency)."""
    idx = TIER_ORDER.index(tier)
    return TIER_ORDER[min(idx + levels, len(TIER_ORDER) - 1)]


# ─────────────────────────────────────────────────────────────────────
# Tier router (#119)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TierDecision:
    tier: Tier
    reason: str


class TierRouter:
    """Map VLM criticality + confidence + severity → Tier 0..4.

    Thresholds mirror §15:
        Tier 0: info,    conf < 0.70
        Tier 1: info/warning, 0.70 ≤ conf < 0.85
        Tier 2: warning/alert, 0.85 ≤ conf < 0.95
        Tier 3: alert,   0.92 ≤ conf < 0.98 OR severity=alert
        Tier 4: alert,   conf ≥ 0.98 OR urgent_alert flag
    """

    def route(
        self,
        *,
        criticality: str,
        confidence: float,
        severity: str = "info",
        urgent_alert: bool = False,
    ) -> TierDecision:
        if urgent_alert or confidence >= 0.98:
            return TierDecision(Tier.tier_4_emergency, "urgent_alert_or_max_confidence")
        if criticality == "alert" and (confidence >= 0.92 or severity == "alert"):
            return TierDecision(Tier.tier_3_wake, "alert_with_high_confidence")
        if confidence >= 0.85 and criticality in ("warning", "alert"):
            return TierDecision(Tier.tier_2_push, "warning_or_alert_pushable")
        if confidence >= 0.70:
            return TierDecision(Tier.tier_1_in_app, "moderate_confidence_in_app")
        return TierDecision(Tier.tier_0_silent, "low_confidence_silent_log")


# ─────────────────────────────────────────────────────────────────────
# Quiet hours + resident preferences (#121, #123)
# ─────────────────────────────────────────────────────────────────────


class ContactChannel(StrEnum):
    phone_push = "phone_push"
    phone_call = "phone_call"
    sms = "sms"
    in_app_only = "in_app_only"


@dataclass
class ResidentPreferences:
    """Per-resident DND + routing prefs (§15)."""

    resident_id: str
    quiet_hours: tuple[time, time] | None = (time(23, 0), time(7, 0))
    """``(start, end)`` local time; ``None`` disables quiet hours."""
    vacation_mode: bool = False
    """If True, escalate all tiers +1."""
    emergency_only: bool = False
    """If True, suppress Tier 2; allow Tier 3+ only."""
    preferred_contact: ContactChannel = ContactChannel.phone_push
    do_not_alert_for_rules: tuple[str, ...] = ()

    def in_quiet_hours(self, now: datetime) -> bool:
        if self.quiet_hours is None:
            return False
        start, end = self.quiet_hours
        cur = now.time()
        if start <= end:
            return start <= cur <= end
        return cur >= start or cur <= end


@dataclass
class QuietHours:
    """Apply per-resident quiet hours to a tier.

    Per §15 the tier itself is not lowered — sound/vibration are; here we
    surface that as a (tier, silent) tuple so the channel-specific dispatcher
    (push/TTS) can decide what to do.
    """

    def apply(
        self, *, tier: Tier, prefs: ResidentPreferences, now: datetime, force_audio: bool = False
    ) -> tuple[Tier, bool]:
        """Returns ``(effective_tier, silent_flag)``."""
        if not prefs.in_quiet_hours(now) or force_audio:
            return tier, False
        # In quiet hours: Tier 0/1 unchanged; Tier 2 → silent push;
        # Tier 3/4 keep audio.
        if tier in (Tier.tier_0_silent, Tier.tier_1_in_app):
            return tier, False
        if tier == Tier.tier_2_push:
            return tier, True
        return tier, False


# ─────────────────────────────────────────────────────────────────────
# Occupancy-aware routing (#121)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class OccupancySnapshot:
    """Who is home right now (sourced from HA in production)."""

    home: frozenset[str] = field(default_factory=frozenset)
    away: frozenset[str] = field(default_factory=frozenset)

    @property
    def anyone_home(self) -> bool:
        return bool(self.home)


@dataclass
class RouteTarget:
    resident_id: str
    tier: Tier
    silent: bool = False
    channel: ContactChannel = ContactChannel.phone_push


class OccupancyRouter:
    """Decide per-resident effective tier based on who's home (§15).

    - If no one home: bump alerts (criticality=alert) to Tier 3 immediately —
      pushes won't be seen.
    - If someone home: push, escalate later if unread.
    - Mixed: push to all; call to away residents only.
    """

    def route(
        self,
        *,
        base_tier: Tier,
        criticality: str,
        residents: list[ResidentPreferences],
        occupancy: OccupancySnapshot,
        now: datetime,
        quiet: QuietHours | None = None,
        force_audio: bool = False,
    ) -> list[RouteTarget]:
        quiet = quiet or QuietHours()
        targets: list[RouteTarget] = []
        for prefs in residents:
            tier = base_tier
            if prefs.vacation_mode:
                tier = _bump(tier, 1)
            if prefs.emergency_only and tier == Tier.tier_2_push:
                # Suppress push for emergency-only mode.
                continue
            if criticality == "alert" and not occupancy.anyone_home:
                tier = _bump(
                    tier, max(0, TIER_ORDER.index(Tier.tier_3_wake) - TIER_ORDER.index(tier))
                )
            # Mixed-occupancy nudge: away residents on alert get a call.
            if (
                criticality == "alert"
                and occupancy.anyone_home
                and prefs.resident_id in occupancy.away
                and tier == Tier.tier_2_push
            ):
                tier = Tier.tier_3_wake

            eff_tier, silent = quiet.apply(tier=tier, prefs=prefs, now=now, force_audio=force_audio)
            targets.append(
                RouteTarget(
                    resident_id=prefs.resident_id,
                    tier=eff_tier,
                    silent=silent,
                    channel=prefs.preferred_contact,
                )
            )
        return targets


# ─────────────────────────────────────────────────────────────────────
# Last-responder bias mitigation (#122)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class AlertReview:
    """Tracks who's actively reviewing an alert + when they started."""

    alert_id: str
    reviewing: dict[str, datetime] = field(default_factory=dict)
    resolved: bool = False
    resolved_by: str | None = None


class LastResponderTracker:
    """Per §15: when multiple residents alerted, explicit delegation.

    Workflow:
    - alert sent to N residents
    - resident_1 opens → ``mark_reviewing`` → others see "X is reviewing"
    - resident_1 dismisses → ``mark_resolved`` → no follow-up
    - timeout without resolution → ``escalate_to_others`` returns
      the residents who haven't responded so the caller can re-alert them.
    """

    def __init__(self, *, review_timeout: timedelta = timedelta(minutes=5)) -> None:
        self._alerts: dict[str, AlertReview] = {}
        self._timeout = review_timeout

    def open(self, alert_id: str) -> AlertReview:
        review = AlertReview(alert_id=alert_id)
        self._alerts[alert_id] = review
        return review

    def mark_reviewing(self, alert_id: str, resident_id: str, *, now: datetime) -> None:
        review = self._alerts.setdefault(alert_id, AlertReview(alert_id=alert_id))
        review.reviewing[resident_id] = now

    def mark_resolved(self, alert_id: str, resident_id: str) -> None:
        review = self._alerts.get(alert_id)
        if review is None:
            return
        review.resolved = True
        review.resolved_by = resident_id

    def escalate_to_others(
        self, alert_id: str, all_residents: list[str], *, now: datetime
    ) -> list[str]:
        review = self._alerts.get(alert_id)
        if review is None or review.resolved:
            return []
        # If anyone started reviewing > timeout ago and didn't resolve, page others.
        stale = any(now - started > self._timeout for started in review.reviewing.values())
        if not stale and review.reviewing:
            return []
        return [r for r in all_residents if r not in review.reviewing]


# ─────────────────────────────────────────────────────────────────────
# Autonomous action policy (#127, #128)
# ─────────────────────────────────────────────────────────────────────


class PolicyDisposition(StrEnum):
    auto = "auto"
    gated = "gated"
    blocked = "blocked"


# Per §15: auto-allowed device-control actions.
AUTO_ALLOWED_SERVICES: frozenset[str] = frozenset(
    {
        "light.turn_on",
        "light.turn_off",
        "switch.turn_on",
        "switch.turn_off",
        "media_player.play_media",
        "tts.speak",
        "scene.turn_on",
        "ptz.slew",
        "ptz.profile_switch",
    }
)

# Policy-gated: require pre-approval rule or ask() confirmation.
POLICY_GATED_SERVICES: frozenset[str] = frozenset(
    {
        "lock.lock",
        "lock.unlock",
        "alarm_control_panel.alarm_arm_home",
        "alarm_control_panel.alarm_arm_away",
        "alarm_control_panel.alarm_arm_night",
    }
)

# Hard-blocked: cannot run without out-of-band human action.
HARD_BLOCKED_SERVICES: frozenset[str] = frozenset(
    {
        "alarm_control_panel.alarm_disarm",
        "siren.turn_on",
        "cover.open_cover",  # garage door safety
        "lock.open",  # bulk unlock pattern
    }
)


@dataclass
class PolicyDecision:
    disposition: PolicyDisposition
    reason: str
    fallback_action: dict[str, Any] | None = None
    pre_approval_rule_id: str | None = None


@dataclass
class PreApproval:
    """A pre-approval entry: rule X authorizes service Y under conditions."""

    rule_id: str
    service: str
    conditions_satisfied: Callable[[dict[str, Any]], bool] = lambda _ctx: True


class PreApprovalRegistry:
    """Registry of pre-approval rules per §15."""

    def __init__(self) -> None:
        self._by_service: dict[str, list[PreApproval]] = defaultdict(list)

    def register(self, pre_approval: PreApproval) -> None:
        self._by_service[pre_approval.service].append(pre_approval)

    def find(self, service: str, ctx: dict[str, Any]) -> PreApproval | None:
        for pa in self._by_service.get(service, ()):
            if pa.conditions_satisfied(ctx):
                return pa
        return None


class PolicyGate:
    """Enforces autonomous action policy."""

    def __init__(self, pre_approvals: PreApprovalRegistry | None = None) -> None:
        self._pre = pre_approvals or PreApprovalRegistry()

    @property
    def pre_approvals(self) -> PreApprovalRegistry:
        return self._pre

    def evaluate(
        self, action: dict[str, Any], *, ctx: dict[str, Any] | None = None
    ) -> PolicyDecision:
        """Decide whether an action is auto, gated, or blocked.

        ``action`` is the rule-engine action dict; for ``ha_service_call``
        actions the gate inspects the requested service.
        """
        ctx = ctx or {}
        action_type = action.get("type")

        if action_type in (
            "notify",
            "speak",
            "ask",
            "memory_write",
            "session_open",
            "session_close",
        ):
            return PolicyDecision(PolicyDisposition.auto, "non_device_action")

        if action_type == "escalate":
            return PolicyDecision(PolicyDisposition.auto, "escalation_directive")

        if action_type != "ha_service_call":
            return PolicyDecision(PolicyDisposition.auto, "unknown_treated_as_auto")

        service = action.get("service", "")
        if service in HARD_BLOCKED_SERVICES:
            return PolicyDecision(
                PolicyDisposition.blocked,
                f"{service}_requires_explicit_human_action",
                fallback_action={
                    "type": "notify",
                    "targets": ["all_residents"],
                    "message_template": (
                        f"Action {service} was suggested but is policy-blocked; "
                        "manual intervention required."
                    ),
                },
            )
        if service in POLICY_GATED_SERVICES:
            pa = self._pre.find(service, ctx)
            if pa is not None:
                return PolicyDecision(
                    PolicyDisposition.auto,
                    "pre_approved",
                    pre_approval_rule_id=pa.rule_id,
                )
            return PolicyDecision(PolicyDisposition.gated, "needs_user_confirmation")
        # Default auto for unknown HA services that aren't gated/blocked.
        return PolicyDecision(PolicyDisposition.auto, "service_not_restricted")


# ─────────────────────────────────────────────────────────────────────
# Remediation + deeper-assessment loop (#131, #132)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class RemediationAction:
    """An environmental action proposed for a confidence-limiting factor."""

    action: dict[str, Any]
    expected_improvement: str = ""


class RemediationRegistry:
    """Map limiting factors x area resources to environmental actions (§06)."""

    def __init__(self) -> None:
        # (limiting_factor, resource_kind) → builder
        self._mapping: dict[tuple[str, str], Callable[[str], RemediationAction]] = {}
        self._install_defaults()

    def _install_defaults(self) -> None:
        self._mapping[("low_light", "light")] = lambda entity_id: RemediationAction(
            action={
                "type": "ha_service_call",
                "service": "light.turn_on",
                "entity_id": entity_id,
            },
            expected_improvement="illumination",
        )
        self._mapping[("subject_too_small", "ptz")] = lambda entity_id: RemediationAction(
            action={
                "type": "ha_service_call",
                "service": "ptz.slew",
                "entity_id": entity_id,
                "data": {"zoom": 2.0},
            },
            expected_improvement="zoom_to_subject",
        )
        self._mapping[("subject_partially_occluded", "ptz")] = lambda entity_id: RemediationAction(
            action={
                "type": "ha_service_call",
                "service": "ptz.profile_switch",
                "entity_id": entity_id,
                "data": {"profile": "wide"},
            },
            expected_improvement="alternative_angle",
        )
        self._mapping[("camera_obstructed", "ptz")] = lambda entity_id: RemediationAction(
            action={
                "type": "ha_service_call",
                "service": "ptz.profile_switch",
                "entity_id": entity_id,
                "data": {"profile": "secondary"},
            },
            expected_improvement="alternative_camera_view",
        )

    def register(
        self,
        limiting_factor: str,
        resource_kind: str,
        builder: Callable[[str], RemediationAction],
    ) -> None:
        self._mapping[(limiting_factor, resource_kind)] = builder

    def propose(
        self, limiting_factors: list[str], area_resources: dict[str, list[str]]
    ) -> list[RemediationAction]:
        """For each limiting factor, find a matching resource and produce an action."""
        proposed: list[RemediationAction] = []
        for factor in limiting_factors:
            for kind, entities in area_resources.items():
                builder = self._mapping.get((factor, kind))
                if builder is None or not entities:
                    continue
                proposed.append(builder(entities[0]))
                break
        return proposed


@dataclass
class DeeperAssessmentResult:
    remediations: list[RemediationAction]
    second_response: dict[str, Any] | None
    triggered: bool


class DeeperAssessmentLoop:
    """After remediation, schedule a follow-up VLM call (§06, §15).

    The actual VLM call is injected so this module stays unit-testable; the
    real wiring connects to ``services/vlm-router``.
    """

    def __init__(
        self,
        registry: RemediationRegistry,
        *,
        vlm_call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        wait_seconds: float = 2.0,
        confidence_threshold: float = 0.85,
    ) -> None:
        self._registry = registry
        self._vlm_call = vlm_call
        self._wait = wait_seconds
        self._threshold = confidence_threshold

    async def maybe_run(
        self,
        *,
        vlm_response: dict[str, Any],
        area_resources: dict[str, list[str]],
        rebuild_request: Callable[[], dict[str, Any]],
    ) -> DeeperAssessmentResult:
        limiting_factors = list(vlm_response.get("limiting_factors") or [])
        confidence = float(vlm_response.get("confidence") or 0.0)
        wants_deeper = bool(vlm_response.get("deeper_assessment")) or (
            limiting_factors and confidence < self._threshold
        )
        if not wants_deeper:
            return DeeperAssessmentResult(remediations=[], second_response=None, triggered=False)

        remediations = self._registry.propose(limiting_factors, area_resources)
        if not remediations or self._vlm_call is None:
            return DeeperAssessmentResult(
                remediations=remediations, second_response=None, triggered=bool(remediations)
            )
        # Give the environment a moment to settle (lights warm up, PTZ slews).
        await asyncio.sleep(self._wait)
        second = await self._vlm_call(rebuild_request())
        return DeeperAssessmentResult(
            remediations=remediations, second_response=second, triggered=True
        )


# ─────────────────────────────────────────────────────────────────────
# Explanation generator (#129)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AlertExplanation:
    headline: str
    why: tuple[str, ...]
    rules_fired: tuple[str, ...]
    confidence: float
    evidence_ref: str | None
    edit_paths: tuple[dict[str, str], ...]


class ExplanationGenerator:
    """Render the "why" payload that ships with every alert (§15)."""

    def render(
        self,
        *,
        criticality: str,
        confidence: float,
        rules_fired: list[str],
        identified_actors: list[dict[str, Any]] | None = None,
        limiting_factors: list[str] | None = None,
        vlm_explanation: str = "",
        evidence_ref: str | None = None,
        subject_label: str = "subject",
        location_label: str | None = None,
    ) -> AlertExplanation:
        loc = f" at {location_label}" if location_label else ""
        headline = f"{criticality.capitalize()}: {subject_label}{loc}"
        why: list[str] = []
        if vlm_explanation:
            why.append(vlm_explanation)
        for actor in identified_actors or []:
            name = actor.get("name") or actor.get("actor_id", "unknown")
            ac = actor.get("confidence")
            why.append(f"Identified actor: {name}" + (f" ({ac:.0%})" if ac is not None else ""))
        for factor in limiting_factors or []:
            why.append(f"Limiting factor: {factor.replace('_', ' ')}")
        if not why:
            why.append("No additional context available.")
        edit_paths = tuple({"rule_id": rid, "path": f"/rules/{rid}/edit"} for rid in rules_fired)
        return AlertExplanation(
            headline=headline,
            why=tuple(why),
            rules_fired=tuple(rules_fired),
            confidence=confidence,
            evidence_ref=evidence_ref,
            edit_paths=edit_paths,
        )


# ─────────────────────────────────────────────────────────────────────
# Alert acknowledgment + feedback loop (#130)
# ─────────────────────────────────────────────────────────────────────


class FeedbackType(StrEnum):
    correct = "correct"
    false_alarm = "false_alarm"
    not_sure = "not_sure"
    edit_rule = "edit_rule"


@dataclass
class AckRecord:
    alert_id: str
    rule_ids: tuple[str, ...]
    issued_at: datetime
    responded_at: datetime | None = None
    feedback: FeedbackType | None = None
    resident_id: str | None = None
    dwell_seconds: float | None = None

    @property
    def response_latency(self) -> timedelta | None:
        if self.responded_at is None:
            return None
        return self.responded_at - self.issued_at


class AckTracker:
    """Records user feedback on alerts; surface for §10.5 optimization."""

    def __init__(self) -> None:
        self._records: dict[str, AckRecord] = {}

    def issue(self, alert_id: str, *, rule_ids: list[str], now: datetime) -> AckRecord:
        rec = AckRecord(alert_id=alert_id, rule_ids=tuple(rule_ids), issued_at=now)
        self._records[alert_id] = rec
        return rec

    def record(
        self,
        alert_id: str,
        *,
        feedback: FeedbackType,
        resident_id: str,
        now: datetime,
        dwell_seconds: float | None = None,
    ) -> AckRecord | None:
        rec = self._records.get(alert_id)
        if rec is None:
            return None
        rec.feedback = feedback
        rec.resident_id = resident_id
        rec.responded_at = now
        rec.dwell_seconds = dwell_seconds
        return rec

    def get(self, alert_id: str) -> AckRecord | None:
        return self._records.get(alert_id)

    def aggregate_rule_stats(self) -> dict[str, dict[str, int]]:
        """Counts of feedback types per rule (for §10.5 calibration signals)."""
        stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for rec in self._records.values():
            if rec.feedback is None:
                continue
            for rid in rec.rule_ids:
                stats[rid][rec.feedback.value] += 1
        return {k: dict(v) for k, v in stats.items()}


# ─────────────────────────────────────────────────────────────────────
# Tier escalation engine (#120)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class EscalationTimer:
    alert_id: str
    current_tier: Tier
    next_at: datetime
    levels: int = 1
    cancelled: bool = False


class EscalationEngine:
    """Schedules tier escalations on unanswered alerts.

    Timeouts mirror §15:
      Tier 1 → Tier 2+ after 5 min (rule needs ``escalate_on_timeout``)
      Tier 2 → Tier 3+ after 60 s if no-one home
      Tier 3 → Tier 4 after 120 s with no ack

    This engine is *passive*: it returns scheduled timers; the caller (the
    dispatcher) actually waits on them and invokes the next-tier action.
    """

    DEFAULT_TIMEOUTS: ClassVar[dict[Tier, timedelta]] = {
        Tier.tier_1_in_app: timedelta(minutes=5),
        Tier.tier_2_push: timedelta(seconds=60),
        Tier.tier_3_wake: timedelta(seconds=120),
    }

    def __init__(self, timeouts: dict[Tier, timedelta] | None = None) -> None:
        self._timeouts = timeouts or dict(self.DEFAULT_TIMEOUTS)
        self._active: dict[str, EscalationTimer] = {}

    def schedule(
        self,
        alert_id: str,
        *,
        tier: Tier,
        now: datetime,
        escalate_on_timeout: bool = False,
        no_one_home: bool = False,
    ) -> EscalationTimer | None:
        # Tier 0 never escalates from inactivity; Tier 4 already at top.
        if tier == Tier.tier_0_silent or tier == Tier.tier_4_emergency:
            return None
        # Tier 1 only escalates if rule has the opt-in flag.
        if tier == Tier.tier_1_in_app and not escalate_on_timeout:
            return None
        # Tier 2 only auto-escalates when no-one's home (§15).
        if tier == Tier.tier_2_push and not no_one_home and not escalate_on_timeout:
            return None
        delta = self._timeouts.get(tier)
        if delta is None:
            return None
        timer = EscalationTimer(alert_id=alert_id, current_tier=tier, next_at=now + delta)
        self._active[alert_id] = timer
        return timer

    def cancel(self, alert_id: str) -> None:
        timer = self._active.get(alert_id)
        if timer is not None:
            timer.cancelled = True

    def follow_up_detected(self, alert_id: str, *, now: datetime) -> EscalationTimer | None:
        """A repeat detection of the same subject — accelerate escalation."""
        timer = self._active.get(alert_id)
        if timer is None or timer.cancelled:
            return None
        timer.next_at = now  # fire immediately
        timer.levels = 2
        return timer

    def tick(self, *, now: datetime) -> list[EscalationTimer]:
        """Return timers that have fired since the last tick."""
        fired: list[EscalationTimer] = []
        for timer in list(self._active.values()):
            if timer.cancelled:
                self._active.pop(timer.alert_id, None)
                continue
            if timer.next_at <= now:
                fired.append(timer)
                self._active.pop(timer.alert_id, None)
        return fired

    def active(self) -> list[EscalationTimer]:
        return [t for t in self._active.values() if not t.cancelled]


# ─────────────────────────────────────────────────────────────────────
# Action dispatcher (#118) — orchestrates everything above
# ─────────────────────────────────────────────────────────────────────


@dataclass
class DispatchPlan:
    """The full plan produced from a (resolved rules, vlm_response) input."""

    alert_id: str
    event_id: str
    tier_decision: TierDecision
    targets: list[RouteTarget]
    actions: list[ActionEvent]
    policy_blocks: list[PolicyDecision]
    explanation: AlertExplanation
    escalation: EscalationTimer | None
    remediations: list[RemediationAction]


def _new_action_id() -> str:
    return f"act_{uuid.uuid4().hex[:16]}"


def _new_alert_id() -> str:
    return f"alert_{uuid.uuid4().hex[:12]}"


class ActionDispatcher:
    """Build a :class:`DispatchPlan` and (optionally) publish to the bus.

    Pure-function ``plan()`` is unit-test friendly; ``dispatch()`` is the
    thin wrapper that also publishes to ``actions.*``.
    """

    def __init__(
        self,
        *,
        bus: Bus | None = None,
        tier_router: TierRouter | None = None,
        quiet: QuietHours | None = None,
        occupancy_router: OccupancyRouter | None = None,
        policy: PolicyGate | None = None,
        remediation: RemediationRegistry | None = None,
        explanation: ExplanationGenerator | None = None,
        escalation: EscalationEngine | None = None,
        ack: AckTracker | None = None,
    ) -> None:
        self._bus = bus
        self._tier_router = tier_router or TierRouter()
        self._quiet = quiet or QuietHours()
        self._occupancy = occupancy_router or OccupancyRouter()
        self._policy = policy or PolicyGate()
        self._remediation = remediation or RemediationRegistry()
        self._explanation = explanation or ExplanationGenerator()
        self._escalation = escalation or EscalationEngine()
        self._ack = ack or AckTracker()

    @property
    def escalation_engine(self) -> EscalationEngine:
        return self._escalation

    @property
    def policy_gate(self) -> PolicyGate:
        return self._policy

    @property
    def ack_tracker(self) -> AckTracker:
        return self._ack

    @property
    def remediation_registry(self) -> RemediationRegistry:
        return self._remediation

    def plan(
        self,
        *,
        event_id: str,
        vlm_response: dict[str, Any],
        resolution: ResolutionOutcome,
        residents: list[ResidentPreferences],
        occupancy: OccupancySnapshot,
        evidence_ref: str | None = None,
        subject_label: str = "subject",
        location_label: str | None = None,
        urgent_alert: bool = False,
        escalate_on_timeout: bool = False,
        force_audio: bool = False,
        trace_id: str | None = None,
        now: datetime | None = None,
    ) -> DispatchPlan:
        now = now or datetime.now(UTC)
        alert_id = _new_alert_id()

        criticality = str(vlm_response.get("criticality") or "info")
        confidence = float(vlm_response.get("confidence") or 0.0)
        tier_dec = self._tier_router.route(
            criticality=criticality,
            confidence=confidence,
            severity=resolution.severity,
            urgent_alert=urgent_alert,
        )

        targets = self._occupancy.route(
            base_tier=tier_dec.tier,
            criticality=criticality,
            residents=residents,
            occupancy=occupancy,
            now=now,
            quiet=self._quiet,
            force_audio=force_audio,
        )

        action_events: list[ActionEvent] = []
        policy_blocks: list[PolicyDecision] = []

        # Notification actions (per resident target).
        for tgt in targets:
            if tgt.tier == Tier.tier_0_silent:
                continue
            action_type = ActionType.notify_push
            if tgt.tier == Tier.tier_3_wake:
                # Wake households also speak via TTS.
                action_events.append(
                    ActionEvent(
                        action_id=_new_action_id(),
                        event_id=event_id,
                        trace_id=trace_id,
                        action_type=ActionType.notify_speak,
                        tier=tgt.tier,
                        targets=[tgt.resident_id],
                        rules_fired=list(resolution.winning_rule_ids),
                    )
                )
            action_events.append(
                ActionEvent(
                    action_id=_new_action_id(),
                    event_id=event_id,
                    trace_id=trace_id,
                    action_type=action_type,
                    tier=tgt.tier,
                    targets=[tgt.resident_id],
                    rules_fired=list(resolution.winning_rule_ids),
                    evidence_ref=evidence_ref,
                )
            )

        # Device actions from the resolved rule set.
        for raw_action in resolution.actions:
            if not raw_action:
                continue
            decision = self._policy.evaluate(raw_action, ctx={"event_id": event_id})
            if decision.disposition == PolicyDisposition.blocked:
                policy_blocks.append(decision)
                if decision.fallback_action:
                    action_events.append(
                        self._notify_from_dict(
                            decision.fallback_action,
                            event_id=event_id,
                            trace_id=trace_id,
                            rules_fired=list(resolution.winning_rule_ids),
                        )
                    )
                continue
            if raw_action.get("type") == "ha_service_call":
                action_events.append(
                    ActionEvent(
                        action_id=_new_action_id(),
                        event_id=event_id,
                        trace_id=trace_id,
                        action_type=ActionType.ha_service_call,
                        tier=tier_dec.tier,
                        ha_service={
                            "service": raw_action.get("service"),
                            "entity_id": raw_action.get("entity_id"),
                            "data": raw_action.get("data"),
                        },
                        rules_fired=list(resolution.winning_rule_ids),
                        policy_gate_required=(decision.disposition == PolicyDisposition.gated),
                    )
                )
                if decision.disposition == PolicyDisposition.gated:
                    # Pose an ask() to confirm gated action.
                    action_events.append(
                        ActionEvent(
                            action_id=_new_action_id(),
                            event_id=event_id,
                            trace_id=trace_id,
                            action_type=ActionType.ask,
                            tier=Tier.tier_2_push,
                            targets=[t.resident_id for t in targets] or None,
                            message=(
                                f"Allow action {raw_action.get('service')} on "
                                f"{raw_action.get('entity_id')}?"
                            ),
                            rules_fired=list(resolution.winning_rule_ids),
                        )
                    )
            elif raw_action.get("type") == "speak":
                action_events.append(
                    ActionEvent(
                        action_id=_new_action_id(),
                        event_id=event_id,
                        trace_id=trace_id,
                        action_type=ActionType.notify_speak,
                        tier=tier_dec.tier,
                        message=raw_action.get("message_template") or None,
                        rules_fired=list(resolution.winning_rule_ids),
                    )
                )

        explanation = self._explanation.render(
            criticality=criticality,
            confidence=confidence,
            rules_fired=list(resolution.winning_rule_ids),
            identified_actors=list(vlm_response.get("identified_actors") or []),
            limiting_factors=list(vlm_response.get("limiting_factors") or []),
            vlm_explanation=str(vlm_response.get("explanation") or ""),
            evidence_ref=evidence_ref,
            subject_label=subject_label,
            location_label=location_label,
        )

        escalation_timer = self._escalation.schedule(
            alert_id,
            tier=tier_dec.tier,
            now=now,
            escalate_on_timeout=escalate_on_timeout,
            no_one_home=not occupancy.anyone_home,
        )

        self._ack.issue(alert_id, rule_ids=list(resolution.winning_rule_ids), now=now)

        return DispatchPlan(
            alert_id=alert_id,
            event_id=event_id,
            tier_decision=tier_dec,
            targets=targets,
            actions=action_events,
            policy_blocks=policy_blocks,
            explanation=explanation,
            escalation=escalation_timer,
            remediations=[],
        )

    @staticmethod
    def _notify_from_dict(
        d: dict[str, Any],
        *,
        event_id: str,
        trace_id: str | None,
        rules_fired: list[str],
    ) -> ActionEvent:
        return ActionEvent(
            action_id=_new_action_id(),
            event_id=event_id,
            trace_id=trace_id,
            action_type=ActionType.notify_push,
            tier=Tier.tier_2_push,
            targets=list(d.get("targets") or []),
            message=d.get("message_template") or d.get("message"),
            rules_fired=rules_fired,
        )

    async def dispatch(self, plan: DispatchPlan) -> None:
        """Publish all actions on the bus (no-op if no bus configured)."""
        if self._bus is None:
            return
        for action in plan.actions:
            subject = f"actions.{action.action_type.value}"
            await self._bus.publish(subject, action)
