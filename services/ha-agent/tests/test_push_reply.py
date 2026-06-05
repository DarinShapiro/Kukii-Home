"""Push-reply fragment-load (Part X §40) — drawer auto-opens on
/alert/{id}?drawer=1 with the alert pre-loaded as context."""

from __future__ import annotations

from kukiihome_ha_agent.provenance_store import ProvenanceStore
from kukiihome_ha_agent.web_ui.drawer import is_drawer_requested, render_drawer
from kukiihome_ha_agent.web_ui.shell import render_shell

NOW = 1_700_000_000.0


def _store():
    return ProvenanceStore(path=None)


# ─── is_drawer_requested — alert URL params ──────────────────────


def test_alert_url_with_drawer_query_opens():
    # Mimics /alert/{id}?drawer=1 — the canonical push-reply URL
    assert is_drawer_requested({"drawer": "1"})


def test_alert_url_with_extra_params_still_opens_drawer():
    # /alert/{id}?other=x&drawer=1 — extras don't suppress it
    assert is_drawer_requested({"other": "x", "drawer": "1"})


def test_alert_url_without_drawer_param_does_not_open():
    assert not is_drawer_requested({})


# ─── Drawer pre-loaded with alert context ────────────────────────


def test_drawer_renders_alert_context_strip_when_set():
    store = _store()
    sess = store.open_session(
        "alice",
        alert_context="evt_42",
        page_context="alert/evt_42",
        now_ts=NOW,
    )
    html = render_drawer(
        session=sess,
        turns=[],
        alert_context="evt_42",
        now_ts=NOW,
    )
    # Context strip surfaces the alert id so user knows what they're refining
    assert "evt_42" in html
    assert "drawer-context" in html
    assert "Refine the rule" in html or "alert" in html.lower()
    store.close()


def test_drawer_carries_alert_context_into_composer_hidden_field():
    store = _store()
    sess = store.open_session(
        "alice",
        alert_context="evt_42",
        now_ts=NOW,
    )
    html = render_drawer(
        session=sess,
        turns=[],
        alert_context="evt_42",
        now_ts=NOW,
    )
    # The composer's hidden alert_context input carries the value so the
    # POST /api/drawer/turn handler can route the reply against the alert
    assert "name='alert_context'" in html
    assert "value='evt_42'" in html
    store.close()


def test_drawer_without_alert_context_does_not_show_strip():
    store = _store()
    sess = store.open_session("alice", now_ts=NOW)
    html = render_drawer(session=sess, turns=[], now_ts=NOW)
    assert "drawer-context" not in html
    store.close()


# ─── Shell fragment-rewrite JS ────────────────────────────────────


def test_shell_emits_fragment_to_query_rewrite_script():
    html = render_shell("home", "<p>x</p>")
    # The JS detects #drawer and rewrites the URL to ?drawer=1 so the
    # server sees the open-request (fragments are client-side only).
    assert "location.hash==='#drawer'" in html
    assert "drawer=1" in html


def test_shell_drawer_rewrite_script_loads_before_body():
    html = render_shell("home", "<p>x</p>")
    # The rewrite needs to fire before the body renders to avoid a flash
    # of non-drawer content; script must appear before </head>.
    head_close = html.index("</head>")
    rewrite_pos = html.index("location.hash")
    assert rewrite_pos < head_close


def test_shell_with_open_drawer_html_renders_aside():
    drawer_html = "<aside class='drawer'>conversation panel</aside>"
    html = render_shell("home", "<p>x</p>", drawer_html=drawer_html)
    assert "drawer'>conversation panel" in html
    # Main content shifts to make room
    assert "main class='with-drawer'" in html


def test_shell_without_drawer_html_renders_no_aside():
    html = render_shell("home", "<p>x</p>")
    assert "<aside" not in html
    assert "main class=''" in html
