"""Rule engine — condition evaluation, conflict resolution, NL parsing.

Architecture: docs/architecture/10-rule-schema-and-retrieval.md

The data model (RuleRecord) and hybrid retrieval (MemoryStore.retrieve_rules)
live in services/memory (Epic 6). This module provides the runtime evaluation
+ conflict resolution + NL-driven CRUD on top.

Key types:
- :class:`RuleEvaluator` — evaluates conditions against an event context
- :class:`ConflictResolver` — applies scope specificity + severity hierarchy
- :class:`RuleParser` — LLM-backed NL→structured rule (stub for v1; the
  real LLM call lands when conversational UX wires up)
- :data:`DEFAULT_RULE_PACK` — Tier-1 safety rules shipped out of the box
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from sentihome_memory.models import RuleRecord

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Scope priority + severity hierarchy
# ─────────────────────────────────────────────────────────────────────


# Lower number = higher priority (more specific scope)
SCOPE_PRIORITY: dict[str, int] = {
    "zone": 0,
    "camera": 1,
    "area": 2,
    "journey": 3,
    "composite": 4,
    "global": 5,
}

# Higher number = higher severity (max wins on conflict)
SEVERITY_RANK: dict[str, int] = {
    "info": 0,
    "warning": 1,
    "alert": 2,
}


# ─────────────────────────────────────────────────────────────────────
# Event context for rule evaluation
# ─────────────────────────────────────────────────────────────────────


@dataclass
class RuleEvalContext:
    """Per-event data needed to evaluate rules.

    Built by the triage worker from the EnrichedEvent + world state snapshot
    from HA. Passed into RuleEvaluator.evaluate() per rule.
    """

    event_id: str
    camera_id: str
    area_id: str | None
    zone_id: str | None
    subject_type: str | None
    """person | pet | vehicle | object | None"""
    subject_known: str | None
    """known | unknown | None (when no identity resolved)"""
    subject_actor_id: str | None
    detections: tuple[str, ...]
    """Detection class labels present in the event."""
    contexts_active: tuple[str, ...]
    """Names of active SituationalContexts (e.g. "delivery_expected")."""
    now: datetime
    """Event timestamp; used for temporal condition checks."""
    confidence: float
    """VLM/detection confidence for the event subject."""


# ─────────────────────────────────────────────────────────────────────
# RuleEvaluator
# ─────────────────────────────────────────────────────────────────────


class RuleEvaluator:
    """Evaluates a rule against a ``RuleEvalContext``.

    Returns True if the rule should fire — all conditions match, temporal
    window is open, confidence meets the rule's threshold.
    """

    def evaluate(self, rule: RuleRecord, ctx: RuleEvalContext) -> bool:
        """Return True if the rule fires on this context."""
        if rule.deleted_at is not None:
            return False
        if rule.suppress_until is not None and rule.suppress_until > ctx.now:
            return False

        # Confidence gate
        if ctx.confidence < (rule.confidence_required or 0.0):
            return False

        # Temporal conditions
        if not self._check_temporal(rule.temporal or {}, ctx.now):
            return False

        # Subject + location conditions
        conditions = rule.conditions or {}
        if not self._check_conditions(conditions, ctx):
            return False

        return True

    def _check_temporal(self, temporal: dict[str, Any], now: datetime) -> bool:
        # active_hours: "HH:MM-HH:MM" string
        if hours := temporal.get("active_hours"):
            if not _within_hours(hours, now):
                return False

        # active_days: list of weekday names ["Mon", "Tue", ...]
        if days := temporal.get("active_days"):
            day_name = now.strftime("%a")  # Mon, Tue, ...
            if day_name not in days:
                return False

        # exclusions: list of "YYYY-MM-DD HH:MM-HH:MM" strings
        for exclusion in temporal.get("exclusions") or []:
            if _within_exclusion(exclusion, now):
                return False

        return True

    def _check_conditions(self, conditions: dict[str, Any], ctx: RuleEvalContext) -> bool:
        # subject_type filter
        st = conditions.get("subject_type")
        if st is not None and ctx.subject_type != st:
            return False

        # subject_known filter ("known" | "unknown" | specific actor)
        sk = conditions.get("subject_known")
        if sk is not None:
            if sk == "known" and ctx.subject_known != "known":
                return False
            if sk == "unknown" and ctx.subject_known != "unknown":
                return False
            if sk not in ("known", "unknown") and ctx.subject_actor_id != sk:
                # Specific actor required but not present
                return False

        # location filter (area_id or zone_id)
        loc = conditions.get("location")
        if loc is not None:
            if ctx.area_id != loc and ctx.zone_id != loc:
                return False

        # detections_required: all required labels must be present
        for required in conditions.get("detections_required") or []:
            if required not in ctx.detections:
                return False

        # exclude_if_detected: any forbidden actor cancels firing
        for forbidden in conditions.get("exclude_if_detected") or []:
            if forbidden in ctx.detections:
                return False

        # context_required: all required SituationalContexts must be active
        for ctx_name in conditions.get("context_required") or []:
            if ctx_name not in ctx.contexts_active:
                return False

        return True


def _within_hours(hours: str, now: datetime) -> bool:
    """Check ``hours`` like "08:00-22:00" against ``now``."""
    try:
        start_str, end_str = hours.split("-")
        start = _parse_time(start_str.strip())
        end = _parse_time(end_str.strip())
    except (ValueError, AttributeError):
        logger.warning("rules.bad_temporal_hours", hours=hours)
        return True  # Don't block firing on malformed config

    current = now.time()
    if start <= end:
        return start <= current <= end
    # Wraps midnight
    return current >= start or current <= end


def _parse_time(s: str) -> time:
    """Parse 'HH:MM' to a time."""
    h, m = s.split(":")
    return time(hour=int(h), minute=int(m))


def _within_exclusion(exclusion: str, now: datetime) -> bool:
    """Check '2026-05-23 09:00-10:30' against now."""
    try:
        date_part, hours_part = exclusion.split(" ", 1)
        excl_date = datetime.strptime(date_part, "%Y-%m-%d").date()
    except (ValueError, IndexError):
        return False
    if now.date() != excl_date:
        return False
    return _within_hours(hours_part, now)


# ─────────────────────────────────────────────────────────────────────
# ConflictResolver
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolutionOutcome:
    """The resolved set of rule firings + their composite actions."""

    winning_rule_ids: tuple[str, ...]
    """Rule IDs that survived conflict resolution."""

    severity: str
    """Highest severity among winners."""

    actions: tuple[dict[str, Any], ...]
    """De-duplicated union of actions across winners."""

    suppressed_rule_ids: tuple[str, ...]
    """Rule IDs that lost conflict resolution."""


class ConflictResolver:
    """Resolves conflicts among firing rules (§10).

    Algorithm:
    1. Group firing rules by scope specificity (most specific scope wins).
    2. Within the most-specific scope group, take all rules — they aren't
       mutually exclusive, they compose.
    3. Suppression rules from equal-or-higher scope override others.
    4. Severity = max() across surviving rules.
    5. Actions = de-duplicated union (notify targets union, device actions additive).
    """

    def resolve(self, firing: list[RuleRecord]) -> ResolutionOutcome:
        if not firing:
            return ResolutionOutcome(
                winning_rule_ids=(),
                severity="info",
                actions=(),
                suppressed_rule_ids=(),
            )

        # Bucket by scope priority
        most_specific = min(SCOPE_PRIORITY.get(r.scope, 99) for r in firing)
        winners = [r for r in firing if SCOPE_PRIORITY.get(r.scope, 99) == most_specific]
        losers = [r for r in firing if SCOPE_PRIORITY.get(r.scope, 99) != most_specific]

        # Apply suppression: a winning rule whose actions contain {type: suppress}
        # cancels other winners.
        suppressors = [r for r in winners if _has_suppress_action(r)]
        if suppressors:
            suppressed_ids = {target for r in suppressors for target in _suppress_targets(r)}
            losers.extend([r for r in winners if r.rule_id in suppressed_ids])
            winners = [r for r in winners if r.rule_id not in suppressed_ids]

        # Severity = max
        severity = "info"
        for r in winners:
            if SEVERITY_RANK.get(r.severity, 0) > SEVERITY_RANK.get(severity, 0):
                severity = r.severity

        # Action union (deduplicate by (type, targets/device))
        actions = _merge_actions([a for r in winners for a in (r.actions or [])])

        return ResolutionOutcome(
            winning_rule_ids=tuple(r.rule_id for r in winners),
            severity=severity,
            actions=tuple(actions),
            suppressed_rule_ids=tuple(r.rule_id for r in losers),
        )


def _has_suppress_action(rule: RuleRecord) -> bool:
    return any((a or {}).get("type") == "suppress" for a in (rule.actions or []))


def _suppress_targets(rule: RuleRecord) -> list[str]:
    targets: list[str] = []
    for action in rule.actions or []:
        if (action or {}).get("type") == "suppress":
            tid = action.get("target_rule_id")
            if tid:
                targets.append(tid)
    return targets


def _merge_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De-duplicate (type, targets/device) tuples."""
    seen: set[tuple[Any, ...]] = set()
    merged: list[dict[str, Any]] = []
    for action in actions:
        if not action:
            continue
        key = (
            action.get("type"),
            tuple(sorted(action.get("targets") or [])) if action.get("targets") else None,
            action.get("device") or action.get("target_rule_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(action)
    return merged


# ─────────────────────────────────────────────────────────────────────
# Default rule pack
# ─────────────────────────────────────────────────────────────────────


DEFAULT_RULE_PACK: list[dict[str, Any]] = [
    {
        "rule_id": "default_smoke_alarm",
        "text": "Tier-1 safety: smoke alarm fires immediate alert",
        "scope": "global",
        "scope_ref": None,
        "severity": "alert",
        "conditions": {"detections_required": ["smoke_alarm"]},
        "actions": [
            {"type": "notify", "targets": ["all_residents"]},
            {"type": "escalate", "tier": 3},
        ],
        "confidence_required": 0.9,
        "created_by": "system",
    },
    {
        "rule_id": "default_co_alarm",
        "text": "Tier-1 safety: CO alarm fires immediate alert",
        "scope": "global",
        "scope_ref": None,
        "severity": "alert",
        "conditions": {"detections_required": ["co_alarm"]},
        "actions": [
            {"type": "notify", "targets": ["all_residents"]},
            {"type": "escalate", "tier": 3},
        ],
        "confidence_required": 0.9,
        "created_by": "system",
    },
    {
        "rule_id": "default_flood_alarm",
        "text": "Tier-1 safety: flood alarm fires alert",
        "scope": "global",
        "scope_ref": None,
        "severity": "alert",
        "conditions": {"detections_required": ["flood_alarm"]},
        "actions": [{"type": "notify", "targets": ["all_residents"]}],
        "confidence_required": 0.85,
        "created_by": "system",
    },
    {
        "rule_id": "default_package_delivery",
        "text": "Package delivery confirmation",
        "scope": "area",
        "scope_ref": "front_door",
        "severity": "info",
        "conditions": {"detections_required": ["package"]},
        "actions": [
            {"type": "notify", "targets": ["resident_1"], "message_template": "Package delivered"}
        ],
        "confidence_required": 0.7,
        "created_by": "system",
    },
    {
        "rule_id": "default_known_guest_arrival",
        "text": "Known guest arrival confirmation",
        "scope": "area",
        "scope_ref": "front_door",
        "severity": "info",
        "conditions": {"subject_type": "person", "subject_known": "known"},
        "actions": [{"type": "notify", "targets": ["resident_1"]}],
        "confidence_required": 0.75,
        "created_by": "system",
    },
    {
        "rule_id": "default_unanswered_knock",
        "text": "Repeated unanswered knock at front door",
        "scope": "area",
        "scope_ref": "front_door",
        "severity": "warning",
        "conditions": {"detections_required": ["knock"], "context_required": ["nobody_home"]},
        "actions": [{"type": "notify", "targets": ["all_residents"]}],
        "confidence_required": 0.7,
        "created_by": "system",
    },
]


# ─────────────────────────────────────────────────────────────────────
# RuleParser — NL → structured rule
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ParsedRule:
    """The structured-rule output of NL parsing."""

    text: str
    scope: str
    scope_ref: str | None
    severity: str
    conditions: dict[str, Any]
    actions: list[dict[str, Any]]
    temporal: dict[str, Any] = field(default_factory=dict)
    confidence_required: float = 0.6


class RuleParser:
    """Parse natural-language rule text into a structured rule.

    v1 ships a heuristic parser that handles the most common shapes;
    LLM-backed parsing wires in when conversational UX (Epic 7 #110 + the
    rule-creation chat flow) connects to the vlm-router. The structure here
    is stable so the LLM layer can drop in seamlessly.
    """

    def parse(self, text: str, *, default_scope: str = "global") -> ParsedRule:
        """Heuristic parse — best-effort. Falls back to a global info rule."""
        lower = text.lower()

        severity = self._extract_severity(lower)
        subject_type = self._extract_subject_type(lower)
        subject_known = self._extract_subject_known(lower)
        location = self._extract_location(lower)
        actions = self._extract_actions(lower)
        temporal = self._extract_temporal(lower)

        scope, scope_ref = ("area", location) if location else (default_scope, None)

        conditions: dict[str, Any] = {}
        if subject_type:
            conditions["subject_type"] = subject_type
        if subject_known:
            conditions["subject_known"] = subject_known
        if location:
            conditions["location"] = location

        return ParsedRule(
            text=text,
            scope=scope,
            scope_ref=scope_ref,
            severity=severity,
            conditions=conditions,
            actions=actions,
            temporal=temporal,
        )

    @staticmethod
    def _extract_severity(text: str) -> str:
        if any(w in text for w in ("alert", "urgent", "emergency", "critical")):
            return "alert"
        if any(w in text for w in ("warn", "warning", "suspicious")):
            return "warning"
        return "info"

    @staticmethod
    def _extract_subject_type(text: str) -> str | None:
        if "person" in text or "stranger" in text or "intruder" in text:
            return "person"
        if any(w in text for w in ("dog", "cat", "pet", "animal")):
            return "pet"
        if "vehicle" in text or "car" in text or "truck" in text:
            return "vehicle"
        return None

    @staticmethod
    def _extract_subject_known(text: str) -> str | None:
        if "unknown" in text or "stranger" in text or "intruder" in text:
            return "unknown"
        if "known" in text or "resident" in text or "family" in text:
            return "known"
        return None

    @staticmethod
    def _extract_location(text: str) -> str | None:
        # Order matters — longer/more specific phrases first
        locations = {
            "front_door": ["front door", "front porch", "doorbell"],
            "backyard": ["backyard", "back yard", "rear yard"],
            "driveway": ["driveway"],
            "garage": ["garage"],
            "front_yard": ["front yard", "front lawn"],
        }
        for area_id, phrases in locations.items():
            if any(p in text for p in phrases):
                return area_id
        return None

    @staticmethod
    def _extract_actions(text: str) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        if "notify" in text or "alert me" in text or "let me know" in text or "tell me" in text:
            actions.append({"type": "notify", "targets": ["resident_1"]})
        if "announce" in text or "speak" in text or "sonos" in text:
            actions.append({"type": "speak"})
        if "unlock" in text:
            actions.append({"type": "ha_service_call", "service": "lock.unlock"})
        if "light" in text or "turn on the lights" in text:
            actions.append({"type": "ha_service_call", "service": "light.turn_on"})
        if not actions:
            # Default to notify
            actions.append({"type": "notify", "targets": ["resident_1"]})
        return actions

    @staticmethod
    def _extract_temporal(text: str) -> dict[str, Any]:
        # Very crude — looks for "between HH and HH" or "at night"
        temporal: dict[str, Any] = {}
        if "night" in text or "after dark" in text:
            temporal["active_hours"] = "20:00-07:00"
        if "during the day" in text or "daytime" in text:
            temporal["active_hours"] = "07:00-20:00"
        return temporal


# ─────────────────────────────────────────────────────────────────────
# Dismissal counter helpers (#113, #114)
# ─────────────────────────────────────────────────────────────────────


def record_dismissal(rule: RuleRecord) -> None:
    """Increment dismiss counters on a rule (called by alert ack flow)."""
    rule.dismiss_count = (rule.dismiss_count or 0) + 1
    rule.dismiss_count_24h = (rule.dismiss_count_24h or 0) + 1


def record_fire(rule: RuleRecord, *, fired_at: datetime) -> None:
    """Increment hit counter + update last_fired."""
    rule.hit_count = (rule.hit_count or 0) + 1
    rule.last_fired = fired_at


def should_propose_suppression(rule: RuleRecord, *, dismiss_threshold: int = 3) -> bool:
    """True if recent dismissals warrant proposing a suppression rule (§10).

    Agent-proposed suppression rules let the system learn from repeated
    user dismissals without explicit user action.
    """
    return (rule.dismiss_count_24h or 0) >= dismiss_threshold


def generate_rule_id() -> str:
    """Generate a fresh rule_id. Format: rule_<short-uuid>."""
    return f"rule_{uuid.uuid4().hex[:12]}"
