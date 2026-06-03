"""Review-page rendering + form parsing (pure; no HTTP)."""

from __future__ import annotations

from kukiihome_ha_agent.review_page import parse_label_form, render_review_html


def _track(track_id, kind="person", status="unresolved", **kw):
    base = {
        "event_id": "e1", "camera_id": "pool", "track_id": track_id, "kind": kind,
        "n_frames": 19, "t0": 1.0, "t1": 2.0, "modalities": ["body", "gait"],
        "status": status, "subject_id": None, "subject_name": None,
        "confidence": None, "verdict": None,
    }
    base.update(kw)
    return base


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
