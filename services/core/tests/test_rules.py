"""Tests for the rule engine: evaluation, conflict resolution, NL parsing."""

from __future__ import annotations

from datetime import datetime

from sentihome_core.rules import (
    DEFAULT_RULE_PACK,
    ConflictResolver,
    RuleEvalContext,
    RuleEvaluator,
    RuleParser,
    generate_rule_id,
    record_dismissal,
    record_fire,
    should_propose_suppression,
)
from sentihome_memory.models import RuleRecord


def _ctx(
    *,
    camera_id: str = "front_door",
    area_id: str | None = "front_door",
    zone_id: str | None = None,
    subject_type: str | None = "person",
    subject_known: str | None = "unknown",
    detections: tuple[str, ...] = ("person",),
    contexts_active: tuple[str, ...] = (),
    confidence: float = 0.9,
    now: datetime | None = None,
) -> RuleEvalContext:
    return RuleEvalContext(
        event_id="evt_1",
        camera_id=camera_id,
        area_id=area_id,
        zone_id=zone_id,
        subject_type=subject_type,
        subject_known=subject_known,
        subject_actor_id=None,
        detections=detections,
        contexts_active=contexts_active,
        now=now or datetime(2026, 5, 25, 14, 30),
        confidence=confidence,
    )


def _rule(
    *,
    rule_id: str = "r1",
    scope: str = "global",
    scope_ref: str | None = None,
    severity: str = "info",
    conditions: dict | None = None,
    temporal: dict | None = None,
    actions: list | None = None,
    confidence_required: float = 0.5,
) -> RuleRecord:
    return RuleRecord(
        rule_id=rule_id,
        text=rule_id,
        scope=scope,
        scope_ref=scope_ref,
        severity=severity,
        conditions=conditions or {},
        temporal=temporal or {},
        actions=actions or [],
        confidence_required=confidence_required,
    )


# ─────────────────────────────────────────────────────────────────────
# RuleEvaluator
# ─────────────────────────────────────────────────────────────────────


def test_evaluator_global_rule_matches_anything() -> None:
    ev = RuleEvaluator()
    assert ev.evaluate(_rule(scope="global"), _ctx()) is True


def test_evaluator_filters_subject_type() -> None:
    ev = RuleEvaluator()
    pet_rule = _rule(conditions={"subject_type": "pet"})
    assert ev.evaluate(pet_rule, _ctx(subject_type="person")) is False
    assert ev.evaluate(pet_rule, _ctx(subject_type="pet")) is True


def test_evaluator_filters_subject_known() -> None:
    ev = RuleEvaluator()
    unknown_rule = _rule(conditions={"subject_known": "unknown"})
    assert ev.evaluate(unknown_rule, _ctx(subject_known="known")) is False
    assert ev.evaluate(unknown_rule, _ctx(subject_known="unknown")) is True


def test_evaluator_filters_location() -> None:
    ev = RuleEvaluator()
    rule = _rule(conditions={"location": "backyard"})
    assert ev.evaluate(rule, _ctx(area_id="front_door")) is False
    assert ev.evaluate(rule, _ctx(area_id="backyard")) is True


def test_evaluator_requires_detections() -> None:
    ev = RuleEvaluator()
    rule = _rule(conditions={"detections_required": ["dog", "person"]})
    assert ev.evaluate(rule, _ctx(detections=("person",))) is False
    assert ev.evaluate(rule, _ctx(detections=("dog", "person"))) is True


def test_evaluator_excludes_detected_actors() -> None:
    ev = RuleEvaluator()
    rule = _rule(conditions={"exclude_if_detected": ["sarah"]})
    assert ev.evaluate(rule, _ctx(detections=("person", "sarah"))) is False
    assert ev.evaluate(rule, _ctx(detections=("person",))) is True


def test_evaluator_requires_active_contexts() -> None:
    ev = RuleEvaluator()
    rule = _rule(conditions={"context_required": ["delivery_expected"]})
    assert ev.evaluate(rule, _ctx(contexts_active=())) is False
    assert ev.evaluate(rule, _ctx(contexts_active=("delivery_expected",))) is True


