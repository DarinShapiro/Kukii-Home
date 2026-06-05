"""Rules runtime — scope filtering, shortcut matching, NL prompt + parse."""

from __future__ import annotations

from datetime import datetime

import pytest
from kukiihome_ha_agent.rules_runtime import (
    DEFAULT_MATCH_THRESHOLD,
    RulesRuntime,
    _in_time_window,
    build_nl_prompt_section,
    evaluate_shortcuts,
    nl_rules_in_scope,
    parse_matched_rules,
    rule_in_scope,
    subjects_in_alert,
)
from kukiihome_ha_agent.rules_store import Rule, RuleScope, RulesStore

# ─── time-window evaluator ─────────────────────────────────────────


def test_in_time_window_inclusive_start_exclusive_end():
    w = {"days": ["mon"], "start": "09:00", "end": "17:00"}
    monday_9am = datetime(2026, 6, 1, 9, 0, 0)  # Mon
    monday_8_59 = datetime(2026, 6, 1, 8, 59, 0)
    monday_5pm = datetime(2026, 6, 1, 17, 0, 0)  # exclusive end
    monday_4_59 = datetime(2026, 6, 1, 16, 59, 0)
    assert _in_time_window(w, monday_9am) is True
    assert _in_time_window(w, monday_8_59) is False
    assert _in_time_window(w, monday_5pm) is False
    assert _in_time_window(w, monday_4_59) is True


def test_in_time_window_filters_wrong_day():
    w = {"days": ["mon"], "start": "00:00", "end": "23:59"}
    tuesday = datetime(2026, 6, 2, 12, 0, 0)
    assert _in_time_window(w, tuesday) is False


def test_in_time_window_empty_days_means_any_day():
    w = {"days": [], "start": "12:00", "end": "13:00"}
    sat = datetime(2026, 6, 6, 12, 30, 0)
    sun = datetime(2026, 6, 7, 12, 30, 0)
    assert _in_time_window(w, sat) is True
    assert _in_time_window(w, sun) is True


def test_in_time_window_malformed_clock_fails_closed():
    w = {"days": [], "start": "bad", "end": "13:00"}
    assert _in_time_window(w, datetime(2026, 6, 1, 12, 0)) is False


# ─── scope gating ──────────────────────────────────────────────────


def _r(**kw):
    base: dict = dict(id="r", name="r", mode="nl", intent_text="x")  # noqa: C408
    base.update(kw)
    return Rule(**base)


def test_rule_in_scope_empty_lists_mean_any():
    r = _r(scope=RuleScope())
    assert rule_in_scope(r, camera_id="any", area_id="any", ts=0) is True


def test_rule_in_scope_camera_gate():
    r = _r(scope=RuleScope(cameras=["pool", "front"]))
    assert rule_in_scope(r, camera_id="pool", area_id=None, ts=None) is True
    assert rule_in_scope(r, camera_id="back", area_id=None, ts=None) is False


def test_rule_in_scope_area_gate():
    r = _r(scope=RuleScope(areas=["front_yard"]))
    assert rule_in_scope(r, camera_id=None, area_id="front_yard", ts=None) is True
    assert rule_in_scope(r, camera_id=None, area_id="backyard", ts=None) is False


def test_rule_in_scope_time_gate_uses_local_ts():
    # 2026-06-01 (Monday) 10:00 local → in window
    ts_mon_10 = datetime(2026, 6, 1, 10, 0, 0).timestamp()
    r = _r(
        scope=RuleScope(
            time_windows=[
                {"days": ["mon"], "start": "09:00", "end": "17:00"},
            ]
        )
    )
    assert rule_in_scope(r, camera_id=None, area_id=None, ts=ts_mon_10) is True
    ts_mon_19 = datetime(2026, 6, 1, 19, 0, 0).timestamp()
    assert rule_in_scope(r, camera_id=None, area_id=None, ts=ts_mon_19) is False


