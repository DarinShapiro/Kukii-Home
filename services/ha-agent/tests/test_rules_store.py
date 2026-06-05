"""RulesStore — CRUD, soft-delete, audit log, slug derivation."""

from __future__ import annotations

import time

import pytest
from kukiihome_ha_agent.rules_store import (
    Rule,
    RuleMatch,
    RuleScope,
    RulesStore,
    slug_for,
)


@pytest.fixture
def store():
    s = RulesStore(path=None)
    yield s
    s.close()


def _make_rule(name="Winston unsupervised", **overrides):
    rule = Rule(
        id="",
        name=name,
        mode="nl",
        intent_text="Winston seems to have gotten outside without supervision.",
        scope=RuleScope(cameras=["front_south"]),
    )
    for k, v in overrides.items():
        setattr(rule, k, v)
    return rule


# ─── slugs ────────────────────────────────────────────────────────────


def test_slug_for_basic_normalization():
    assert slug_for("Winston Unsupervised") == "winston_unsupervised"
    assert slug_for("  Bob arrives! ") == "bob_arrives"
    # Empty / pure-symbol input → uuid-suffixed fallback (never empty)
    assert slug_for("").startswith("rule_")
    assert slug_for("!!!").startswith("rule_")


# ─── CRUD ────────────────────────────────────────────────────────────


def test_create_assigns_slug_and_timestamps(store):
    r = store.create(_make_rule(name="Winston unsupervised in front"))
    assert r.id == "winston_unsupervised_in_front"
    assert r.created_at > 0
    assert r.updated_at >= r.created_at
    assert r.enabled is True
    # Round-trip through get()
    fetched = store.get(r.id)
    assert fetched is not None
    assert fetched.name == r.name
    assert fetched.scope.cameras == ["front_south"]


def test_create_collision_appends_suffix(store):
    a = store.create(_make_rule(name="Same"))
    b = store.create(_make_rule(name="Same"))
    assert a.id == "same"
    assert b.id.startswith("same_") and b.id != "same"


def test_update_partial_fields_preserves_others(store):
    r = store.create(_make_rule())
    updated = store.update(r.id, name="renamed", intent_text="new text")
    assert updated.name == "renamed"
    assert updated.intent_text == "new text"
    assert updated.mode == r.mode  # not changed
    # scope_json untouched when 'scope' kw not passed
    assert updated.scope.cameras == r.scope.cameras


def test_update_scope_via_RuleScope_object(store):
    r = store.create(_make_rule())
    updated = store.update(r.id, scope=RuleScope(areas=["front_yard"]))
    assert updated.scope.areas == ["front_yard"]
    assert updated.scope.cameras == []


def test_set_enabled_toggles(store):
    r = store.create(_make_rule())
    store.set_enabled(r.id, False)
    assert store.get(r.id).enabled is False
    store.set_enabled(r.id, True)
    assert store.get(r.id).enabled is True


def test_update_unknown_id_returns_none(store):
    assert store.update("does_not_exist", name="x") is None


# ─── soft-delete + audit preservation ─────────────────────────────────


def test_soft_delete_marks_retired_at(store):
    r = store.create(_make_rule())
    out = store.soft_delete(r.id)
    assert out.retired_at is not None
    # Hidden from default listing
    assert all(x.id != r.id for x in store.all_rules())
    # Visible with include_retired=True
    assert any(x.id == r.id for x in store.all_rules(include_retired=True))
    # Active set also hides retired
    assert all(x.id != r.id for x in store.active_rules())


def test_undelete_restores_visibility(store):
    r = store.create(_make_rule())
    store.soft_delete(r.id)
    store.undelete(r.id)
    assert store.get(r.id).retired_at is None


def test_disabled_rules_hidden_from_active_visible_in_all(store):
    r = store.create(_make_rule())
    store.set_enabled(r.id, False)
    assert all(x.id != r.id for x in store.active_rules())
    assert any(x.id == r.id for x in store.all_rules())


# ─── audit log ────────────────────────────────────────────────────────