def test_evaluator_temporal_active_hours_in_window() -> None:
    ev = RuleEvaluator()
    rule = _rule(temporal={"active_hours": "08:00-22:00"})
    assert ev.evaluate(rule, _ctx(now=datetime(2026, 5, 25, 14, 30))) is True
    assert ev.evaluate(rule, _ctx(now=datetime(2026, 5, 25, 3, 0))) is False


def test_evaluator_temporal_active_hours_wraps_midnight() -> None:
    ev = RuleEvaluator()
    rule = _rule(temporal={"active_hours": "20:00-07:00"})
    assert ev.evaluate(rule, _ctx(now=datetime(2026, 5, 25, 23, 0))) is True
    assert ev.evaluate(rule, _ctx(now=datetime(2026, 5, 25, 3, 0))) is True
    assert ev.evaluate(rule, _ctx(now=datetime(2026, 5, 25, 12, 0))) is False


def test_evaluator_active_days_filter() -> None:
    ev = RuleEvaluator()
    rule = _rule(temporal={"active_days": ["Mon", "Tue", "Wed", "Thu", "Fri"]})
    # 2026-05-25 is a Monday
    assert ev.evaluate(rule, _ctx(now=datetime(2026, 5, 25, 14, 30))) is True
    # Saturday
    assert ev.evaluate(rule, _ctx(now=datetime(2026, 5, 23, 14, 30))) is False


def test_evaluator_skips_suppressed_rule() -> None:
    ev = RuleEvaluator()
    rule = _rule()
    rule.suppress_until = datetime(2099, 1, 1)
    assert ev.evaluate(rule, _ctx()) is False


def test_evaluator_skips_soft_deleted_rule() -> None:
    ev = RuleEvaluator()
    rule = _rule()
    rule.deleted_at = datetime.utcnow()
    assert ev.evaluate(rule, _ctx()) is False


def test_evaluator_confidence_gate() -> None:
    ev = RuleEvaluator()
    rule = _rule(confidence_required=0.8)
    assert ev.evaluate(rule, _ctx(confidence=0.7)) is False
    assert ev.evaluate(rule, _ctx(confidence=0.9)) is True


# ─────────────────────────────────────────────────────────────────────
# ConflictResolver
# ─────────────────────────────────────────────────────────────────────


def test_resolver_empty_list_no_winners() -> None:
    out = ConflictResolver().resolve([])
    assert out.winning_rule_ids == ()
    assert out.severity == "info"


def test_resolver_most_specific_scope_wins() -> None:
    zone_rule = _rule(rule_id="r_zone", scope="zone", scope_ref="entry_mat")
    area_rule = _rule(rule_id="r_area", scope="area", scope_ref="front_door")
    global_rule = _rule(rule_id="r_glob", scope="global")
    out = ConflictResolver().resolve([global_rule, area_rule, zone_rule])
    assert out.winning_rule_ids == ("r_zone",)
    assert "r_area" in out.suppressed_rule_ids
    assert "r_glob" in out.suppressed_rule_ids


def test_resolver_same_scope_severity_max() -> None:
    r_info = _rule(rule_id="r_info", scope="area", severity="info")
    r_warn = _rule(rule_id="r_warn", scope="area", severity="warning")
    r_alert = _rule(rule_id="r_alert", scope="area", severity="alert")
    out = ConflictResolver().resolve([r_info, r_warn, r_alert])
    assert set(out.winning_rule_ids) == {"r_info", "r_warn", "r_alert"}
    assert out.severity == "alert"


def test_resolver_actions_deduplicated() -> None:
    r1 = _rule(
        rule_id="r1",
        scope="area",
        actions=[{"type": "notify", "targets": ["resident_1"]}],
    )
    r2 = _rule(
        rule_id="r2",
        scope="area",
        actions=[{"type": "notify", "targets": ["resident_1"]}],
    )
    out = ConflictResolver().resolve([r1, r2])
    notify_actions = [a for a in out.actions if a["type"] == "notify"]
    assert len(notify_actions) == 1


