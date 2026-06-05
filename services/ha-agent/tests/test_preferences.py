"""Preferences (Part VI A) — store + intent page section."""

from __future__ import annotations

import pytest
from kukiihome_ha_agent.preferences_store import Preferences, PreferencesStore
from kukiihome_ha_agent.web_ui.intent import _preferences_section, render_intent_page


@pytest.fixture
def store():
    s = PreferencesStore(path=None)
    yield s
    s.close()


# ─── store ─────────────────────────────────────────────────────────


def test_get_returns_defaults_on_fresh_store(store):
    prefs = store.get()
    assert prefs.vigilance == "normal"
    assert prefs.what_i_care_about == ""
    assert prefs.quiet_hours == []
    assert prefs.relationships == {}


def test_update_vigilance_persists(store):
    store.update(vigilance="high")
    assert store.get().vigilance == "high"


def test_update_what_i_care_about_persists(store):
    store.update(what_i_care_about="Winston is our dog. Don't alert on him.")
    assert "Winston" in store.get().what_i_care_about


def test_update_quiet_hours_roundtrip(store):
    windows = [{"days": ["sat", "sun"], "start": "00:00", "end": "07:00"}]
    store.update(quiet_hours=windows)
    assert store.get().quiet_hours == windows


def test_update_partial_preserves_other_fields(store):
    store.update(vigilance="low", what_i_care_about="A")
    store.update(vigilance="high")  # only updates vigilance
    out = store.get()
    assert out.vigilance == "high"
    assert out.what_i_care_about == "A"  # not nuked


def test_set_relationship_persists(store):
    store.set_relationship("bob", "household")
    store.set_relationship("alice", "guest")
    rels = store.get().relationships
    assert rels == {"bob": "household", "alice": "guest"}


def test_set_relationship_upserts(store):
    store.set_relationship("bob", "household")
    store.set_relationship("bob", "guest")
    assert store.get().relationships["bob"] == "guest"


def test_clear_relationship_removes(store):
    store.set_relationship("bob", "household")
    store.clear_relationship("bob")
    assert "bob" not in store.get().relationships


def test_persist_to_disk_survives_reopen(tmp_path):
    db = tmp_path / "prefs.db"
    s1 = PreferencesStore(path=str(db))
    s1.update(vigilance="high", what_i_care_about="hello")
    s1.set_relationship("bob", "household")
    s1.close()
    s2 = PreferencesStore(path=str(db))
    prefs = s2.get()
    assert prefs.vigilance == "high"
    assert prefs.what_i_care_about == "hello"
    assert prefs.relationships == {"bob": "household"}
    s2.close()


def test_singleton_constraint_blocks_second_row(store):
    # The CHECK (id = 1) constraint means only id=1 ever exists.
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("INSERT INTO preferences (id, updated_at) VALUES (2, 0)")


# ─── intent page section rendering ─────────────────────────────


def test_preferences_section_renders_all_vigilance_radios():
    prefs = Preferences(vigilance="high")
    html = _preferences_section(prefs)
    for v in ("low", "normal", "high"):
        assert f"value='{v}'" in html
    # current selection is checked
    assert "value='high' checked" in html


def test_preferences_section_shows_text_in_textarea():
    prefs = Preferences(what_i_care_about="Winston is our dog")
    html = _preferences_section(prefs)
    assert "Winston is our dog" in html


def test_preferences_section_quiet_summary_pluralizes():
    one = Preferences(quiet_hours=[{"days": ["sat"], "start": "0", "end": "7"}])
    none = Preferences(quiet_hours=[])
    html_one = _preferences_section(one)
    html_none = _preferences_section(none)
    assert "1 quiet-hour window" in html_one
    assert "No quiet hours" in html_none


def test_preferences_section_relationship_summary_pluralizes():
    none = Preferences()
    two = Preferences(relationships={"a": "household", "b": "guest"})
    assert "No actor relationships" in _preferences_section(none)
    assert "2 actor relationships" in _preferences_section(two)


def test_preferences_section_when_store_missing_renders_notice():
    html = _preferences_section(None)
    assert "Preferences store not wired" in html


def test_preferences_form_posts_to_intent_preferences():
    html = _preferences_section(Preferences())
    assert "action='intent/preferences'" in html


def test_preferences_section_escapes_what_i_care_about():
    prefs = Preferences(what_i_care_about="<script>alert(1)</script>")
    html = _preferences_section(prefs)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ─── render_intent_page integration ────────────────────────────


def test_render_intent_page_includes_preferences_section_above_rules():
    html = render_intent_page(
        rules=[],
        now_ts=1_700_000_000.0,
        preferences=Preferences(vigilance="high"),
    )
    # Preferences card heading before Rules card heading
    pref_idx = html.index("Preferences")
    rules_idx = html.index("<h2>Rules</h2>")
    assert pref_idx < rules_idx
    # Vigilance radio is present + checked at 'high'
    assert "value='high' checked" in html


def test_render_intent_page_with_no_preferences_shows_placeholder_message():
    html = render_intent_page(
        rules=[],
        now_ts=1_700_000_000.0,
        preferences=None,
    )
    assert "Preferences store not wired" in html
