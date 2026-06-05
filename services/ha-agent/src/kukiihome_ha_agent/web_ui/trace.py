"""Trace audit chain (Part III §22 extension).

Reads the three audit stores — rules / actions / policies — for one
incident and renders the *audit chain* card inline on the alert detail
page. Surfaces, per the design doc §22:

  - **Matched rules**: which Task-9 rules fired on this incident, with
    severity + confidence + reasoning. Non-matches sampled as
    "evaluated, did not match" rows when the user explicitly asked.
  - **Protective actions**: every class-3 attempt with status (ok /
    gated / failed / whitelisted_rejected) and the gate_reason for
    rejected ones.
  - **Policy hits**: any dismissal policies or transient intents that
    applied to this incident — the reverse-link from passive activity
    rows (Part VII).

The card is the trust contract: the user can always see exactly what
the system reasoned through. Empty stores or no-hit incidents render
nothing — keeps the page lean for the common case.
"""

from __future__ import annotations

from typing import Any

from kukiihome_ha_agent.web_ui.shell import _e, friendly_time_html


def _severity_chip(sev: str | None) -> str:
    css = {"critical": "bad", "normal": "ok", "low": "warn"}.get((sev or "").lower(), "muted")
    label = sev or "—"
    return f"<span class='chip cam-state {css}'>{_e(label)}</span>"


def _status_chip(status: str) -> str:
    css = {
        "ok": "ok",
        "gated": "warn",
        "failed": "bad",
        "whitelisted_rejected": "bad",
        "dismissed": "muted",
        "boosted": "ok",
        "noop": "muted",
    }.get(status, "muted")
    return f"<span class='chip cam-state {css}'>{_e(status)}</span>"


def render_matched_rules_section(matches: list[Any]) -> str:
    """``matches`` is a list of RuleMatch (rules_store.RuleMatch) for the
    incident. Returns empty string when no matches."""
    if not matches:
        return ""
    rows = "".join(
        "<tr>"
        f"<td><b>{_e(m.rule_id)}</b></td>"
        f"<td>{_severity_chip(m.severity)}</td>"
        f"<td>{round(m.confidence or 0.0, 2) if m.confidence is not None else '—'}</td>"
        f"<td>{_e(m.reasoning or '')}</td>"
        f"<td>{_status_chip('matched' if m.matched else 'no-match')}</td>"
        "</tr>"
        for m in matches
    )
    return (
        "<section class='card'>"
        "<h2>Matched rules</h2>"
        "<div class='sub'>Task 9: rules that evaluated this incident. "
        "Each matched rule fired its own <code>kukiihome_alert</code> event "
        "with the reasoned severity.</div>"
        "<table class='matrix-table'>"
        "<thead><tr><th>Rule</th><th>Severity</th><th>Conf</th>"
        "<th>Reasoning</th><th>Status</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</section>"
    )


def render_protective_actions_section(
    log_rows: list[Any],
    *,
    now_ts: float | None,
) -> str:
    """``log_rows`` is a list of ProtectiveLogRow for the incident."""
    if not log_rows:
        return ""
    rows = "".join(
        "<tr>"
        f"<td>{friendly_time_html(r.ts, now=now_ts) if now_ts else _e(r.ts)}</td>"
        f"<td><b>{_e(r.action_class)}</b></td>"
        f"<td>{_e(r.service)}<br><span class='muted'>{_e(r.target)}</span></td>"
        f"<td>{_status_chip(r.status)}</td>"
        f"<td class='muted'>{_e(r.gate_reason or '')}</td>"
        "</tr>"
        for r in log_rows
    )
    return (
        "<section class='card'>"
        "<h2>Protective actions</h2>"
        "<div class='sub'>Task 10: class-3 actions the dispatcher evaluated. "
        "Executed (ok) / gated (policy blocked) / rejected (no whitelist) / "
        "failed (HA call errored).</div>"
        "<table class='matrix-table'>"
        "<thead><tr><th>When</th><th>Class</th><th>Service / target</th>"
        "<th>Status</th><th>Gate reason</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</section>"
    )


def render_policy_hits_section(
    hits: list[Any],
    policies_by_id: dict[str, Any],
    *,
    now_ts: float | None,
) -> str:
    """``hits`` is a list of PolicyHit; ``policies_by_id`` is a name lookup
    so the row can show the human-readable policy name + kind."""
    if not hits:
        return ""
    rows = "".join(
        "<tr>"
        f"<td>{friendly_time_html(h.applied_at, now=now_ts) if now_ts else _e(h.applied_at)}</td>"
        f"<td><b>{_e((policies_by_id.get(h.policy_id) and policies_by_id[h.policy_id].name) or h.policy_id)}</b><br>"
        f"<span class='muted'>{_e((policies_by_id.get(h.policy_id) and policies_by_id[h.policy_id].kind) or 'unknown')}</span></td>"
        f"<td>{_status_chip(h.outcome)}</td>"
        "</tr>"
        for h in hits
    )
    return (
        "<section class='card'>"
        "<h2>Policy hits</h2>"
        "<div class='sub'>Part VII: dismissal policies or transient "
        "intents that applied to this incident.</div>"
        "<table class='matrix-table'>"
        "<thead><tr><th>When</th><th>Policy</th><th>Outcome</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</section>"
    )


# ─── Top-level assembly ───────────────────────────────────────────


def build_audit_chain_html(
    *,
    incident_id: str,
    rules_store: Any | None = None,
    action_store: Any | None = None,
    policy_store: Any | None = None,
    now_ts: float | None = None,
) -> str:
    """Compose the full audit chain HTML for one incident. Empty string
    when no stores are wired or none have records for this incident — the
    alert detail page just skips the section."""
    parts: list[str] = []

    if rules_store is not None:
        try:
            matches = rules_store.matches_for_incident(incident_id)
        except Exception:
            matches = []
        parts.append(render_matched_rules_section(matches))

    if action_store is not None:
        try:
            log_rows = action_store.log_for_incident(incident_id)
        except Exception:
            log_rows = []
        parts.append(
            render_protective_actions_section(
                log_rows,
                now_ts=now_ts,
            )
        )

    if policy_store is not None:
        try:
            hits = policy_store.hits_for_incident(incident_id)
            policies_by_id = {}
            for h in hits:
                p = policy_store.get(h.policy_id)
                if p is not None:
                    policies_by_id[h.policy_id] = p
        except Exception:
            hits = []
            policies_by_id = {}
        parts.append(
            render_policy_hits_section(
                hits,
                policies_by_id,
                now_ts=now_ts,
            )
        )

    return "".join(parts)
