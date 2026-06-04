"""/identities — Review + Enrolled list + per-identity detail (Part IX §29)."""

from __future__ import annotations

from kukiihome_ha_agent.web_ui.identities import (
    IdentityDetailViewModel,
    IdentitySubject,
    build_identity_subjects,
    filter_guidance_for_subject,
    render_identities_list,
    render_identity_detail,
)
from kukiihome_ha_agent.web_ui.memory import GuidanceEntry

# ─── build_identity_subjects ──────────────────────────────────────


def test_build_subjects_from_subjects_dict_wrapper():
    payload = {"subjects": [
        {"subject_id": "bob", "kind": "person", "display_name": "Bob",
         "modalities": ["face", "body"], "appearances": 12},
        {"subject_id": "winston", "kind": "pet", "display_name": "Winston",
         "species": "dog", "modalities": ["pet", "gait"], "appearances": 7},
    ]}
    subs = build_identity_subjects(payload)
    assert len(subs) == 2
    assert subs[0].kind == "person"
    assert subs[1].species == "dog"


def test_build_subjects_from_raw_list_payload():
    payload = [
        {"subject_id": "a", "display_name": "A", "kind": "person"},
    ]
    subs = build_identity_subjects(payload)
    assert subs[0].subject_id == "a"


def test_build_subjects_handles_none_and_empty():
    assert build_identity_subjects(None) == []
    assert build_identity_subjects({"subjects": []}) == []


def test_build_subjects_skips_non_dict_records():
    subs = build_identity_subjects({"subjects": ["bad", None, {"subject_id": "ok"}]})
    assert len(subs) == 1
    assert subs[0].subject_id == "ok"


def test_build_subjects_defaults_missing_fields():
    s = build_identity_subjects({"subjects": [{"subject_id": "x"}]})[0]
    assert s.kind == "person"
    assert s.display_name == "x"   # falls back to subject_id
    assert s.appearances == 0
    assert s.modalities == []


# ─── render_identities_list ──────────────────────────────────────


def test_list_empty_state_explains_review_flow():
    html = render_identities_list([], unresolved_count=0)
    assert "<h1>Identities</h1>" in html
    assert "No identities enrolled" in html
    assert "Review" in html


def test_list_renders_review_and_enrolled_tabs_with_counts():
    subs = [IdentitySubject(subject_id="bob", kind="person",
                              display_name="Bob")]
    html = render_identities_list(subs, unresolved_count=3, tab="enrolled")
    # Both tab labels with their counts
    assert "Review <span class='muted'>(3)</span>" in html
    assert "Enrolled <span class='muted'>(1)</span>" in html
    # Active class on the selected tab
    assert "class='active'" in html


def test_list_subjects_sorted_people_then_pets_then_vehicles():
    subs = [
        IdentitySubject(subject_id="car1", kind="vehicle",
                         display_name="Tesla"),
        IdentitySubject(subject_id="winston", kind="pet",
                         display_name="Winston"),
        IdentitySubject(subject_id="alice", kind="person",
                         display_name="Alice"),
        IdentitySubject(subject_id="bob", kind="person",
                         display_name="Bob"),
    ]
    html = render_identities_list(subs)
    # Order: people (alphabetical within), then pet, then vehicle
    assert html.index("Alice") < html.index("Bob") < html.index("Winston")
    assert html.index("Winston") < html.index("Tesla")


