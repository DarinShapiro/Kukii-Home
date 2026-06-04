"""ProvenanceStore — sessions + transcripts + per-guidance audit trail
+ PlacementProposal schema validation (Part X §36)."""

from __future__ import annotations

import json

import pytest
from kukiihome_ha_agent.provenance_store import (
    SESSION_IDLE_TIMEOUT_S,
    PlacementProposal,
    Provenance,
    ProvenanceStore,
    validate_proposal,
)

NOW = 1_700_000_000.0


@pytest.fixture
def store():
    s = ProvenanceStore(path=None)
    yield s
    s.close()


# ─── Sessions ──────────────────────────────────────────────────────


def test_active_session_returns_none_for_new_user(store):
    assert store.active_session_for("alice") is None


def test_open_session_creates_row(store):
    s = store.open_session("alice", page_context="memory", now_ts=NOW)
    assert s.id.startswith("sess_")
    assert s.user_id == "alice"
    assert s.opened_at == NOW
    fetched = store.active_session_for("alice", now_ts=NOW)
    assert fetched is not None and fetched.id == s.id


def test_active_session_filters_by_user(store):
    a = store.open_session("alice", now_ts=NOW)
    store.open_session("bob", now_ts=NOW)
    out = store.active_session_for("alice", now_ts=NOW)
    assert out is not None and out.id == a.id


def test_idle_session_auto_closes(store):
    s = store.open_session("alice", now_ts=NOW)
    # Plant an old turn so last_activity = NOW (older than the idle window)
    store.append_turn(s.id, role="user", utterance="hi", now_ts=NOW)
    # Far future — well past the idle timeout
    fetched = store.active_session_for(
        "alice", now_ts=NOW + SESSION_IDLE_TIMEOUT_S + 60,
    )
    assert fetched is None


def test_active_session_keeps_recent_active(store):
    s = store.open_session("alice", now_ts=NOW)
    store.append_turn(s.id, role="user", utterance="hi", now_ts=NOW)
    fetched = store.active_session_for(
        "alice", now_ts=NOW + SESSION_IDLE_TIMEOUT_S - 60,
    )
    assert fetched is not None


def test_get_or_open_reattaches_when_active(store):
    s1 = store.open_session("alice", now_ts=NOW)
    s2 = store.get_or_open_session("alice", now_ts=NOW + 60)
    assert s2.id == s1.id


def test_get_or_open_opens_new_when_idle(store):
    s1 = store.open_session("alice", now_ts=NOW)
    store.append_turn(s1.id, role="user", utterance="hi", now_ts=NOW)
    s2 = store.get_or_open_session(
        "alice", now_ts=NOW + SESSION_IDLE_TIMEOUT_S + 60,
    )
    assert s2.id != s1.id


def test_close_session_marks_closed(store):
    s = store.open_session("alice", now_ts=NOW)
    store.close_session(s.id, now_ts=NOW + 60)
    assert store.active_session_for("alice", now_ts=NOW + 120) is None


# ─── Transcript turns ─────────────────────────────────────────────


def test_append_turn_assigns_monotonic_indices(store):
    s = store.open_session("alice", now_ts=NOW)
    t1 = store.append_turn(s.id, role="user", utterance="hi", now_ts=NOW)
    t2 = store.append_turn(
        s.id, role="system", utterance="proposal",
        proposal_json='{"a":1}', now_ts=NOW + 1,
    )
    assert t1.turn_index == 0
    assert t2.turn_index == 1


def test_turns_for_session_in_order(store):
    s = store.open_session("alice", now_ts=NOW)
    for i, u in enumerate(["one", "two", "three"]):
        store.append_turn(s.id, role="user", utterance=u, now_ts=NOW + i)
    turns = store.turns_for_session(s.id)
    assert [t.utterance for t in turns] == ["one", "two", "three"]


def test_get_turn_returns_full_row(store):
    s = store.open_session("alice", now_ts=NOW)
    t = store.append_turn(
        s.id, role="system", utterance="ok",
        proposal_json='{"x":1}', committed_to="rule_abc", now_ts=NOW,
    )
    fetched = store.get_turn(t.id)
    assert fetched is not None
    assert fetched.proposal_json == '{"x":1}'
    assert fetched.committed_to == "rule_abc"


# ─── Provenance per guidance entry ────────────────────────────────


def test_record_and_get_provenance_roundtrip(store):
    prov = Provenance(
        guidance_id="rule_abc", origin="conversation",
        transcript_id="trn_xyz",
        user_utterance="I want to know when Bob arrives",
        placement_reasoning="explicit fire + persistent → Rule",
        user_confirmed_at=NOW,
    )
    store.record_provenance(prov)
    out = store.get_provenance("rule_abc")
    assert out is not None
    assert out.origin == "conversation"
    assert out.user_utterance == "I want to know when Bob arrives"
    assert out.refinement_transcript_ids == []


