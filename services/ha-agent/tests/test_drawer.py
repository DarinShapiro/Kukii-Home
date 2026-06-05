"""Conversational drawer renderer (Part X §34)."""

from __future__ import annotations

from kukiihome_ha_agent.provenance_store import (
    PlacementProposal,
    ProvenanceStore,
)
from kukiihome_ha_agent.web_ui.drawer import (
    is_drawer_requested,
    render_drawer,
)

NOW = 1_700_000_000.0


def _store():
    return ProvenanceStore(path=None)


# ─── render_drawer ────────────────────────────────────────────────


def test_render_drawer_no_session_shows_onboarding():
    html = render_drawer(session=None, turns=[], now_ts=NOW)
    assert "Conversation" in html
    assert "Tell me what to watch for" in html
    # Composer is always present
    assert "name='utterance'" in html


def test_render_drawer_empty_session_shows_fresh_prompt():
    store = _store()
    sess = store.open_session("alice", now_ts=NOW)
    html = render_drawer(session=sess, turns=[], now_ts=NOW)
    assert "Fresh session" in html or "What's on your mind" in html
    # Hidden session_id propagates to the composer
    assert sess.id in html
    store.close()


def test_render_drawer_with_user_turn():
    store = _store()
    sess = store.open_session("alice", now_ts=NOW)
    t = store.append_turn(
        sess.id,
        role="user",
        utterance="watch for Bob",
        now_ts=NOW,
    )
    html = render_drawer(session=sess, turns=[t], now_ts=NOW)
    assert "watch for Bob" in html
    assert "drawer-turn user" in html
    store.close()


def test_render_drawer_with_proposal_card():
    store = _store()
    sess = store.open_session("alice", now_ts=NOW)
    user_turn = store.append_turn(
        sess.id,
        role="user",
        utterance="Alert when Bob arrives",
        now_ts=NOW,
    )
    proposal = PlacementProposal(
        storage_class="rule",
        name="Bob arrives",
        scope={"actor": "bob"},
        lifecycle="persistent",
        fire_affordance="alert",
        intent_text="alert when bob arrives",
        reasoning="explicit fire + persistent → Rule",
        confidence=0.92,
        severity="normal",
    )
    sys_turn = store.append_turn(
        sess.id,
        role="system",
        utterance=proposal.reasoning,
        proposal_json=proposal.to_json(),
        now_ts=NOW + 1,
    )
    html = render_drawer(
        session=sess,
        turns=[user_turn, sys_turn],
        now_ts=NOW + 60,
    )
    # Proposal preview card surfaces
    assert "Bob arrives" in html
    assert "Rule" in html
    assert "explicit fire + persistent" in html
    # Confirm form action exists when not in disambiguation mode
    assert "api/drawer/confirm" in html
    assert "name='turn_id'" in html
    assert sys_turn.id in html
    store.close()


def test_render_drawer_disambiguation_shows_questions_no_confirm():
    store = _store()
    sess = store.open_session("alice", now_ts=NOW)
    proposal = PlacementProposal(
        storage_class="rule",
        name="Ambiguous",
        scope={},
        lifecycle="persistent",
        fire_affordance="alert",
        intent_text="watch for stuff",
        reasoning="uncertain — asking lifecycle + fire affordance",
        confidence=0.4,
        clarifying_questions=["Just tonight, or always?", "Ping you, or shift my judging?"],
    )
    sys_turn = store.append_turn(
        sess.id,
        role="system",
        utterance=proposal.reasoning,
        proposal_json=proposal.to_json(),
        now_ts=NOW,
    )
    html = render_drawer(session=sess, turns=[sys_turn], now_ts=NOW)
    assert "Just tonight, or always" in html
    assert "Ping you" in html
    # When disambiguation is needed, no Confirm form appears
    assert "type='submit'>Confirm" not in html
    store.close()


def test_render_drawer_committed_marker():
    store = _store()
    sess = store.open_session("alice", now_ts=NOW)
    t = store.append_turn(
        sess.id,
        role="system",
        utterance="committed",
        committed_to="rule_xyz",
        now_ts=NOW,
    )
    html = render_drawer(session=sess, turns=[t], now_ts=NOW)
    assert "committed" in html
    assert "rule_xyz" in html
    store.close()


def test_render_drawer_alert_context_strip_when_set():
    store = _store()
    sess = store.open_session("alice", alert_context="alert_42", now_ts=NOW)
    html = render_drawer(
        session=sess,
        turns=[],
        alert_context="alert_42",
        now_ts=NOW,
    )
    assert "alert_42" in html
    assert "drawer-context" in html
    store.close()


def test_render_drawer_escapes_utterance():
    store = _store()
    sess = store.open_session("alice", now_ts=NOW)
    t = store.append_turn(
        sess.id,
        role="user",
        utterance="<script>alert(1)</script>",
        now_ts=NOW,
    )
    html = render_drawer(session=sess, turns=[t], now_ts=NOW)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    store.close()


# ─── is_drawer_requested ──────────────────────────────────────────


def test_is_drawer_requested_truthy_values():
    for v in ("1", "true", "yes", "open"):
        assert is_drawer_requested({"drawer": v})


def test_is_drawer_requested_falsy_when_absent_or_zero():
    assert not is_drawer_requested({})
    assert not is_drawer_requested({"drawer": ""})
    assert not is_drawer_requested({"drawer": "0"})
    assert not is_drawer_requested({"other": "1"})
