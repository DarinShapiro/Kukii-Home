"""Conversational drawer (Part X §34).

Persistent right-side panel that hosts the unifying authoring surface.
Opens via `?drawer=1` query param or `#drawer` URL fragment; closes
when both are absent. Server-side session attaches via cookie; the
drawer's transcript view re-renders on every page reload from the
ProvenanceStore (single source of truth).

Renderers here are pure HTML — fed turns from the ProvenanceStore +
the active session metadata. The drawer's JS layer (in shell.py) just
handles open/close + form submit; the server does all session +
transcript + placement state.
"""

from __future__ import annotations

from kukiihome_ha_agent.provenance_store import (
    PlacementProposal,
    Session,
    TranscriptTurn,
)
from kukiihome_ha_agent.web_ui.shell import _e, friendly_time_html

# ─── Type chip + preview card ──────────────────────────────────────


_STORAGE_LABEL = {
    "rule": "Rule",
    "preference": "Preference",
    "dismissal_policy": "Dismissal",
    "transient_intent": "Transient",
    "situational_context": "Situational",
    "area_posture": "Area posture",
    "access_profile": "Access profile",
}


def _render_proposal_card(
    proposal: PlacementProposal,
    *,
    turn_id: str,
    session_id: str,
) -> str:
    """The placement preview card the user confirms or refines."""
    storage_label = _STORAGE_LABEL.get(
        proposal.storage_class,
        proposal.storage_class,
    )
    chip = (
        f"<span class='chip cap-src ok'>{_e(storage_label)}</span> · "
        f"<span class='muted'>{_e(proposal.lifecycle)}</span> · "
        f"<span class='muted'>{_e(proposal.fire_affordance)}</span>"
    )
    scope_lines = "".join(
        f"<div><b>{_e(k)}:</b> {_e(str(v))}</div>" for k, v in (proposal.scope or {}).items() if v
    )
    severity_line = (
        f"<div><b>severity:</b> {_e(proposal.severity)}</div>" if proposal.severity else ""
    )

    if proposal.clarifying_questions:
        # Disambiguation state — render the questions, no confirm button
        q_html = "".join(
            f"<div class='clarify-q'>{_e(q)}</div>" for q in proposal.clarifying_questions
        )
        confirm_html = "<div class='hint'>Reply above to answer; I'll re-propose.</div>"
    else:
        q_html = ""
        confirm_html = (
            f"<form method='post' action='api/drawer/confirm' "
            "style='display:inline'>"
            f"<input type='hidden' name='turn_id' value='{_e(turn_id)}'>"
            f"<input type='hidden' name='session_id' value='{_e(session_id)}'>"
            "<button class='btn primary' type='submit'>Confirm</button>"
            "</form>"
            f"<a class='btn' href='memory?drawer=1'>Refine</a>"
        )

    return (
        "<div class='drawer-card proposal'>"
        f"<div class='card-head'><b>{_e(proposal.name)}</b></div>"
        f"<div class='drawer-meta'>{chip}</div>"
        f"{scope_lines}"
        f"{severity_line}"
        f"<div class='drawer-reasoning'>Because: "
        f"{_e(proposal.reasoning)}</div>"
        f"{q_html}"
        f"<div class='drawer-actions'>{confirm_html}</div>"
        "</div>"
    )


def _render_committed_card(turn: TranscriptTurn) -> str:
    """A confirmed turn — shows the guidance entry id + a link to its
    detail page."""
    return (
        "<div class='drawer-card committed'>"
        f"<div><b>✓ committed</b> as "
        f"<code>{_e(turn.committed_to)}</code></div>"
        "<div class='hint'>The entry is live in /memory.</div>"
        "</div>"
    )


def _render_user_turn(turn: TranscriptTurn, *, now_ts: float | None) -> str:
    ts_html = friendly_time_html(turn.ts, now=now_ts) if now_ts else ""
    return (
        "<div class='drawer-turn user'>"
        f"<div class='turn-meta'>You · {ts_html}</div>"
        f"<div class='turn-body'>{_e(turn.utterance)}</div>"
        "</div>"
    )


