"""/intent page — list rendering + new/edit form + form parsing."""

from __future__ import annotations

import pytest
from kukiihome_ha_agent.rules_store import Rule, RuleScope
from kukiihome_ha_agent.web_ui.intent import (
    parse_rule_form,
    render_intent_page,
    render_rule_form,
)

NOW = 1_700_000_000.0


def _nl_rule(**kw):
    base: dict = dict(  # noqa: C408 — kwargs-style preserves keyword ergonomics in helpers
        id="winston_unsupervised",
        name="Winston unsupervised in front",
        mode="nl",
        intent_text="Winston seems to have gotten outside without supervision.",
        scope=RuleScope(areas=["front_yard"]),
        enabled=True,
        matched_count=2,
        last_matched_at=NOW - 600,
    )
    base.update(kw)
    return Rule(**base)


def _shortcut_rule(**kw):
    base: dict = dict(  # noqa: C408 — kwargs-style preserves keyword ergonomics in helpers
        id="bob_arrives",
        name="Bob arrives",
        mode="shortcut",
        intent_text="",
        shortcut_subject="bob",
        severity_static="critical",
        scope=RuleScope(),
        enabled=True,
        matched_count=14,
        last_matched_at=NOW - 23 * 60,
    )
    base.update(kw)
    return Rule(**base)


# ─── List page ────────────────────────────────────────────────────────


def test_empty_list_shows_onboarding_copy():
    html = render_intent_page([], now_ts=NOW)
    assert "<h1>Intent</h1>" in html
    assert "No rules yet" in html
    assert "+ New rule" in html


def test_preferences_placeholder_renders_above_rules():
    html = render_intent_page([], now_ts=NOW)
    # Preferences placeholder is positioned before the Rules card; check
    # by index so the section order is asserted explicitly.
    pref_idx = html.index("Preferences")
    rules_idx = html.index("<h2>Rules</h2>")
    assert pref_idx < rules_idx


def test_nl_rule_row_renders_intent_text_and_VLM_reasoned_label():
    html = render_intent_page([_nl_rule()], now_ts=NOW)
    assert "Winston unsupervised in front" in html
    assert "Winston seems to have gotten outside" in html
    assert "VLM-reasoned" in html
    # Scope shows the area, not the empty cameras
    assert "front_yard" in html


def test_shortcut_rule_row_renders_subject_and_static_severity():
    html = render_intent_page([_shortcut_rule()], now_ts=NOW)
    assert "Bob arrives" in html
    assert ">bob<" in html  # subject called out in the ALERT IF
    assert "critical (static)" in html
    assert "(identity shortcut)" in html


def test_last_matched_shows_count_and_friendly_time():
    html = render_intent_page([_nl_rule()], now_ts=NOW)
    assert "matched <b>2</b> times" in html
    # friendly_time output (Just now / N minutes ago / ...) — we just
    # assert the count fired and the friendly-time helper produced a
    # tooltip span.
    assert "<span title=" in html


def test_never_matched_label_when_no_matches():
    rule = _nl_rule(matched_count=0, last_matched_at=None)
    html = render_intent_page([rule], now_ts=NOW)
    assert "never matched yet" in html


def test_disabled_rule_still_appears_with_disabled_chip():
    rule = _nl_rule(enabled=False)
    html = render_intent_page([rule], now_ts=NOW)
    assert "Winston unsupervised" in html
    assert "disabled" in html


def test_rule_action_buttons_present():
    rule = _nl_rule()
    html = render_intent_page([rule], now_ts=NOW)
    for label in ("Edit", "View matches", "Delete"):
        assert label in html
    # Enable/Disable button toggles based on current state
    assert "Disable" in html  # since rule.enabled=True


def test_trust_line_explains_HA_event_contract():
    html = render_intent_page([], now_ts=NOW)
    assert "kukiihome_alert" in html
    assert "severity" in html


def test_escapes_user_supplied_strings_safely():
    rule = _nl_rule(
        name="<img src=x>",
        intent_text="<script>alert(1)</script>",
    )
    html = render_intent_page([rule], now_ts=NOW)
    assert "<img src=x>" not in html
    assert "<script>" not in html
    assert "&lt;img" in html


