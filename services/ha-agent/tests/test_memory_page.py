"""/memory unified browse (Part IX §28) — view-model builder + page render
+ by-context / by-type grouping."""

from __future__ import annotations

from kukiihome_ha_agent.area_store import Area, AreaStore
from kukiihome_ha_agent.policy_store import Policy, PolicyStore
from kukiihome_ha_agent.preferences_store import PreferencesStore
from kukiihome_ha_agent.rules_store import Rule, RuleScope, RulesStore
from kukiihome_ha_agent.web_ui.memory import (
    GuidanceEntry,
    classify_to_contexts,
    group_by_context,
    group_by_type,
    render_memory_page,
)
from kukiihome_ha_agent.web_ui.memory_data import build_guidance_entries

NOW = 1_700_000_000.0


# ─── classify_to_contexts ────────────────────────────────────────


def _entry(**kw):
    base: dict = dict(  # noqa: C408
        guidance_id="x", name="X", storage_class="rule",
        scope_summary="", scope_fields={}, lifecycle="persistent",
    )
    base.update(kw)
    return GuidanceEntry(**base)


def test_preference_classifies_to_my_preferences_only():
    e = _entry(storage_class="preference")
    assert classify_to_contexts(e) == ["About my preferences"]


def test_actor_scope_classifies_to_about_actor():
    e = _entry(scope_fields={"actor": "winston", "actor_name": "Winston"})
    out = classify_to_contexts(e, known_actor_names={"Winston"})
    assert "About Winston" in out


def test_area_scope_classifies_to_about_area():
    e = _entry(scope_fields={"area": "front_yard", "area_name": "Front Yard"})
    assert "About the Front Yard" in classify_to_contexts(e)


def test_camera_scope_only_classifies_to_camera_when_no_area():
    e = _entry(scope_fields={"camera": "pool", "camera_name": "Pool Cam"})
    out = classify_to_contexts(e)
    assert "About Pool Cam" in out
    # When area is also present, camera doesn't double-bucket.
    e2 = _entry(scope_fields={
        "camera": "pool", "camera_name": "Pool Cam",
        "area": "pool_area", "area_name": "Pool",
    })
    out2 = classify_to_contexts(e2)
    assert "About Pool Cam" not in out2
    assert "About the Pool" in out2


def test_actor_and_area_both_buckets():
    e = _entry(scope_fields={
        "actor": "winston", "actor_name": "Winston",
        "area": "front_yard", "area_name": "Front Yard",
    })
    out = classify_to_contexts(e)
    assert "About Winston" in out
    assert "About the Front Yard" in out


def test_temporal_lifecycle_buckets_temporal_watches():
    e = _entry(lifecycle="temporal", scope_fields={"actor": "bob"})
    out = classify_to_contexts(e)
    assert "Temporal watches" in out
    assert "About bob" in out


def test_uncategorized_falls_back_to_other():
    e = _entry(scope_fields={})
    assert classify_to_contexts(e) == ["Other"]


# ─── group_by_context / group_by_type ──────────────────────────


def test_group_by_context_buckets_multi_membership_entries():
    e1 = _entry(name="A", scope_fields={"area": "pool", "area_name": "Pool"})
    e2 = _entry(
        name="B",
        scope_fields={
            "actor": "winston", "actor_name": "Winston",
            "area": "pool", "area_name": "Pool",
        },
    )
    groups = group_by_context([e1, e2])
    assert {x.name for x in groups["About the Pool"]} == {"A", "B"}
    assert {x.name for x in groups["About Winston"]} == {"B"}


def test_group_by_type_buckets_by_storage_class_label():
    a = _entry(name="A", storage_class="rule")
    b = _entry(name="B", storage_class="dismissal_policy")
    c = _entry(name="C", storage_class="rule")
    g = group_by_type([a, b, c])
    assert {x.name for x in g["Rule"]} == {"A", "C"}
    assert {x.name for x in g["Dismissal"]} == {"B"}


# ─── render_memory_page ─────────────────────────────────────────


def test_empty_renders_onboarding_copy():
    html = render_memory_page([], now_ts=NOW)
    assert "<h1>Memory</h1>" in html
    assert "No guidance yet" in html
    assert "Tell me what to watch for" in html


def test_render_drawer_trigger_visible():
    html = render_memory_page([], now_ts=NOW)
    assert "✨ Tell me what to watch for" in html
    assert "memory?drawer=1" in html


