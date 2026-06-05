"""Audit-pass bugfixes (self-review found before user did).

Covers:
  - Drawer close link: RFC 3986 §5.3 query-only href bug, same family as
    the trigger had. Close now uses the current request_path so it
    returns to the same page with the drawer hidden.
  - context_from_boot storage_class classification for committed turns:
    earlier prefix-match logic (startswith 'rule' / 'policy') matched
    nothing because rule ids are bare slugs and policy ids are pol_.
  - commit_guidance cross-class refinement guard: refuse to
    refine_guidance when the proposal's storage_class doesn't match
    the existing entry's class — would have silently mis-written.
  - Diagnostics build_camera_summaries hoist: was called 3x, now 2x
    (counts + health rows) and the counts call shares its result.
"""

from __future__ import annotations

import pytest
from kukiihome_ha_agent.commit_guidance import (
    GuidanceStores,
    commit_guidance,
)
from kukiihome_ha_agent.dispatcher import _classify_guidance_id
from kukiihome_ha_agent.policy_store import PolicyStore
from kukiihome_ha_agent.preferences_store import PreferencesStore
from kukiihome_ha_agent.provenance_store import (
    PlacementProposal,
    ProvenanceStore,
)
from kukiihome_ha_agent.rules_store import RulesStore
from kukiihome_ha_agent.web_ui.drawer import render_drawer

NOW = 1_700_000_000.0


# ─── Drawer close link uses request_path ─────────────────────────


def test_drawer_close_returns_to_current_page_not_root():
    """Earlier: <a href='?'>close</a> resolved against <base href> and
    sent you to the add-on landing page on depth-2 pages. Fix: emit
    the current page's relative path so the close link is a no-op
    nav back to the same page (drawer query stripped)."""
    html = render_drawer(
        session=None,
        turns=[],
        request_path="/cameras/pool",
    )
    assert "href='cameras/pool'" in html
    assert "href='?'" not in html


def test_drawer_close_falls_back_to_memory_without_request_path():
    """Legacy callers / tests that don't pass request_path → safe
    fallback so the link is never broken."""
    html = render_drawer(session=None, turns=[])
    assert "href='memory'" in html


def test_drawer_close_handles_root_path():
    html = render_drawer(session=None, turns=[], request_path="/")
    # Root resolves to 'memory' fallback (empty after lstrip)
    assert "href='memory'" in html


def test_drawer_close_handles_alert_detail_path():
    html = render_drawer(
        session=None,
        turns=[],
        request_path="/alert/evt_42",
    )
    assert "href='alert/evt_42'" in html


# ─── _classify_guidance_id ──────────────────────────────────────


def test_classify_preferences_singleton():
    assert _classify_guidance_id("preferences:singleton") == "preference"
    assert _classify_guidance_id("preferences:vigilance") == "preference"


def test_classify_area_prefix():
    assert _classify_guidance_id("area:pool") == "area_posture"


def test_classify_policy_uuid_shape():
    """PolicyStore emits pol_<uuid8> ids — the old code was checking
    startswith('policy') which never matched."""
    assert _classify_guidance_id("pol_abc12345") == "policy"


def test_classify_rule_bare_slug():
    """Rules use the slugified name as id (no prefix)."""
    assert _classify_guidance_id("winston_alone_front_yard") == "rule"


def test_classify_empty_string():
    assert _classify_guidance_id("") == ""


def test_classify_unknown_shape_defaults_to_rule():
    """Defensive: anything that isn't preferences:/area:/pol_ is treated
    as a rule (the most common case)."""
    assert _classify_guidance_id("random_id_format") == "rule"


# ─── Cross-class refinement guard in commit_guidance ─────────────


@pytest.fixture
def stores():
    bundle = GuidanceStores(
        rules=RulesStore(path=None),
        preferences=PreferencesStore(path=None),
        policies=PolicyStore(path=None),
        provenance=ProvenanceStore(path=None),
    )
    yield bundle
    for s in (bundle.rules, bundle.preferences, bundle.policies, bundle.provenance):
        if s:
            s.close()


def _rule_proposal(**kw):
    base = dict(  # noqa: C408
        storage_class="rule",
        name="Test",
        scope={"area": "front_yard"},
        lifecycle="persistent",
        fire_affordance="alert",
        severity="normal",
        intent_text="x",
        reasoning="r",
        confidence=0.9,
    )
    base.update(kw)
    return PlacementProposal(**base)


