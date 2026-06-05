"""commit_guidance — single write surface across every guidance class
(Part X §37)."""

from __future__ import annotations

import pytest
from kukiihome_ha_agent.action_store import ActionStore
from kukiihome_ha_agent.area_store import Area, AreaStore
from kukiihome_ha_agent.commit_guidance import (
    GuidanceStores,
    commit_guidance,
    refine_guidance,
)
from kukiihome_ha_agent.policy_store import PolicyStore
from kukiihome_ha_agent.preferences_store import PreferencesStore
from kukiihome_ha_agent.provenance_store import (
    PlacementProposal,
    ProvenanceStore,
)
from kukiihome_ha_agent.rules_store import RulesStore

NOW = 1_700_000_000.0


@pytest.fixture
def stores():
    bundle = GuidanceStores(
        rules=RulesStore(path=None),
        preferences=PreferencesStore(path=None),
        policies=PolicyStore(path=None),
        actions=ActionStore(path=None),
        areas=AreaStore(path=None),
        provenance=ProvenanceStore(path=None),
    )
    yield bundle
    for s in (
        bundle.rules,
        bundle.preferences,
        bundle.policies,
        bundle.actions,
        bundle.areas,
        bundle.provenance,
    ):
        if s:
            s.close()


def _rule_proposal(**overrides):
    base: dict = dict(  # noqa: C408
        storage_class="rule",
        name="Winston unsupervised front yard",
        scope={"area": "front_yard", "actor": "winston"},
        lifecycle="persistent",
        fire_affordance="alert",
        severity="critical",
        intent_text="Winston in front yard without an adult — alert critical.",
        reasoning="persistent + explicit alert → Rule. Scope from utterance.",
        confidence=0.93,
    )
    base.update(overrides)
    return PlacementProposal(**base)


# ─── Rule routing ──────────────────────────────────────────────────


def test_commit_rule_writes_to_rules_store_and_provenance(stores):
    p = _rule_proposal()
    gid = commit_guidance(
        p,
        stores=stores,
        origin="conversation",
        transcript_id="trn_xyz",
        user_utterance="alert me when winston…",
        now_ts=NOW,
    )
    # The rule lands in RulesStore
    rule = stores.rules.get(gid)
    assert rule is not None
    assert rule.name == p.name
    assert rule.severity_static == "critical"
    assert "front_yard" in rule.scope.areas
    # Provenance row was written
    prov = stores.provenance.get_provenance(gid)
    assert prov is not None
    assert prov.origin == "conversation"
    assert prov.transcript_id == "trn_xyz"
    assert prov.placement_reasoning.startswith("persistent")


def test_commit_rule_no_severity_when_proposal_has_none(stores):
    p = _rule_proposal(severity=None)
    gid = commit_guidance(p, stores=stores, now_ts=NOW)
    rule = stores.rules.get(gid)
    # severity_static is None when no static severity provided (VLM reasons it)
    assert rule.severity_static is None


def test_commit_rule_form_origin(stores):
    gid = commit_guidance(
        _rule_proposal(),
        stores=stores,
        origin="form",
        now_ts=NOW,
    )
    prov = stores.provenance.get_provenance(gid)
    assert prov.origin == "form"


# ─── Preference routing ────────────────────────────────────────────


def test_commit_preference_updates_singleton_and_records_provenance(stores):
    p = PlacementProposal(
        storage_class="preference",
        name="What I care about",
        scope={},
        lifecycle="persistent",
        fire_affordance="shift_prior",
        intent_text="Winston is our dog. Don't alert on him.",
        reasoning="global + soft prior → Preference",
        confidence=0.9,
    )
    gid = commit_guidance(p, stores=stores, now_ts=NOW)
    assert gid == "preferences:singleton"
    prefs = stores.preferences.get()
    assert "Winston" in prefs.what_i_care_about
    assert stores.provenance.get_provenance(gid) is not None


def test_commit_preference_with_vigilance_scope(stores):
    p = PlacementProposal(
        storage_class="preference",
        name="Crank vigilance",
        scope={"vigilance": "high"},
        lifecycle="persistent",
        fire_affordance="shift_prior",
        intent_text="",  # vigilance-only change
        reasoning="vigilance update",
        confidence=0.95,
    )
    commit_guidance(p, stores=stores, now_ts=NOW)
    assert stores.preferences.get().vigilance == "high"


# ─── TransientIntent + DismissalPolicy routing ────────────────────


def test_commit_transient_intent_to_policies_with_ttl(stores):
    p = PlacementProposal(
        storage_class="transient_intent",
        name="Watch for Bob's car tonight",
        scope={"actor": "bob"},
        lifecycle="temporal",
        lifecycle_ttl_iso="2026-06-05T07:00:00+00:00",
        fire_affordance="alert",
        intent_text="Notify when bob's car arrives",
        reasoning="temporal + explicit fire → TransientIntent",
        confidence=0.88,
    )
    gid = commit_guidance(p, stores=stores, now_ts=NOW)
    pol = stores.policies.get(gid)
    assert pol is not None
    assert pol.kind == "transient_intent"
    assert pol.expires_at is not None
    assert pol.descriptor.get("actor") == "bob"


