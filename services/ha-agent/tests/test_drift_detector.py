"""Drift detection (Part X §39 backstop #3) — pure detector + banner render."""

from __future__ import annotations

from dataclasses import dataclass, field

from kukiihome_ha_agent.drift_detector import (
    DriftSuggestion,
    detect_all_drift,
    detect_stale_dismissals,
    detect_stale_rules,
    detect_stale_transient_intents,
)
from kukiihome_ha_agent.web_ui.memory import GuidanceEntry, render_memory_page

NOW = 1_700_000_000.0
THIRTY_ONE_DAYS = 31 * 86400.0
EIGHT_DAYS = 8 * 86400.0
TWO_DAYS = 2 * 86400.0


# ─── Stub rule / policy types so the detector can be tested without
#     a real RulesStore + PolicyStore ────────────────────────────────


@dataclass
class _StubRule:
    id: str
    name: str = "x"
    created_at: float = 0.0
    last_matched_at: float | None = None


@dataclass
class _StubPolicy:
    id: str
    name: str
    kind: str
    created_at: float = 0.0
    last_applied_at: float | None = None
    apply_count: int = 0
    descriptor: dict = field(default_factory=dict)


# ─── detect_stale_rules ──────────────────────────────────────────


def test_stale_rule_never_matched_and_old_is_flagged():
    rules = [_StubRule(id="r1", name="Old", created_at=NOW - THIRTY_ONE_DAYS)]
    out = detect_stale_rules(rules, now_ts=NOW)
    assert len(out) == 1
    assert out[0].guidance_id == "r1"
    assert out[0].recommended_action == "convert_to_preference"


def test_stale_rule_recent_creation_skipped():
    rules = [_StubRule(id="r1", name="Fresh", created_at=NOW - TWO_DAYS)]
    out = detect_stale_rules(rules, now_ts=NOW)
    assert out == []


def test_stale_rule_recent_match_skipped():
    rules = [_StubRule(
        id="r1", name="Active", created_at=NOW - THIRTY_ONE_DAYS,
        last_matched_at=NOW - TWO_DAYS,
    )]
    out = detect_stale_rules(rules, now_ts=NOW)
    assert out == []


def test_stale_rule_old_match_still_flagged():
    """Matched ages ago but not recently — count as drift."""
    rules = [_StubRule(
        id="r1", name="Stale", created_at=NOW - 90 * 86400.0,
        last_matched_at=NOW - 60 * 86400.0,
    )]
    out = detect_stale_rules(rules, now_ts=NOW)
    assert len(out) == 1


def test_stale_rule_no_created_at_skipped():
    """Defensive: a rule with no created_at can't be assessed."""
    rules = [_StubRule(id="r1", created_at=0.0)]
    out = detect_stale_rules(rules, now_ts=NOW)
    assert out == []


# ─── detect_stale_dismissals ─────────────────────────────────────


def test_stale_dismissal_flagged_when_old_and_unused():
    pols = [_StubPolicy(
        id="p1", name="Dog at front", kind="dismissal",
        created_at=NOW - THIRTY_ONE_DAYS,
    )]
    out = detect_stale_dismissals(pols, now_ts=NOW)
    assert len(out) == 1
    assert out[0].recommended_action == "revoke"


def test_stale_dismissal_skipped_when_recently_applied():
    pols = [_StubPolicy(
        id="p1", name="x", kind="dismissal",
        created_at=NOW - THIRTY_ONE_DAYS,
        last_applied_at=NOW - TWO_DAYS,
    )]
    out = detect_stale_dismissals(pols, now_ts=NOW)
    assert out == []


def test_stale_dismissal_filters_to_dismissal_kind():
    """A transient_intent with the same age + zero applies shouldn't
    surface as a stale dismissal."""
    pols = [_StubPolicy(
        id="p1", name="x", kind="transient_intent",
        created_at=NOW - THIRTY_ONE_DAYS,
    )]
    out = detect_stale_dismissals(pols, now_ts=NOW)
    assert out == []


# ─── detect_stale_transient_intents ──────────────────────────────


def test_stale_fire_once_ti_flagged_after_seven_days():
    pols = [_StubPolicy(
        id="p1", name="Watch for Bob", kind="transient_intent",
        created_at=NOW - EIGHT_DAYS,
        descriptor={"fire_once": True}, apply_count=0,
    )]
    out = detect_stale_transient_intents(pols, now_ts=NOW)
    assert len(out) == 1
    assert out[0].recommended_action == "convert_to_rule"