def test_append_refinement_extends_list(store):
    store.record_provenance(Provenance(
        guidance_id="rule_abc", origin="conversation",
        transcript_id="trn_xyz",
    ))
    store.append_refinement("rule_abc", "trn_ref1")
    store.append_refinement("rule_abc", "trn_ref2")
    out = store.get_provenance("rule_abc")
    assert out.refinement_transcript_ids == ["trn_ref1", "trn_ref2"]


def test_append_refinement_dedupes(store):
    store.record_provenance(Provenance(
        guidance_id="rule_abc", origin="conversation", transcript_id="t0",
    ))
    store.append_refinement("rule_abc", "trn_ref1")
    store.append_refinement("rule_abc", "trn_ref1")  # duplicate
    out = store.get_provenance("rule_abc")
    assert out.refinement_transcript_ids == ["trn_ref1"]


def test_append_refinement_returns_none_for_unknown(store):
    assert store.append_refinement("ghost", "x") is None


def test_backfill_pre_provenance_marks_existing(store):
    n = store.backfill_pre_provenance(["a", "b", "c"])
    assert n == 3
    for gid in ("a", "b", "c"):
        p = store.get_provenance(gid)
        assert p is not None
        assert p.origin == "pre_provenance"


def test_backfill_skips_already_provenant(store):
    store.record_provenance(Provenance(
        guidance_id="a", origin="conversation", transcript_id="t0",
    ))
    n = store.backfill_pre_provenance(["a", "b"])
    assert n == 1  # only 'b' got backfilled
    assert store.get_provenance("a").origin == "conversation"


def test_persist_to_disk_survives_reopen(tmp_path):
    db = tmp_path / "prov.db"
    s1 = ProvenanceStore(path=str(db))
    sess = s1.open_session("alice", now_ts=NOW)
    s1.append_turn(sess.id, role="user", utterance="hi", now_ts=NOW)
    s1.record_provenance(Provenance(
        guidance_id="rule_1", origin="conversation", transcript_id="x",
    ))
    s1.close()
    s2 = ProvenanceStore(path=str(db))
    out = s2.get_provenance("rule_1")
    assert out is not None and out.origin == "conversation"
    s2.close()


# ─── PlacementProposal schema validation ──────────────────────────


def _good_proposal_dict():
    return {
        "storage_class": "rule",
        "name": "Winston front yard",
        "scope": {"area": "front_yard"},
        "lifecycle": "persistent",
        "fire_affordance": "alert",
        "intent_text": "alert when winston in front yard",
        "reasoning": "explicit fire + persistent → Rule",
        "confidence": 0.92,
        "severity": "critical",
    }


def test_validate_proposal_happy_path():
    p = validate_proposal(_good_proposal_dict())
    assert p.storage_class == "rule"
    assert p.severity == "critical"


def test_validate_proposal_rejects_non_dict():
    with pytest.raises(ValueError):
        validate_proposal("not a dict")


def test_validate_proposal_missing_required():
    d = _good_proposal_dict()
    del d["name"]
    with pytest.raises(ValueError, match="missing required"):
        validate_proposal(d)


def test_validate_proposal_bad_enum():
    d = _good_proposal_dict()
    d["storage_class"] = "garbage"
    with pytest.raises(ValueError, match="bad storage_class"):
        validate_proposal(d)


def test_validate_proposal_temporal_requires_ttl():
    d = _good_proposal_dict()
    d["lifecycle"] = "temporal"
    with pytest.raises(ValueError, match="temporal requires"):
        validate_proposal(d)


def test_validate_proposal_fire_once_allowed_without_ttl():
    d = _good_proposal_dict()
    d["lifecycle"] = "fire_once"
    p = validate_proposal(d)
    assert p.lifecycle == "fire_once"


def test_validate_proposal_bad_severity():
    d = _good_proposal_dict()
    d["severity"] = "garbage"
    with pytest.raises(ValueError, match="bad severity"):
        validate_proposal(d)


def test_proposal_to_from_json_roundtrip():
    p = validate_proposal(_good_proposal_dict())
    blob = p.to_json()
    p2 = PlacementProposal.from_json(blob)
    assert p2.storage_class == p.storage_class
    assert p2.name == p.name
    assert p2.scope == p.scope


def test_proposal_needs_disambiguation():
    high = validate_proposal({**_good_proposal_dict(), "confidence": 0.92})
    low = validate_proposal({**_good_proposal_dict(), "confidence": 0.4})
    assert not high.needs_disambiguation()
    assert low.needs_disambiguation()


def test_proposal_to_json_carries_clarifying_questions():
    p = validate_proposal({
        **_good_proposal_dict(),
        "clarifying_questions": ["Just tonight, or always?"],
        "confidence": 0.55,
    })
    blob = json.loads(p.to_json())
    assert blob["clarifying_questions"] == ["Just tonight, or always?"]