def test_by_context_default_groups_rendered():
    entries = [
        _entry(
            name="Winston rule",
            scope_fields={"actor": "winston", "actor_name": "Winston"},
        ),
        _entry(
            name="Pool rule",
            scope_fields={"area": "pool", "area_name": "Pool"},
        ),
    ]
    html = render_memory_page(entries, cut="by_context", now_ts=NOW)
    assert "About Winston" in html
    assert "About the Pool" in html
    assert "Winston rule" in html and "Pool rule" in html


def test_by_type_cut_groups_by_storage_class():
    entries = [
        _entry(name="A", storage_class="rule"),
        _entry(name="B", storage_class="dismissal_policy"),
    ]
    html = render_memory_page(entries, cut="by_type", now_ts=NOW)
    # Section headings reflect _CLASS_LABEL chips
    assert "Rule" in html and "Dismissal" in html


def test_cut_toggle_marks_active_link():
    html_ctx = render_memory_page([], cut="by_context", now_ts=NOW)
    html_typ = render_memory_page([], cut="by_type", now_ts=NOW)
    assert "class='active'" in html_ctx
    assert "class='active'" in html_typ
    # Make sure both anchor URLs are present
    assert "cut=by_context" in html_ctx and "cut=by_type" in html_ctx


def test_my_preferences_section_pinned_to_bottom():
    entries = [
        _entry(name="A pref", storage_class="preference"),
        _entry(name="Z area", scope_fields={"area": "z", "area_name": "Z"}),
    ]
    html = render_memory_page(entries, cut="by_context", now_ts=NOW)
    # "About the Z" appears before "About my preferences"
    assert html.index("About the Z") < html.index("About my preferences")


def test_origin_icons_render_per_origin():
    e = _entry(name="X", provenance_origin="conversation")
    html = render_memory_page([e], now_ts=NOW)
    assert "💬" in html

    e2 = _entry(name="Y", provenance_origin="form")
    html2 = render_memory_page([e2], now_ts=NOW)
    assert "✎" in html2


# ─── build_guidance_entries (data layer) ───────────────────────


def _open_stores():
    return (
        RulesStore(path=None),
        PreferencesStore(path=None),
        PolicyStore(path=None),
        AreaStore(path=None),
    )


def test_scope_from_rule_skips_bool_entries_in_areas():
    """Regression: a pre-validation-tightening rule had scope.areas=[True]
    persisted, and /memory 500'd on str.join. The renderer must drop
    non-string scope entries so old corrupted rules still render."""
    from types import SimpleNamespace

    from kukiihome_ha_agent.web_ui.memory_data import _scope_from_rule
    fake = SimpleNamespace(scope=SimpleNamespace(
        areas=[True, "front_yard", False],
        cameras=[],
        time_windows=[],
    ))
    summary, fields = _scope_from_rule(fake)
    assert "True" not in summary
    assert "False" not in summary
    assert "front_yard" in summary
    assert fields.get("area") == "front_yard"


def test_scope_from_rule_all_bool_areas_yields_empty():
    """When the only entries are bools, scope_summary should be empty
    rather than 'True' — keeps the /memory row clean."""
    from types import SimpleNamespace

    from kukiihome_ha_agent.web_ui.memory_data import _scope_from_rule
    fake = SimpleNamespace(scope=SimpleNamespace(
        areas=[True], cameras=[False], time_windows=[],
    ))
    summary, fields = _scope_from_rule(fake)
    assert summary == ""
    assert fields == {}


def test_scope_from_rule_handles_non_list_areas():
    """RulesStore should never serialize a non-list, but a malformed
    LLM proposal might still have slipped one through pre-validation.
    Coerce to []."""
    from types import SimpleNamespace

    from kukiihome_ha_agent.web_ui.memory_data import _scope_from_rule
    fake = SimpleNamespace(scope=SimpleNamespace(
        areas=True,  # not a list at all
        cameras=None,
        time_windows=[],
    ))
    summary, fields = _scope_from_rule(fake)
    assert summary == ""
    assert fields == {}


def test_scope_from_rule_keeps_int_and_float_as_strings():
    """Numbers get stringified — they're legitimate identifier shapes
    in some adapter configs (camera_id=1, area_id=2)."""
    from types import SimpleNamespace

    from kukiihome_ha_agent.web_ui.memory_data import _scope_from_rule
    fake = SimpleNamespace(scope=SimpleNamespace(
        areas=[1, "garage"], cameras=[], time_windows=[],
    ))
    summary, fields = _scope_from_rule(fake)
    assert "1" in summary
    assert "garage" in summary


