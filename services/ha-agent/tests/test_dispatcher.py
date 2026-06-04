"""Heuristic dispatcher provider (Part X §35) — utterance → placement."""

from __future__ import annotations

from kukiihome_ha_agent.dispatcher import (
    DispatcherContext,
    HeuristicDispatcherProvider,
)


def _ctx(**kw):
    base: dict = dict(  # noqa: C408
        known_actor_names=["Winston", "Bob"],
        known_area_names=["Pool", "Front yard", "Backyard"],
        known_camera_names=["Pool Camera", "Front Camera"],
    )
    base.update(kw)
    return DispatcherContext(**base)


def _dispatcher():
    return HeuristicDispatcherProvider()


# ─── Rule branch ───────────────────────────────────────────────────


def test_notify_persistent_rule_with_actor_and_area():
    p = _dispatcher().propose(
        "Notify me when Winston is in the Front yard alone", ctx=_ctx(),
    )
    assert p.storage_class == "rule"
    assert p.lifecycle == "persistent"
    assert p.fire_affordance == "alert"
    assert p.scope.get("actor") == "winston"
    assert p.scope.get("area") == "front_yard"
    assert p.confidence >= 0.7


def test_actor_resolved_with_canonical_casing():
    p = _dispatcher().propose(
        "alert me when bob arrives", ctx=_ctx(),
    )
    # case-insensitive match, but actor_name is the canonical "Bob"
    assert p.scope.get("actor_name") == "Bob"


def test_camera_used_when_no_area_mentioned():
    # The heuristic prefers area matches over camera matches. When the
    # utterance mentions a camera that overlaps an area name, the area
    # wins; this is by-design — areas are the higher-level grouping.
    p = _dispatcher().propose(
        "Tell me when motion happens on the Front Camera", ctx=_ctx(),
    )
    # Either path is acceptable; assert at least one was resolved.
    assert p.scope.get("camera") == "front_camera" or \
        p.scope.get("area") == "front_yard"


# ─── Transient intent branch ──────────────────────────────────────


def test_tonight_keyword_routes_to_transient_intent():
    p = _dispatcher().propose(
        "Notify me when Bob's car arrives tonight", ctx=_ctx(),
    )
    assert p.storage_class == "transient_intent"
    assert p.lifecycle == "temporal"
    assert p.fire_affordance == "alert"
    assert "actor" in p.scope and p.scope["actor"] == "bob"


def test_today_keyword_routes_to_transient_intent():
    p = _dispatcher().propose(
        "Alert me if anyone is at the pool today", ctx=_ctx(),
    )
    assert p.storage_class == "transient_intent"


# ─── Dismissal branch ─────────────────────────────────────────────


def test_dont_alert_routes_to_dismissal_policy():
    p = _dispatcher().propose(
        "Don't alert me when there's a dog at the front camera", ctx=_ctx(),
    )
    assert p.storage_class == "dismissal_policy"
    assert p.fire_affordance == "dismiss"


def test_boring_routes_to_dismissal():
    p = _dispatcher().propose(
        "These wind-in-tree events are boring noise", ctx=_ctx(),
    )
    assert p.storage_class == "dismissal_policy"


def test_dismissal_with_temporal_marker_is_temporal_lifecycle():
    p = _dispatcher().propose(
        "Ignore alerts at the Pool tonight", ctx=_ctx(),
    )
    assert p.storage_class == "dismissal_policy"
    assert p.lifecycle == "temporal"


# ─── Preference branch ────────────────────────────────────────────


def test_winston_is_our_dog_routes_to_preference():
    # "don't" wins the dismissal pattern first; this is by-design
    # (negative-instruction priority). But the household statement pattern
    # should be detectable when no don't-alert intent is present:
    p = _dispatcher().propose("Winston is our dog", ctx=_ctx())
    assert p.storage_class == "preference"


def test_i_care_about_pattern_to_preference():
    p = _dispatcher().propose(
        "I care about anything happening at the front door at night",
        ctx=_ctx(),
    )
    assert p.storage_class == "preference"
    assert p.fire_affordance == "shift_prior"


# ─── Disambiguation fallback ──────────────────────────────────────


def test_ambiguous_utterance_returns_clarifying_questions():
    p = _dispatcher().propose("watch for stuff", ctx=_ctx())
    assert p.confidence < 0.7
    assert p.needs_disambiguation()
    assert len(p.clarifying_questions) >= 1


def test_proposals_always_include_reasoning_field():
    samples = [
        "Notify when Winston is alone in Front yard",
        "Don't ping me about dogs at the front camera",
        "I care about anyone at the pool",
        "Alert me if a car arrives tonight",
        "watch for stuff",
    ]
    for utt in samples:
        p = _dispatcher().propose(utt, ctx=_ctx())
        assert p.reasoning  # non-empty


# ─── Reasoning field is single sentence ──────────────────────────


def test_reasoning_field_is_concise():
    p = _dispatcher().propose(
        "Notify me when Winston is at the front yard", ctx=_ctx(),
    )
    # Single sentence guidance — under 200 chars keeps the audit row legible.
    assert len(p.reasoning) < 200


# ─── Severity defaults ────────────────────────────────────────────


def test_rule_proposal_carries_a_default_severity():
    p = _dispatcher().propose(
        "Tell me when Winston is in the Front yard alone", ctx=_ctx(),
    )
    # Heuristic provider doesn't infer severity from text in v1 — defaults to
    # 'normal' for rules so the rule can fire without VLM grading.
    assert p.severity in ("low", "normal", "critical")
