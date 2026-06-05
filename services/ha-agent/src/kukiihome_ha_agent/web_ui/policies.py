"""/policies — list dismissal policies + transient intents (Part VII).

Each row shows: name, rationale (why this exists), apply-count, last
applied, and a Revoke button. Two sections — Dismissals on top
(usually larger), Transient Intents below.

The reverse-link from passive activity rows happens upstream (in
home.py / activity.py) using ``PolicyStore.hits_for_incident``; this
page renders the policy entries themselves.
"""

from __future__ import annotations

from kukiihome_ha_agent.policy_store import Policy
from kukiihome_ha_agent.web_ui.shell import _e, friendly_time_html


def _policy_row(p: Policy, *, now_ts: float) -> str:
    applied = (
        friendly_time_html(p.last_applied_at, now=now_ts)
        if p.last_applied_at
        else "<span class='muted'>never applied</span>"
    )
    expires = f" · expires {friendly_time_html(p.expires_at, now=now_ts)}" if p.expires_at else ""
    rationale = f"<div class='muted'>{_e(p.rationale)}</div>" if p.rationale else ""
    return (
        f"<div class='rule-row'>"
        f"<div class='rule-head'><b>{_e(p.name)}</b>"
        f"<span class='muted'>applied {p.apply_count} time"
        f"{'s' if p.apply_count != 1 else ''} · last: {applied}{expires}</span>"
        "</div>"
        f"{rationale}"
        f"<div class='rule-actions'>"
        f"<form method='post' action='policies/{_e(p.id)}/revoke' "
        f"style='display:inline' "
        f"onsubmit='return confirm(\"Revoke this policy?\")'>"
        f"<button class='btn danger' type='submit'>Revoke</button>"
        f"</form>"
        f"</div>"
        f"</div>"
    )


def _section(
    title: str,
    blurb: str,
    policies: list[Policy],
    *,
    now_ts: float,
    empty_copy: str,
) -> str:
    if not policies:
        body = f"<div class='empty'>{empty_copy}</div>"
    else:
        body = "".join(_policy_row(p, now_ts=now_ts) for p in policies)
    return (
        "<section class='card'>"
        f"<h2>{_e(title)}</h2>"
        f"<div class='sub'>{blurb}</div>" + body + "</section>"
    )


def render_policies_page(
    *,
    dismissals: list[Policy],
    transient_intents: list[Policy],
    now_ts: float | None = None,
) -> str:
    import time as _time

    now_ts = now_ts if now_ts is not None else _time.time()
    return (
        "<h1>Policies</h1>"
        "<div class='sub'>The throttles and overrides the agent has built "
        "up over time. Every policy is viewable, revocable, and shows the "
        "incidents it has acted on.</div>"
        + _section(
            "Dismissals",
            "Patterns the system has learned to ignore — typically created "
            "by ✗ feedback on a passive event.",
            dismissals,
            now_ts=now_ts,
            empty_copy="No dismissals yet. Tap ✗ on an event that "
            "shouldn't have surfaced — the system will learn the pattern.",
        )
        + _section(
            "Transient intents",
            "Conversational watches you've set: <i>“notify me when Bob "
            "arrives”</i>. Self-prune on TTL or fire_once.",
            transient_intents,
            now_ts=now_ts,
            empty_copy="No transient intents active. These show up as the "
            "agent's heightened-attention list — added via the assistant.",
        )
    )