def test_rule_in_scope_all_axes_AND_combined():
    r = _r(scope=RuleScope(cameras=["pool"], areas=["backyard"]))
    assert rule_in_scope(r, camera_id="pool", area_id="backyard", ts=None) is True
    assert rule_in_scope(r, camera_id="pool", area_id="front", ts=None) is False
    assert rule_in_scope(r, camera_id="other", area_id="backyard", ts=None) is False


# ─── subject extraction ────────────────────────────────────────────


def test_subjects_in_alert_reads_identified_actors_first():
    alert = {
        "identified_actors": [
            {"kind": "person", "actor_id": "bob"},
            {"kind": "dog", "actor_id": "winston"},
        ]
    }
    assert subjects_in_alert(alert) == [("person", "bob"), ("dog", "winston")]


def test_subjects_in_alert_falls_back_to_classification():
    alert = {"sensor_classification": "person"}
    assert subjects_in_alert(alert) == [("person", None)]


def test_subjects_in_alert_empty_when_nothing_known():
    assert subjects_in_alert({}) == []


# ─── shortcut evaluator ────────────────────────────────────────────


def _shortcut_rule(subject, *, severity="critical", cameras=None):
    return Rule(
        id=f"sc_{subject}",
        name=f"sc {subject}",
        mode="shortcut",
        intent_text="",
        shortcut_subject=subject,
        severity_static=severity,
        scope=RuleScope(cameras=cameras or []),
    )


def test_evaluate_shortcuts_matches_actor_id():
    rules = [_shortcut_rule("bob")]
    alert = {"identified_actors": [{"kind": "person", "actor_id": "bob"}]}
    out = evaluate_shortcuts(rules, alert=alert, camera_id=None, area_id=None, ts=None)
    assert len(out) == 1
    assert out[0].rule.id == "sc_bob"
    assert out[0].matched_subject_id == "bob"
    assert out[0].severity == "critical"


def test_evaluate_shortcuts_matches_kind_when_no_actor():
    # Pattern: alert me on ANY unknown person
    rules = [_shortcut_rule("person", severity="normal")]
    alert = {"sensor_classification": "person"}
    out = evaluate_shortcuts(rules, alert=alert, camera_id=None, area_id=None, ts=None)
    assert len(out) == 1
    assert out[0].severity == "normal"


def test_evaluate_shortcuts_respects_camera_scope():
    rules = [_shortcut_rule("bob", cameras=["pool"])]
    alert = {"identified_actors": [{"kind": "person", "actor_id": "bob"}]}
    on_pool = evaluate_shortcuts(rules, alert=alert, camera_id="pool", area_id=None, ts=None)
    elsewhere = evaluate_shortcuts(rules, alert=alert, camera_id="driveway", area_id=None, ts=None)
    assert len(on_pool) == 1
    assert len(elsewhere) == 0


def test_evaluate_shortcuts_skips_nl_rules():
    nl = Rule(id="nl1", name="nl", mode="nl", intent_text="something", shortcut_subject="bob")
    out = evaluate_shortcuts(
        [nl], alert={"sensor_classification": "person"}, camera_id=None, area_id=None, ts=None
    )
    assert out == []


def test_evaluate_shortcuts_fires_once_per_rule_even_if_multi_subject():
    rules = [_shortcut_rule("person", severity="low")]
    alert = {
        "identified_actors": [
            {"kind": "person", "actor_id": "a"},
            {"kind": "person", "actor_id": "b"},
        ]
    }
    out = evaluate_shortcuts(rules, alert=alert, camera_id=None, area_id=None, ts=None)
    assert len(out) == 1  # one rule, one fire


# ─── NL prompt section ─────────────────────────────────────────────


def test_nl_rules_in_scope_filters_by_scope_and_mode():
    nl_in = Rule(id="n1", name="n1", mode="nl", intent_text="t", scope=RuleScope(cameras=["pool"]))
    nl_out = Rule(
        id="n2", name="n2", mode="nl", intent_text="t", scope=RuleScope(cameras=["front"])
    )
    shortcut = Rule(
        id="s1", name="s1", mode="shortcut", intent_text="", shortcut_subject="bob"
    )  # excluded by mode
    rules = [nl_in, nl_out, shortcut]
    assert nl_rules_in_scope(rules, camera_id="pool", area_id=None, ts=None) == [nl_in]