def test_resolver_actions_union_across_winners() -> None:
    r1 = _rule(
        rule_id="r1",
        scope="area",
        actions=[{"type": "notify", "targets": ["resident_1"]}],
    )
    r2 = _rule(rule_id="r2", scope="area", actions=[{"type": "speak"}])
    out = ConflictResolver().resolve([r1, r2])
    types = {a["type"] for a in out.actions}
    assert types == {"notify", "speak"}


def test_resolver_suppression_cancels_target() -> None:
    target = _rule(rule_id="r_target", scope="area")
    suppressor = _rule(
        rule_id="r_supp",
        scope="area",
        actions=[{"type": "suppress", "target_rule_id": "r_target"}],
    )
    out = ConflictResolver().resolve([target, suppressor])
    assert "r_target" not in out.winning_rule_ids
    assert "r_target" in out.suppressed_rule_ids


# ─────────────────────────────────────────────────────────────────────
# RuleParser (NL)
# ─────────────────────────────────────────────────────────────────────


def test_parser_detects_alert_severity() -> None:
    p = RuleParser().parse("Alert me if a stranger is at the door")
    assert p.severity == "alert"


def test_parser_detects_unknown_subject() -> None:
    p = RuleParser().parse("Notify me if an unknown person enters the front yard")
    assert p.conditions.get("subject_known") == "unknown"
    assert p.conditions.get("subject_type") == "person"


def test_parser_detects_pet_subject() -> None:
    p = RuleParser().parse("Let me know if the dog is alone in the backyard")
    assert p.conditions.get("subject_type") == "pet"
    assert p.scope == "area"
    assert p.scope_ref == "backyard"


def test_parser_detects_actions_notify_and_speak() -> None:
    p = RuleParser().parse("Let me know when the mailman arrives and announce it on Sonos")
    types = {a["type"] for a in p.actions}
    assert "notify" in types
    assert "speak" in types


def test_parser_detects_unlock_action() -> None:
    p = RuleParser().parse("Unlock the door when Sarah arrives")
    services = {a.get("service") for a in p.actions}
    assert "lock.unlock" in services


def test_parser_extracts_night_window() -> None:
    p = RuleParser().parse("Alert at night if motion in backyard")
    assert p.temporal.get("active_hours") == "20:00-07:00"


def test_parser_defaults_to_notify_when_no_action_specified() -> None:
    p = RuleParser().parse("dog in backyard")
    assert any(a["type"] == "notify" for a in p.actions)


# ─────────────────────────────────────────────────────────────────────
# Default rule pack
# ─────────────────────────────────────────────────────────────────────


def test_default_pack_includes_tier1_safety() -> None:
    rule_ids = {r["rule_id"] for r in DEFAULT_RULE_PACK}
    assert "default_smoke_alarm" in rule_ids
    assert "default_co_alarm" in rule_ids
    assert "default_flood_alarm" in rule_ids


def test_default_pack_all_have_required_fields() -> None:
    for rule in DEFAULT_RULE_PACK:
        assert "rule_id" in rule
        assert "scope" in rule
        assert "severity" in rule
        assert "actions" in rule


# ─────────────────────────────────────────────────────────────────────
# Lifecycle helpers
# ─────────────────────────────────────────────────────────────────────


def test_record_dismissal_increments_counters() -> None:
    rule = _rule()
    record_dismissal(rule)
    record_dismissal(rule)
    assert rule.dismiss_count == 2
    assert rule.dismiss_count_24h == 2


def test_record_fire_updates_hit_count_and_timestamp() -> None:
    rule = _rule()
    now = datetime(2026, 5, 25, 14, 30)
    record_fire(rule, fired_at=now)
    assert rule.hit_count == 1
    assert rule.last_fired == now


def test_should_propose_suppression_at_threshold() -> None:
    rule = _rule()
    rule.dismiss_count_24h = 2
    assert should_propose_suppression(rule, dismiss_threshold=3) is False
    rule.dismiss_count_24h = 3
    assert should_propose_suppression(rule, dismiss_threshold=3) is True


def test_generate_rule_id_format() -> None:
    rid = generate_rule_id()
    assert rid.startswith("rule_")
    assert len(rid) > len("rule_")