# ─── Form rendering ───────────────────────────────────────────────────


def test_new_form_shape_includes_both_mode_radios_and_severity():
    html = render_rule_form(None)
    assert "<h1>New rule</h1>" in html
    # Mode radios
    assert "value='nl'" in html and "checked" in html  # nl default
    assert "value='shortcut'" in html
    # Severity radios
    for v in ("low", "normal", "critical"):
        assert f"value='{v}'" in html
    # Form posts to /intent/rules for create
    assert "action='intent/rules'" in html


def test_new_form_lists_available_subjects_and_cameras():
    html = render_rule_form(
        None,
        available_subjects=[("bob", "Bob (person)"), ("winston", "Winston (dog)")],
        available_cameras=[("pool", "Pool Camera"), ("front", "Front Camera")],
    )
    assert "Bob (person)" in html
    assert "Winston (dog)" in html
    assert "Pool Camera" in html
    assert "Front Camera" in html


def test_edit_form_preserves_rule_state():
    rule = _nl_rule()
    html = render_rule_form(rule)
    assert "Edit rule" in html
    assert rule.name in html
    assert rule.intent_text in html
    # Form posts back to /intent/rules/{id}
    assert f"action='intent/rules/{rule.id}'" in html


def test_edit_form_shortcut_mode_shows_severity_selected():
    rule = _shortcut_rule(severity_static="critical")
    html = render_rule_form(rule)
    # the "critical" radio is checked when severity_static == critical
    assert "value='critical' checked" in html


# ─── Form parsing ─────────────────────────────────────────────────────


def test_parse_rule_form_nl_minimal():
    out = parse_rule_form({
        "name": "Winston rule",
        "mode": "nl",
        "intent_text": "be careful",
    })
    assert out["name"] == "Winston rule"
    assert out["mode"] == "nl"
    assert out["intent_text"] == "be careful"
    assert out["scope"].cameras == []
    # NL mode → severity_static is None, shortcut_subject is None
    assert out["severity_static"] is None
    assert out["shortcut_subject"] is None


def test_parse_rule_form_shortcut_pickers_and_severity():
    out = parse_rule_form({
        "name": "Bob arrives",
        "mode": "shortcut",
        "shortcut_subject": "bob",
        "severity_static": "critical",
    })
    assert out["shortcut_subject"] == "bob"
    assert out["severity_static"] == "critical"


def test_parse_rule_form_custom_subject_overrides_picker():
    out = parse_rule_form({
        "name": "Person seen",
        "mode": "shortcut",
        "shortcut_subject": "bob",
        "shortcut_subject_custom": "person",
        "severity_static": "normal",
    })
    assert out["shortcut_subject"] == "person"  # custom wins


def test_parse_rule_form_bad_mode_falls_back_to_nl():
    out = parse_rule_form({"name": "x", "mode": "garbage"})
    assert out["mode"] == "nl"


def test_parse_rule_form_bad_severity_falls_back_to_normal():
    out = parse_rule_form({
        "name": "x", "mode": "shortcut", "shortcut_subject": "bob",
        "severity_static": "ultra-critical",
    })
    assert out["severity_static"] == "normal"


def test_parse_rule_form_missing_name_raises():
    with pytest.raises(ValueError):
        parse_rule_form({"name": "   ", "mode": "nl"})


def test_parse_rule_form_scope_lists_from_multivalue_form():
    # aiohttp's MultiDict supports .getall; our parser uses it. Simulate
    # with a dict that exposes .getall.
    class M(dict):
        def getall(self, key, default):
            v = self.get(key)
            if v is None:
                return default
            return v if isinstance(v, list) else [v]

    form = M({
        "name": "scoped",
        "mode": "nl",
        "cameras": ["pool", "front_south"],
        "areas": ["front_yard"],
    })
    out = parse_rule_form(form)
    assert out["scope"].cameras == ["pool", "front_south"]
    assert out["scope"].areas == ["front_yard"]