def test_build_nl_prompt_section_empty_input_returns_empty_string():
    assert build_nl_prompt_section([]) == ""


def test_build_nl_prompt_section_lists_each_rule_with_id_and_intent():
    rules = [
        Rule(
            id="r1",
            name="Winston unsupervised",
            mode="nl",
            intent_text="winston is outside without a person",
        ),
        Rule(id="r2", name="Delivery", mode="nl", intent_text="someone left a package"),
    ]
    prompt = build_nl_prompt_section(rules)
    assert "Named user intents" in prompt
    assert "[rule:r1]" in prompt and "Winston unsupervised" in prompt
    assert "[rule:r2]" in prompt and "someone left a package" in prompt


# ─── VLM response parsing ──────────────────────────────────────────


def test_parse_matched_rules_filters_below_threshold():
    rules = [Rule(id="r1", name="r1", mode="nl", intent_text="")]
    payload = [
        {
            "rule_id": "r1",
            "matched": True,
            "confidence": 0.5,
            "severity": "critical",
            "reasoning": ".",
        },
    ]
    out = parse_matched_rules(rules, payload, threshold=0.6)
    assert len(out) == 1
    assert out[0].matched is False  # below threshold → non-match
    assert out[0].severity is None


def test_parse_matched_rules_records_real_match():
    rules = [Rule(id="r1", name="r1", mode="nl", intent_text="")]
    payload = [
        {
            "rule_id": "r1",
            "matched": True,
            "confidence": 0.9,
            "severity": "critical",
            "reasoning": "bad scene",
        },
    ]
    out = parse_matched_rules(rules, payload)
    assert len(out) == 1
    assert out[0].matched is True
    assert out[0].severity == "critical"
    assert out[0].confidence == pytest.approx(0.9)
    assert out[0].reasoning == "bad scene"


def test_parse_matched_rules_skips_unknown_rule_ids():
    rules = [Rule(id="r1", name="r1", mode="nl", intent_text="")]
    payload = [{"rule_id": "stale", "matched": True, "confidence": 0.9}]
    assert parse_matched_rules(rules, payload) == []


def test_default_match_threshold_constant_value():
    # The doc'd default — protects against accidental retuning.
    assert DEFAULT_MATCH_THRESHOLD == 0.6


# ─── runtime cache ─────────────────────────────────────────────────


@pytest.fixture
def store():
    s = RulesStore(path=None)
    yield s
    s.close()


def test_runtime_returns_active_rules_only(store):
    active = store.create(Rule(id="", name="active", mode="nl", intent_text="x"))
    disabled = store.create(Rule(id="", name="disabled", mode="nl", intent_text="y", enabled=False))
    rt = RulesRuntime(store)
    ids = {r.id for r in rt.active_rules()}
    assert active.id in ids
    assert disabled.id not in ids


def test_runtime_cache_reloads_after_dirty(store):
    rt = RulesRuntime(store)
    assert rt.active_rules() == []
    store.create(Rule(id="", name="new", mode="nl", intent_text=""))
    # Dirty bit flipped; next active_rules() reflects the new rule
    assert len(rt.active_rules()) == 1


def test_runtime_shortcuts_for_threads_through(store):
    store.create(
        Rule(
            id="",
            name="bob",
            mode="shortcut",
            intent_text="",
            shortcut_subject="bob",
            severity_static="critical",
        )
    )
    rt = RulesRuntime(store)
    out = rt.shortcuts_for(
        alert={"identified_actors": [{"kind": "person", "actor_id": "bob"}]},
        camera_id=None,
        area_id=None,
        ts=None,
    )
    assert len(out) == 1
    assert out[0].severity == "critical"