def test_build_includes_rules_with_scope_summary():
    rs, prefs, pols, areas = _open_stores()
    try:
        rs.create(Rule(
            id="", name="Winston front yard", mode="nl",
            intent_text="alert",
            scope=RuleScope(areas=["front_yard"]),
        ))
        entries = build_guidance_entries(
            rules=rs.all_rules(), preferences=prefs.get(),
            policies=[], areas=[],
        )
        # 1 rule + 1 vigilance pref baseline (always present)
        assert len(entries) == 2
        rule_entry = next(e for e in entries if e.storage_class == "rule")
        assert rule_entry.name == "Winston front yard"
        assert "front_yard" in rule_entry.scope_summary
        assert rule_entry.scope_fields.get("area") == "front_yard"
    finally:
        rs.close()
        prefs.close()
        pols.close()
        areas.close()


def test_build_preferences_flattens_to_rows_per_field():
    rs, prefs, pols, areas = _open_stores()
    try:
        prefs.update(
            vigilance="high", what_i_care_about="Winston is our dog",
        )
        prefs.set_relationship("bob", "household")
        entries = build_guidance_entries(
            rules=[], preferences=prefs.get(), policies=[], areas=[],
        )
        names = {e.name for e in entries}
        assert "Vigilance baseline" in names
        assert "What I care about" in names
        assert "Actor relationships" in names
    finally:
        rs.close()
        prefs.close()
        pols.close()
        areas.close()


def test_build_policies_lift_descriptor_to_scope_fields():
    rs, prefs, pols, areas = _open_stores()
    try:
        pols.create(Policy(
            id="", kind="dismissal", name="Dog at front",
            descriptor={"camera": "front", "kind": "dog"},
        ))
        entries = build_guidance_entries(
            rules=[], preferences=None,
            policies=pols.all_policies(kind="dismissal"), areas=[],
        )
        e = next(e for e in entries if e.storage_class == "dismissal_policy")
        assert e.scope_fields.get("camera") == "front"
        assert "front" in e.scope_summary
    finally:
        rs.close()
        prefs.close()
        pols.close()
        areas.close()


def test_build_situational_context_detected_from_descriptor_marker():
    rs, prefs, pols, areas = _open_stores()
    try:
        pols.create(Policy(
            id="", kind="transient_intent", name="Halloween",
            descriptor={"is_situational_context": True},
            expires_at=NOW + 3600,
        ))
        entries = build_guidance_entries(
            rules=[], preferences=None,
            policies=pols.all_policies(now_ts=NOW),
            areas=[],
        )
        e = entries[0]
        assert e.storage_class == "situational_context"
        assert e.lifecycle == "temporal"
        assert e.expires_at is not None
    finally:
        rs.close()
        prefs.close()
        pols.close()
        areas.close()


def test_build_area_posture_emitted_only_for_non_default_attention_or_role():
    rs, prefs, pols, areas = _open_stores()
    try:
        # default — no row
        areas.create(Area(id="", name="Default", attention_mode="normal"))
        # attention=attention — emits a row
        areas.create(Area(id="", name="Pool", attention_mode="attention"))
        # role set, default attention — emits a row
        areas.create(Area(
            id="", name="Bedroom", attention_mode="normal", role="private",
        ))
        entries = build_guidance_entries(
            rules=[], preferences=None, policies=[],
            areas=areas.all_areas(),
        )
        names = {e.name for e in entries if e.storage_class == "area_posture"}
        assert "Pool posture" in names
        assert "Bedroom posture" in names
        assert "Default posture" not in names
    finally:
        rs.close()
        prefs.close()
        pols.close()
        areas.close()


def test_build_provenance_origin_pulled_from_provenance_store():
    from kukiihome_ha_agent.provenance_store import (
        Provenance,
        ProvenanceStore,
    )
    rs, prefs, pols, areas = _open_stores()
    prov = ProvenanceStore(path=None)
    try:
        rule = rs.create(Rule(
            id="", name="X", mode="nl", intent_text="",
        ))
        prov.record_provenance(Provenance(
            guidance_id=rule.id, origin="conversation",
            transcript_id="t1",
        ))
        entries = build_guidance_entries(
            rules=rs.all_rules(), preferences=None, policies=[], areas=[],
            provenance_store=prov,
        )
        rule_e = next(e for e in entries if e.storage_class == "rule")
        assert rule_e.provenance_origin == "conversation"
    finally:
        rs.close()
        prefs.close()
        pols.close()
        areas.close()
        prov.close()