def test_stale_ti_skipped_when_fire_once_false():
    """A non-fire_once TI shouldn't be flagged for conversion."""
    pols = [_StubPolicy(
        id="p1", name="x", kind="transient_intent",
        created_at=NOW - EIGHT_DAYS,
        descriptor={"fire_once": False},
    )]
    out = detect_stale_transient_intents(pols, now_ts=NOW)
    assert out == []


def test_stale_ti_skipped_when_fired():
    pols = [_StubPolicy(
        id="p1", name="x", kind="transient_intent",
        created_at=NOW - EIGHT_DAYS,
        descriptor={"fire_once": True}, apply_count=1,
    )]
    out = detect_stale_transient_intents(pols, now_ts=NOW)
    assert out == []


def test_stale_ti_skipped_when_recent():
    pols = [_StubPolicy(
        id="p1", name="x", kind="transient_intent",
        created_at=NOW - TWO_DAYS,
        descriptor={"fire_once": True},
    )]
    out = detect_stale_transient_intents(pols, now_ts=NOW)
    assert out == []


# ─── detect_all_drift ────────────────────────────────────────────


def test_detect_all_drift_aggregates():
    rules = [_StubRule(id="r1", name="Stale rule",
                        created_at=NOW - THIRTY_ONE_DAYS)]
    pols = [
        _StubPolicy(id="p1", name="Stale dismissal", kind="dismissal",
                     created_at=NOW - THIRTY_ONE_DAYS),
        _StubPolicy(id="p2", name="Stale TI", kind="transient_intent",
                     created_at=NOW - EIGHT_DAYS,
                     descriptor={"fire_once": True}),
    ]
    out = detect_all_drift(rules=rules, policies=pols, now_ts=NOW)
    assert len(out) == 3
    kinds = sorted(s.kind for s in out)
    assert kinds == ["dismissal", "rule", "transient_intent"]


def test_detect_all_drift_empty_when_nothing_stale():
    rules = [_StubRule(id="r1", created_at=NOW - TWO_DAYS)]
    pols = []
    assert detect_all_drift(rules=rules, policies=pols, now_ts=NOW) == []


# ─── Banner rendering on /memory ─────────────────────────────────


def test_memory_page_renders_drift_banner_when_present():
    suggestions = [DriftSuggestion(
        guidance_id="r1", kind="rule", name="Stale rule",
        summary="rule has not fired in 30+ days",
        recommended_action="convert_to_preference",
    )]
    html = render_memory_page(
        [], cut="by_context", drift_suggestions=suggestions, now_ts=NOW,
    )
    assert "drift-banner" in html
    assert "Stale rule" in html
    assert "has not fired" in html
    assert "(1 item)" in html


def test_memory_page_no_drift_no_banner():
    html = render_memory_page([], cut="by_context", drift_suggestions=[], now_ts=NOW)
    assert "drift-banner" not in html


def test_memory_page_drift_banner_html_escapes_entry_names():
    suggestions = [DriftSuggestion(
        guidance_id="x", kind="rule", name="<script>",
        summary="bad", recommended_action="x",
    )]
    html = render_memory_page(
        [], drift_suggestions=suggestions, now_ts=NOW,
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_memory_page_drift_banner_pluralizes():
    suggestions = [
        DriftSuggestion(guidance_id=f"x{i}", kind="rule", name=f"R{i}",
                         summary="x", recommended_action="y")
        for i in range(3)
    ]
    html = render_memory_page(
        [], drift_suggestions=suggestions, now_ts=NOW,
    )
    assert "(3 items)" in html


def test_memory_page_drift_banner_appears_above_entries():
    suggestions = [DriftSuggestion(
        guidance_id="r1", kind="rule", name="Drift item",
        summary="x", recommended_action="y",
    )]
    entries = [GuidanceEntry(
        guidance_id="r2", name="Normal entry",
        storage_class="rule", scope_summary="x",
    )]
    html = render_memory_page(
        entries, drift_suggestions=suggestions, now_ts=NOW,
    )
    # Drift banner content precedes the entry rows
    assert html.index("Drift item") < html.index("Normal entry")