def test_list_html_escapes_subject_names():
    html = render_identities_list(
        [IdentitySubject(subject_id="x", kind="person",
                          display_name="<script>")],
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_list_subject_tile_links_to_detail():
    subs = [IdentitySubject(subject_id="bob_42", kind="person",
                              display_name="Bob")]
    html = render_identities_list(subs)
    assert "identities/bob_42" in html


# ─── render_identity_detail ──────────────────────────────────────


def _vm(**kw):
    base = IdentitySubject(
        subject_id="bob", kind="person", display_name="Bob",
        modalities=["face", "body"], appearances=12,
    )
    return IdentityDetailViewModel(subject=base, **kw)


def test_detail_includes_all_sections():
    html = render_identity_detail(_vm())
    for heading in (
        "Enrolled templates",
        "Access profile",
        "Linked guidance",
        "Operations",
    ):
        assert f"<h2>{heading}</h2>" in html


def test_detail_shows_modality_chips():
    html = render_identity_detail(_vm())
    assert "face" in html
    assert "body" in html


def test_detail_no_modalities_shows_empty_marker():
    s = IdentitySubject(subject_id="x", kind="person",
                         display_name="X", modalities=[])
    html = render_identity_detail(IdentityDetailViewModel(subject=s))
    assert "no enrolled templates" in html


def test_detail_back_link_to_identities_list():
    html = render_identity_detail(_vm())
    assert "href='identities'" in html


def test_detail_stop_recognizing_button_disabled_pending_preproc_endpoint():
    html = render_identity_detail(_vm())
    assert "Stop recognizing" in html
    assert "disabled" in html  # the button is disabled in v1


def test_detail_linked_guidance_empty_state():
    html = render_identity_detail(_vm())
    assert "No rules or policies reference this identity" in html


def test_detail_linked_guidance_lists_entries():
    vm = _vm(linked_guidance=[
        GuidanceEntry(
            guidance_id="r1", name="Bob arrives", storage_class="rule",
            scope_summary="front_door", detail_url="memory?cut=by_type#r1",
        ),
        GuidanceEntry(
            guidance_id="p1", name="Watch for Bob tonight",
            storage_class="transient_intent",
            scope_summary="tonight", detail_url="memory#p1",
        ),
    ])
    html = render_identity_detail(vm)
    assert "Bob arrives" in html
    assert "Watch for Bob tonight" in html
    assert "rule" in html
    assert "transient_intent" in html


def test_detail_includes_kind_icon_for_pets():
    s = IdentitySubject(subject_id="winston", kind="pet",
                         display_name="Winston", species="dog")
    html = render_identity_detail(IdentityDetailViewModel(subject=s))
    assert "🐾" in html or "Winston" in html


# ─── filter_guidance_for_subject ─────────────────────────────────


def _entry(actor=None, actor_name=None, scope_summary=""):
    return GuidanceEntry(
        guidance_id="x", name="x", storage_class="rule",
        scope_summary=scope_summary,
        scope_fields={"actor": actor or "", "actor_name": actor_name or ""},
    )


def test_filter_matches_by_actor_id():
    bob = IdentitySubject(subject_id="bob", kind="person", display_name="Bob")
    entries = [_entry(actor="bob"), _entry(actor="alice")]
    out = filter_guidance_for_subject(entries, subject=bob)
    assert len(out) == 1


def test_filter_matches_by_actor_name():
    bob = IdentitySubject(subject_id="bob_42", kind="person",
                          display_name="Bob")
    entries = [_entry(actor_name="Bob"), _entry(actor_name="Alice")]
    out = filter_guidance_for_subject(entries, subject=bob)
    assert len(out) == 1


def test_filter_matches_case_insensitively():
    bob = IdentitySubject(subject_id="bob", kind="person", display_name="Bob")
    entries = [_entry(actor="BOB"), _entry(actor="bOb")]
    out = filter_guidance_for_subject(entries, subject=bob)
    assert len(out) == 2


def test_filter_fallback_to_scope_summary_substring():
    bob = IdentitySubject(subject_id="bob", kind="person", display_name="Bob")
    entries = [
        _entry(scope_summary="when Bob arrives at the front door"),
    ]
    out = filter_guidance_for_subject(entries, subject=bob)
    assert len(out) == 1


def test_filter_empty_when_no_match():
    bob = IdentitySubject(subject_id="bob", kind="person", display_name="Bob")
    entries = [_entry(actor="alice"), _entry(actor_name="Carol")]
    out = filter_guidance_for_subject(entries, subject=bob)
    assert out == []