def test_record_match_bumps_counter_for_matched_only(store):
    r = store.create(_make_rule())
    now = time.time()
    store.record_match(
        RuleMatch(
            rule_id=r.id,
            incident_id="i1",
            matched_at=now,
            severity="critical",
            confidence=0.92,
            reasoning="match",
            matched=True,
        )
    )
    store.record_match(
        RuleMatch(
            rule_id=r.id,
            incident_id="i2",
            matched_at=now + 1,
            severity=None,
            confidence=0.20,
            reasoning="non-match",
            matched=False,
        )
    )
    refreshed = store.get(r.id)
    assert refreshed.matched_count == 1
    assert refreshed.last_matched_at == now


def test_matches_for_rule_returns_newest_first(store):
    r = store.create(_make_rule())
    base = time.time()
    for i, sev in enumerate(["low", "normal", "critical"]):
        store.record_match(
            RuleMatch(
                rule_id=r.id,
                incident_id=f"i{i}",
                matched_at=base + i,
                severity=sev,
                confidence=0.7,
                reasoning="m",
                matched=True,
            )
        )
    rows = store.matches_for_rule(r.id)
    assert len(rows) == 3
    assert rows[0].severity == "critical"
    assert rows[-1].severity == "low"


def test_matches_for_rule_only_matched_filters_non_matches(store):
    r = store.create(_make_rule())
    base = time.time()
    store.record_match(
        RuleMatch(
            rule_id=r.id,
            incident_id="i1",
            matched_at=base,
            severity="normal",
            confidence=0.8,
            reasoning="x",
            matched=True,
        )
    )
    store.record_match(
        RuleMatch(
            rule_id=r.id,
            incident_id="i2",
            matched_at=base + 1,
            severity=None,
            confidence=0.1,
            reasoning="y",
            matched=False,
        )
    )
    rows = store.matches_for_rule(r.id, only_matched=True)
    assert len(rows) == 1


def test_matches_for_incident_returns_all_rule_evaluations(store):
    r1 = store.create(_make_rule(name="r1"))
    r2 = store.create(_make_rule(name="r2"))
    now = time.time()
    store.record_match(
        RuleMatch(
            rule_id=r1.id,
            incident_id="inc99",
            matched_at=now,
            severity="low",
            confidence=0.7,
            reasoning="a",
            matched=True,
        )
    )
    store.record_match(
        RuleMatch(
            rule_id=r2.id,
            incident_id="inc99",
            matched_at=now,
            severity=None,
            confidence=0.2,
            reasoning="b",
            matched=False,
        )
    )
    rows = store.matches_for_incident("inc99")
    assert {row.rule_id for row in rows} == {r1.id, r2.id}


def test_protective_actions_roundtrip_as_json(store):
    r = store.create(_make_rule())
    actions = [
        {"service": "lock.lock", "target": "lock.back_door", "result": "ok"},
        {"service": "light.turn_on", "target": "light.flood", "result": "ok"},
    ]
    store.record_match(
        RuleMatch(
            rule_id=r.id,
            incident_id="i1",
            matched_at=time.time(),
            severity="critical",
            confidence=0.9,
            reasoning="x",
            matched=True,
            protective_actions_taken=actions,
        )
    )
    rows = store.matches_for_rule(r.id)
    assert rows[0].protective_actions_taken == actions


# ─── persistence ──────────────────────────────────────────────────────


def test_persist_to_disk_survives_reopen(tmp_path):
    db = tmp_path / "rules.db"
    s1 = RulesStore(path=str(db))
    r = s1.create(_make_rule(name="Persisted"))
    s1.close()

    s2 = RulesStore(path=str(db))
    assert s2.get(r.id) is not None
    assert s2.get(r.id).name == "Persisted"
    s2.close()


# ─── dirty-bit cache coherence ────────────────────────────────────────


def test_dirty_bit_set_on_writes_cleared_on_take(store):
    # Initial state: dirty (so cache loads on first access)
    assert store.take_dirty() is True
    # After taking, clean
    assert store.take_dirty() is False
    # Any write flips it back on
    store.create(_make_rule())
    assert store.take_dirty() is True
    assert store.take_dirty() is False
