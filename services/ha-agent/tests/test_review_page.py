"""Review-page rendering + form parsing (pure; no HTTP)."""

from __future__ import annotations

from kukiihome_ha_agent.review_page import (
    parse_label_form,
    parse_merge_form,
    parse_reject_form,
    render_review_html,
    render_track_detail_html,
)


def _track(track_id, kind="person", status="unresolved", **kw):
    base = {
        "event_id": "e1", "camera_id": "pool", "track_id": track_id, "kind": kind,
        "n_frames": 19, "t0": 1.0, "t1": 2.0, "modalities": ["body", "gait"],
        "status": status, "subject_id": None, "subject_name": None,
        "confidence": None, "verdict": None,
    }
    base.update(kw)
    return base


def test_renderers_return_body_only_not_full_document():
    """Tasks 4+6: review_page renderers return body-only HTML; the route
    handlers wrap them in render_shell() so the shared nav + sticky header
    apply uniformly. Each renderer's output must not carry its own
    <!doctype>, <html>, or <body> tags."""
    for html in [
        render_review_html([], [], configured=False),
        render_review_html([_track("t1")], [], configured=True),
        render_track_detail_html(
            {"event_id": "e1", "track_id": "t1", "kind": "person",
             "camera_id": "pool", "n_frames": 5, "modalities": ["body"],
             "status": "unresolved", "candidates": [], "margin": None}
        ),
    ]:
        assert "<!doctype" not in html.lower()
        assert "<html" not in html.lower()
        assert "<body" not in html.lower()
        # but page-specific styles still travel with the body
        assert "<style>" in html


def test_unconfigured_shows_setup_notice():
    html = render_review_html([], [], configured=False)
    assert "preprocessor_url" in html
    assert "review/label" not in html  # no queue when unconfigured


def test_unresolved_track_renders_label_form_and_relative_thumb():
    html = render_review_html([_track("t1")], [], configured=True)
    assert "review/thumb/e1/t1.jpg" in html        # relative thumb (ingress-safe)
    assert "action='review/label'" in html          # relative form action
    assert "name='event_id' value='e1'" in html
    assert "name='track_id' value='t1'" in html
    assert "<span class='badge'>body</span>" in html and "gait" in html


def test_resolved_track_shows_name_not_form():
    html = render_review_html(
        [_track("t2", kind="pet", status="resolved",
                subject_id="rex", subject_name="Rex", confidence=0.82)],
        [{"subject_id": "rex", "kind": "pet", "display_name": "Rex", "species": "dog",
          "owner_id": None, "modalities": ["pet"], "appearances": 3}],
        configured=True,
    )
    assert "✓ Rex" in html
    assert "0.82" in html
    assert "Rex</b> (dog)" in html  # subject chip with species


def test_low_confidence_resolution_flagged():
    html = render_review_html(
        [_track("t3", status="resolved", subject_name="Bob", confidence=0.64)],
        [], configured=True,
    )
    assert "lowconf" in html  # < 0.70 styled distinctly


def test_html_is_escaped():
    html = render_review_html([_track("t<script>")], [], configured=True)
    assert "t<script>" not in html
    assert "t&lt;script&gt;" in html


def test_flash_rendered():
    html = render_review_html([], [], configured=True, flash="Labelled Alice")
    assert "Labelled Alice" in html


def test_resolved_card_has_reject_form():
    html = render_review_html(
        [_track("t2", status="resolved", subject_name="Alice", confidence=0.89)],
        [], configured=True,
    )
    assert "action='review/reject'" in html
    assert "✗ not them" in html
    assert "name='track_id' value='t2'" in html


def _subj(sid, name, kind="person"):
    return {"subject_id": sid, "kind": kind, "display_name": name, "species": None,
            "owner_id": None, "modalities": ["body"], "appearances": 1}


def test_merge_form_shown_with_two_subjects():
    html = render_review_html(
        [], [_subj("alice", "Alice"), _subj("bob", "Bob")], configured=True,
    )
    assert "action='review/merge'" in html
    assert "name='from_id'" in html and "name='into_id'" in html


def test_merge_form_hidden_with_one_subject():
    html = render_review_html([], [_subj("alice", "Alice")], configured=True)
    assert "action='review/merge'" not in html


def test_parse_reject_form():
    assert parse_reject_form({"event_id": "e", "track_id": "t"}) == {
        "event_id": "e", "track_id": "t",
    }
    assert parse_reject_form({"event_id": "e"}) is None


def test_parse_merge_form():
    assert parse_merge_form({"from_id": "a", "into_id": "b"}) == {
        "from_id": "a", "into_id": "b",
    }
    assert parse_merge_form({"from_id": "a", "into_id": "a"}) is None  # self-merge
    assert parse_merge_form({"from_id": "a"}) is None


def test_card_thumbnail_links_to_detail():
    html = render_review_html([_track("t1")], [], configured=True)
    assert "review-track?e=e1&t=t1" in html  # thumbnail opens the track-detail page


# ─── track-detail page (T3) ─────────────────────────────────────────


def _detail(**kw):
    base = {
        "event_id": "e1", "track_id": "t1", "kind": "person", "camera_id": "pool",
        "n_frames": 15, "modalities": ["body", "face"], "status": "unresolved",
        "subject_id": None, "subject_name": None, "confidence": None,
        "candidates": [
            {"subject_id": "alice", "name": "Alice", "kind": "person",
             "score": 0.78, "modality": "face"},
            {"subject_id": "bob", "name": "Bob", "kind": "person",
             "score": 0.41, "modality": "body"},
        ],
        "margin": 0.37,
    }
    base.update(kw)
    return base


def test_track_detail_clip_and_candidates():
    html = render_track_detail_html(_detail())
    assert "review-track-clip?e=e1&t=t1" in html          # animated clip
    assert "Confirm Alice" in html and "Confirm Bob" in html
    assert "action='review/label'" in html                # confirm posts a label
    assert "0.78" in html and "0.41" in html              # similarity scores
    assert "label as someone new" in html                 # fallback
    assert "href='review'" in html                        # back link


def test_track_detail_no_candidates_still_labelable():
    html = render_track_detail_html(_detail(candidates=[], margin=None))
    assert "No one enrolled to compare against yet" in html
    assert "label as someone new" in html


def test_track_detail_resolved_shows_reject():
    html = render_track_detail_html(
        _detail(status="resolved", subject_name="Alice", confidence=0.9)
    )
    assert "✓ Alice" in html
    assert "action='review/reject'" in html


def test_parse_label_form():
    assert parse_label_form({"event_id": "e", "track_id": "t", "name": "Alice"}) == {
        "event_id": "e", "track_id": "t", "name": "Alice",
    }
    # missing required → None
    assert parse_label_form({"event_id": "e", "track_id": "t", "name": ""}) is None
    assert parse_label_form({"name": "x"}) is None
    # optional pet fields carried through
    p = parse_label_form(
        {"event_id": "e", "track_id": "t", "name": "Rex", "species": "dog", "kind": "pet"}
    )
    assert p is not None and p["species"] == "dog" and p["kind"] == "pet"