def test_refine_same_class_routes_to_update_in_place(stores):
    """Baseline: refining a rule with another rule works."""
    gid = commit_guidance(
        _rule_proposal(), stores=stores, transcript_id="t0", user_utterance="initial"
    )
    refined = _rule_proposal(
        intent_text="UPDATED",
        scope={"area": "front_yard", "refines_guidance_id": gid},
    )
    gid2 = commit_guidance(refined, stores=stores, transcript_id="t1", user_utterance="refinement")
    assert gid2 == gid
    assert len(stores.rules.all_rules()) == 1
    assert "UPDATED" in stores.rules.get(gid).intent_text


def test_refine_across_classes_falls_through_to_fresh_create(stores):
    """The LLM occasionally emits a Preference proposal claiming to refine
    a Rule. Earlier code would call _update_preference() which doesn't
    take a guidance_id — silently writing to the singleton preference and
    leaving the rule untouched. Fix: detect class mismatch and fall
    through to a fresh create."""
    rule_id = commit_guidance(
        _rule_proposal(), stores=stores, transcript_id="t0", user_utterance="initial"
    )

    # Try to "refine" the rule with a preference proposal — should NOT
    # mutate either the rule OR the singleton preferences row in place;
    # should land as a fresh preference commit.
    bad_refine = PlacementProposal(
        storage_class="preference",
        name="Pretending to refine",
        scope={"refines_guidance_id": rule_id},
        lifecycle="persistent",
        fire_affordance="shift_prior",
        intent_text="something about preferences",
        reasoning="r",
        confidence=0.7,
    )
    returned_gid = commit_guidance(
        bad_refine,
        stores=stores,
        transcript_id="t1",
        user_utterance="x",
    )
    # Got a preferences id (fresh write to singleton), NOT the rule's id
    assert returned_gid != rule_id
    assert returned_gid.startswith("preferences:")
    # The original rule is unchanged
    rule = stores.rules.get(rule_id)
    assert "something about preferences" not in (rule.intent_text or "")
    # The preferences DID update (since _commit_preference writes
    # the singleton) — this is the expected outcome of falling through
    assert "something about preferences" in stores.preferences.get().what_i_care_about


def test_refine_policy_class_family_dismissal_and_transient_both_treat_as_policy(stores):
    """Both DismissalPolicy and TransientIntent live in PolicyStore
    under different `kind`s. The class-mismatch guard treats them as
    one family — refining a dismissal proposal against a transient_intent
    id should work (same pol_ id space, same store)."""
    initial = PlacementProposal(
        storage_class="transient_intent",
        name="Watch tonight",
        scope={"actor": "bob"},
        lifecycle="temporal",
        lifecycle_ttl_iso="2026-12-01T00:00:00+00:00",
        fire_affordance="alert",
        intent_text="watch",
        reasoning="r",
        confidence=0.9,
    )
    gid = commit_guidance(initial, stores=stores, transcript_id="t0", user_utterance="initial")
    refined = PlacementProposal(
        storage_class="dismissal_policy",
        name="Convert to dismiss",
        scope={"actor": "bob", "refines_guidance_id": gid},
        lifecycle="persistent",
        fire_affordance="dismiss",
        intent_text="actually dismiss",
        reasoning="r2",
        confidence=0.8,
    )
    gid2 = commit_guidance(refined, stores=stores, transcript_id="t1", user_utterance="refine")
    # Same family (both pol_ ids) → refinement allowed
    assert gid2 == gid


# ─── Diagnostics build_camera_summaries hoist ───────────────────


def test_diagnostics_hoists_camera_summary_calls():
    """Patch the module that diagnostics imports from (camera_data) so
    the function-local import sees our counting wrapper. Was called 3x
    before the hoist (perception sum + protective sum + cam health
    rows); now 2x (one shared for counts, one for health rows)."""
    from unittest.mock import patch

    from kukiihome_ha_agent.web_ui import camera_data
    from kukiihome_ha_agent.web_ui import diagnostics as diag

    calls = []
    real = camera_data.build_camera_summaries

    def counting(**kw):
        calls.append(kw)
        return real(**kw)

    class _FakeActions:
        def perception_for(self, _cid):
            return []

        def protective_for(self, _cid):
            return []

    with patch.object(camera_data, "build_camera_summaries", side_effect=counting):
        diag.build_diagnostics_vm(
            version="0.x",
            preprocessor_ok=None,
            preprocessor_url=None,
            ha_connected=False,
            ha_entities=0,
            rules_store=None,
            action_store=_FakeActions(),
            area_store=None,
            policy_store=None,
            registry_statuses=[],
            ha_loops=[],
            alerts=[],
            now_ts=NOW,
        )
    assert len(calls) == 2