def _render_system_turn(
    turn: TranscriptTurn,
    *,
    session_id: str,
    now_ts: float | None,
) -> str:
    ts_html = friendly_time_html(turn.ts, now=now_ts) if now_ts else ""
    body: str
    if turn.committed_to:
        body = _render_committed_card(turn)
    elif turn.proposal_json:
        try:
            proposal = PlacementProposal.from_json(turn.proposal_json)
            body = _render_proposal_card(
                proposal,
                turn_id=turn.id,
                session_id=session_id,
            )
        except Exception:
            body = (
                f"<div class='drawer-card'><div class='hint'>"
                f"(malformed proposal: {_e(turn.proposal_json[:60])}…)"
                "</div></div>"
            )
    else:
        body = f"<div class='turn-body'>{_e(turn.utterance)}</div>"
    return (
        "<div class='drawer-turn system'>"
        f"<div class='turn-meta'>Kukii · {ts_html}</div>"
        f"{body}"
        "</div>"
    )


# ─── Top-level drawer renderer ─────────────────────────────────────


def render_drawer(
    *,
    session: Session | None,
    turns: list[TranscriptTurn],
    alert_context: str = "",
    request_path: str = "",
    now_ts: float | None = None,
) -> str:
    """Render the drawer panel. The host page (any page) embeds this on
    every request when the drawer is open; closed pages skip rendering
    entirely.

    ``request_path`` is the current page's URL path; the drawer's
    close link returns to that path WITHOUT the ?drawer=1 query so
    the page stays put with the drawer hidden. Earlier iteration used
    a query-only href ``?`` which RFC 3986 §5.3 resolves against
    ``<base href>`` — on depth-2 pages it sent you to the add-on
    landing page instead of closing the drawer.
    """
    # Compute close target: current page with leading slash stripped
    # so it's relative to <base href>'s app-root resolution. Falls back
    # to 'memory' when no request_path provided (legacy / tests).
    close_href = (request_path or "/memory").lstrip("/") or "memory"
    header = (
        "<div class='drawer-head'>"
        "<h3>✨ Conversation</h3>"
        f"<a class='drawer-close' href='{_e(close_href)}'>close</a>"
        "</div>"
    )

    context_strip = ""
    if alert_context:
        context_strip = (
            "<div class='drawer-context'>"
            f"Pre-loaded with alert <code>{_e(alert_context)}</code>. "
            "Refine the rule that fired or add a dismissal."
            "</div>"
        )

    if session is None:
        thread_html = (
            "<div class='drawer-empty'>"
            "Tell me what to watch for. I'll propose where to file it — "
            "always-on rule, tonight-only watch, or just a preference — "
            "and you confirm."
            "</div>"
        )
    elif not turns:
        thread_html = "<div class='drawer-empty'>Fresh session. What's on your mind?</div>"
    else:
        thread_html = "".join(
            _render_user_turn(t, now_ts=now_ts)
            if t.role == "user"
            else _render_system_turn(t, session_id=session.id, now_ts=now_ts)
            for t in turns
        )

    composer = (
        "<form class='drawer-composer' method='post' "
        "action='api/drawer/turn'>"
        f"<input type='hidden' name='session_id' "
        f"value='{_e(session.id) if session else ''}'>"
        f"<input type='hidden' name='alert_context' "
        f"value='{_e(alert_context)}'>"
        "<textarea name='utterance' rows='2' "
        "placeholder=\"e.g. 'Tell me when Winston is out front alone'\" "
        "required></textarea>"
        "<div class='form-actions'>"
        "<button class='btn primary' type='submit'>Send</button>"
        "</div>"
        "</form>"
    )

    return (
        "<aside class='drawer' role='complementary'>"
        + header
        + context_strip
        + "<div class='drawer-thread'>"
        + thread_html
        + "</div>"
        + composer
        + "</aside>"
    )


def is_drawer_requested(query: dict) -> bool:
    """Returns True when the URL query/fragment indicates the drawer
    should render. ``?drawer=1`` is the canonical opener; the URL
    fragment ``#drawer`` is read client-side (no server signal), but
    the redirect-on-fragment-tap-of-push-notification flow appends
    ``?drawer=1`` so the server sees it."""
    val = query.get("drawer") if isinstance(query, dict) else None
    if val is None and hasattr(query, "get"):
        val = query.get("drawer")
    return str(val or "").strip() in ("1", "true", "yes", "open")