def test_commit_dismissal_policy_to_policies(stores):
    p = PlacementProposal(
        storage_class="dismissal_policy",
        name="Dog at front cam",
        scope={"camera": "front", "kind": "dog"},
        lifecycle="persistent",
        fire_affordance="dismiss",
        intent_text="Suppress dog detections on front cam",
        reasoning="suppress + persistent → DismissalPolicy",
        confidence=0.91,
    )
    gid = commit_guidance(p, stores=stores, now_ts=NOW)
    pol = stores.policies.get(gid)
    assert pol is not None and pol.kind == "dismissal"


def test_commit_situational_context_rides_on_policies(stores):
    p = PlacementProposal(
        storage_class="situational_context",
        name="Halloween 2026",
        scope={},
        lifecycle="temporal",
        lifecycle_ttl_iso="2026-11-01T07:00:00+00:00",
        fire_affordance="shift_prior",
        intent_text="Strangers at door are expected tonight",
        reasoning="temporal + soft prior → SituationalContext",
        confidence=0.9,
    )
    gid = commit_guidance(p, stores=stores, now_ts=NOW)
    pol = stores.policies.get(gid)
    assert pol is not None
    assert pol.descriptor.get("is_situational_context") is True


# ─── Area posture routing ────────────────────────────────────────


def test_commit_area_posture_updates_existing_area(stores):
    area = stores.areas.create(Area(id="", name="Pool", attention_mode="normal"))
    p = PlacementProposal(
        storage_class="area_posture",
        name="Pool = attention",
        scope={"area": area.id, "attention_mode": "attention"},
        lifecycle="persistent",
        fire_affordance="metadata",
        intent_text="Continuous monitoring on pool",
        reasoning="metadata scope change → area_posture",
        confidence=0.95,
    )
    gid = commit_guidance(p, stores=stores, now_ts=NOW)
    assert gid == f"area:{area.id}"
    refreshed = stores.areas.get(area.id)
    assert refreshed.attention_mode == "attention"


def test_commit_area_posture_requires_area_in_scope(stores):
    p = PlacementProposal(
        storage_class="area_posture",
        name="x",
        scope={"attention_mode": "attention"},
        lifecycle="persistent",
        fire_affordance="metadata",
        intent_text="",
        reasoning="r",
        confidence=1.0,
    )
    with pytest.raises(ValueError, match="must scope to an area"):
        commit_guidance(p, stores=stores, now_ts=NOW)


# ─── Refinement ────────────────────────────────────────────────────


def test_refine_rule_updates_in_place_and_appends_transcript(stores):
    gid = commit_guidance(
        _rule_proposal(),
        stores=stores,
        transcript_id="trn0",
        user_utterance="initial",
        now_ts=NOW,
    )
    refined = _rule_proposal(
        intent_text="UPDATED — unless my brother is with him",
        severity="normal",
    )
    refine_guidance(
        gid,
        refined,
        stores=stores,
        transcript_id="trn1",
        user_utterance="refinement",
        now_ts=NOW + 60,
    )
    rule = stores.rules.get(gid)
    assert "UPDATED" in rule.intent_text
    assert rule.severity_static == "normal"
    prov = stores.provenance.get_provenance(gid)
    assert "trn1" in prov.refinement_transcript_ids


def test_refine_unknown_guidance_raises(stores):
    with pytest.raises(ValueError):
        refine_guidance("ghost", _rule_proposal(), stores=stores, transcript_id="t", now_ts=NOW)


def test_refine_policy_updates_descriptor(stores):
    gid = commit_guidance(
        PlacementProposal(
            storage_class="dismissal_policy",
            name="Wind tree",
            scope={"camera": "front"},
            lifecycle="persistent",
            fire_affordance="dismiss",
            intent_text="suppress wind",
            reasoning="r",
            confidence=0.9,
        ),
        stores=stores,
        now_ts=NOW,
    )
    refine_guidance(
        gid,
        PlacementProposal(
            storage_class="dismissal_policy",
            name="Wind tree (updated)",
            scope={"camera": "back"},
            lifecycle="persistent",
            fire_affordance="dismiss",
            intent_text="suppress wind on back too",
            reasoning="r2",
            confidence=0.9,
        ),
        stores=stores,
        transcript_id="trn1",
        now_ts=NOW + 60,
    )
    pol = stores.policies.get(gid)
    assert pol.name == "Wind tree (updated)"
    assert pol.descriptor.get("camera") == "back"


# ─── Error cases ───────────────────────────────────────────────────


def test_commit_requires_provenance_store():
    empty = GuidanceStores()
    with pytest.raises(RuntimeError, match="ProvenanceStore"):
        commit_guidance(_rule_proposal(), stores=empty, now_ts=NOW)


def test_commit_unknown_storage_class_raises(stores):
    p = _rule_proposal()
    p.storage_class = "garbage"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="unknown storage_class"):
        commit_guidance(p, stores=stores, now_ts=NOW)


def test_commit_access_profile_not_yet_implemented(stores):
    p = _rule_proposal()
    p.storage_class = "access_profile"  # type: ignore[assignment]
    with pytest.raises(NotImplementedError):
        commit_guidance(p, stores=stores, now_ts=NOW)
