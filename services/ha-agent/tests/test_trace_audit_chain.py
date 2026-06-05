"""Trace audit chain (Part III §22 extension) — matched rules + protective
actions + policy hits inline on the alert detail page."""

from __future__ import annotations

import pytest
from kukiihome_ha_agent.action_store import ActionStore, ProtectiveLogRow
from kukiihome_ha_agent.policy_store import Policy, PolicyHit, PolicyStore
from kukiihome_ha_agent.rules_store import Rule, RuleMatch, RulesStore
from kukiihome_ha_agent.web_ui.trace import (
    build_audit_chain_html,
    render_matched_rules_section,
    render_policy_hits_section,
    render_protective_actions_section,
)

NOW = 1_700_000_000.0


# ─── matched rules section ────────────────────────────────────────


def test_matched_rules_section_empty_when_no_matches():
    assert render_matched_rules_section([]) == ""


def test_matched_rules_section_lists_each_rule():
    matches = [
        RuleMatch(
            rule_id="winston_outdoors",
            incident_id="i1",
            matched_at=NOW,
            severity="critical",
            confidence=0.92,
            reasoning="pet alone in front",
            matched=True,
        ),
        RuleMatch(
            rule_id="bob_arrives",
            incident_id="i1",
            matched_at=NOW,
            severity="normal",
            confidence=0.88,
            reasoning="bob recognized",
            matched=True,
        ),
    ]
    html = render_matched_rules_section(matches)
    assert "Matched rules" in html
    assert "winston_outdoors" in html
    assert "bob_arrives" in html
    assert "pet alone in front" in html
    # severity chips
    assert "critical" in html and "normal" in html


def test_matched_rules_section_marks_non_matches():
    matches = [
        RuleMatch(
            rule_id="delivery",
            incident_id="i",
            matched_at=NOW,
            severity=None,
            confidence=0.4,
            reasoning="ambiguous",
            matched=False,
        ),
    ]
    html = render_matched_rules_section(matches)
    assert "no-match" in html


# ─── protective actions section ───────────────────────────────────


def _log(status, *, gate="", action_class="lock", ts=NOW):
    return ProtectiveLogRow(
        incident_id="i",
        camera_id="back",
        ts=ts,
        action_class=action_class,
        service="lock.lock",
        target="lock.back_door",
        data_json=None,
        status=status,
        gate_reason=gate or None,
    )


def test_protective_actions_section_empty_when_none():
    assert render_protective_actions_section([], now_ts=NOW) == ""


def test_protective_actions_section_shows_ok_and_gated():
    rows = [
        _log("ok"),
        _log("gated", gate="severity_below_threshold", action_class="siren"),
    ]
    html = render_protective_actions_section(rows, now_ts=NOW)
    assert "Protective actions" in html
    assert "lock.back_door" in html
    assert "severity_below_threshold" in html


def test_protective_actions_section_status_chip_for_each_outcome():
    rows = [
        _log("ok"),
        _log("failed", gate="ha_call_raised"),
        _log("whitelisted_rejected"),
    ]
    html = render_protective_actions_section(rows, now_ts=NOW)
    # Status names appear in chips
    for s in ("ok", "failed", "whitelisted_rejected"):
        assert s in html


# ─── policy hits section ──────────────────────────────────────────


def test_policy_hits_section_empty_when_none():
    assert render_policy_hits_section([], {}, now_ts=NOW) == ""


def test_policy_hits_section_resolves_policy_names():
    pol = Policy(id="p1", kind="dismissal", name="Wind in tree", created_at=NOW)
    hits = [
        PolicyHit(policy_id="p1", incident_id="i", applied_at=NOW, outcome="dismissed"),
    ]
    html = render_policy_hits_section(hits, {"p1": pol}, now_ts=NOW)
    assert "Wind in tree" in html
    assert "dismissal" in html
    assert "dismissed" in html


def test_policy_hits_section_unknown_policy_falls_back_to_id():
    hits = [
        PolicyHit(policy_id="unknown_pol", incident_id="i", applied_at=NOW, outcome="dismissed")
    ]
    html = render_policy_hits_section(hits, {}, now_ts=NOW)
    assert "unknown_pol" in html


# ─── top-level build_audit_chain_html ─────────────────────────────


@pytest.fixture
def stores():
    r = RulesStore(path=None)
    a = ActionStore(path=None)
    p = PolicyStore(path=None)
    yield r, a, p
    r.close()
    a.close()
    p.close()


def test_build_audit_chain_empty_when_all_stores_none():
    assert build_audit_chain_html(incident_id="i", now_ts=NOW) == ""


def test_build_audit_chain_empty_when_stores_present_but_no_hits(stores):
    r, a, p = stores
    html = build_audit_chain_html(
        incident_id="ghost",
        rules_store=r,
        action_store=a,
        policy_store=p,
        now_ts=NOW,
    )
    assert html == ""


def test_build_audit_chain_assembles_all_three_sections(stores):
    r, a, p = stores
    # Plant data
    rule = r.create(Rule(id="", name="Winston", mode="nl", intent_text=""))
    r.record_match(
        RuleMatch(
            rule_id=rule.id,
            incident_id="inc1",
            matched_at=NOW,
            severity="critical",
            confidence=0.9,
            reasoning="x",
            matched=True,
        )
    )
    a.log_protective(
        ProtectiveLogRow(
            incident_id="inc1",
            camera_id="back",
            ts=NOW,
            action_class="lock",
            service="lock.lock",
            target="lock.x",
            data_json=None,
            status="ok",
        )
    )
    pol = p.create(Policy(id="", kind="dismissal", name="Dog at front"))
    p.record_hit(
        PolicyHit(policy_id=pol.id, incident_id="inc1", applied_at=NOW, outcome="dismissed")
    )

    html = build_audit_chain_html(
        incident_id="inc1",
        rules_store=r,
        action_store=a,
        policy_store=p,
        now_ts=NOW,
    )
    # All three section headings present
    assert "Matched rules" in html
    assert "Protective actions" in html
    assert "Policy hits" in html
    # Section contents visible
    assert rule.id in html
    assert "lock.x" in html
    assert "Dog at front" in html


def test_build_audit_chain_partial_sections_when_only_some_stores_have_hits(stores):
    r, a, p = stores
    rule = r.create(Rule(id="", name="X", mode="nl", intent_text=""))
    r.record_match(
        RuleMatch(
            rule_id=rule.id,
            incident_id="inc",
            matched_at=NOW,
            severity="normal",
            confidence=0.7,
            reasoning="",
            matched=True,
        )
    )
    # action + policy stores empty for this incident
    html = build_audit_chain_html(
        incident_id="inc",
        rules_store=r,
        action_store=a,
        policy_store=p,
        now_ts=NOW,
    )
    assert "Matched rules" in html
    assert "Protective actions" not in html
    assert "Policy hits" not in html


def test_build_audit_chain_tolerates_store_exceptions():
    """If any store raises, we still produce the other sections."""

    class _BoomStore:
        def matches_for_incident(self, *_, **__):
            raise RuntimeError("simulated")

        def log_for_incident(self, *_, **__):
            raise RuntimeError("simulated")

        def hits_for_incident(self, *_, **__):
            raise RuntimeError("simulated")

    html = build_audit_chain_html(
        incident_id="i",
        rules_store=_BoomStore(),
        action_store=_BoomStore(),
        policy_store=_BoomStore(),
        now_ts=NOW,
    )
    # All sections empty since the stores threw; final assembly is empty string
    assert html == ""
