"""/intent page — Preferences placeholder + live Rules section.

This is the user-facing surface of Task 9. Preferences stays a coming-soon
section (lights up in Iteration 2.A); Rules is the live half — list + new +
edit + delete forms, both NL and shortcut modes.

The renderer accepts a list of :class:`Rule` so it stays pure: the route
handler in ``__main__`` loads from the store and passes them in. That keeps
this module trivially unit-testable and lets the v2 mock page evict itself
cleanly (the old ``render_intent_page`` from ``mocks.py`` is removed in the
same commit).
"""

from __future__ import annotations

import html as _html
import time
from typing import Any

from kukiihome_ha_agent.rules_store import Rule
from kukiihome_ha_agent.web_ui.shell import _e, friendly_time_html

# ─── Rules list ─────────────────────────────────────────────────────


def _severity_label(rule: Rule) -> str:
    """How the rule's severity reads in the list — *VLM-reasoned* for NL,
    explicit class for shortcut."""
    if rule.mode == "shortcut":
        return f"severity: {rule.severity_static or 'normal'} (static)"
    return "severity: VLM-reasoned"


def _scope_summary(rule: Rule) -> str:
    """Compact WHEN line: ``Front Yard · any time`` shape."""
    cam_part = (
        "any camera" if not rule.scope.cameras
        else ", ".join(rule.scope.cameras)
    )
    area_part = (
        "any area" if not rule.scope.areas
        else ", ".join(rule.scope.areas)
    )
    time_part = (
        "any time" if not rule.scope.time_windows
        else f"{len(rule.scope.time_windows)} window"
        + ("s" if len(rule.scope.time_windows) > 1 else "")
    )
    # If both camera & area are explicit, list both; usually one is enough.
    if rule.scope.cameras and rule.scope.areas:
        return f"{cam_part} · {area_part} · {time_part}"
    if rule.scope.cameras:
        return f"{cam_part} · {time_part}"
    if rule.scope.areas:
        return f"{area_part} · {time_part}"
    return f"any camera · {time_part}"


def _intent_body(rule: Rule) -> str:
    """Rendered ALERT IF block — quoted prose for NL, *"<subject> seen"* for
    shortcut. Both shapes share the same row so the list reads uniform."""
    if rule.mode == "shortcut":
        subj = rule.shortcut_subject or "(no subject)"
        return f"<i>{_e(subj)}</i> seen <span class='hint'>(identity shortcut)</span>"
    return (
        '"<span class="intent-text">'
        + _e(rule.intent_text or "(no intent text)")
        + '"</span>'
    )


def _last_matched_line(rule: Rule, *, now_ts: float) -> str:
    if rule.matched_count == 0:
        return "<div class='muted'>↳ never matched yet</div>"
    if rule.last_matched_at is None:
        return (
            f"<div class='muted'>↳ matched {rule.matched_count} time"
            f"{'s' if rule.matched_count != 1 else ''}</div>"
        )
    last_html = friendly_time_html(rule.last_matched_at, now=now_ts)
    return (
        "<div class='muted'>↳ matched "
        f"<b>{rule.matched_count}</b> time"
        f"{'s' if rule.matched_count != 1 else ''} · "
        f"last match {last_html}</div>"
    )


def _enabled_chip(rule: Rule) -> str:
    if not rule.enabled:
        return "<span class='chip disabled'>disabled</span>"
    return "<span class='chip enabled'>enabled ●</span>"


def _rule_row(rule: Rule, *, now_ts: float) -> str:
    """One row in the rules list — clickable header + WHEN + ALERT IF +
    match summary + the four action buttons. All actions go through the
    POST form to keep behavior on the server side (no JS dependency)."""
    enabled_str = "0" if rule.enabled else "1"  # what we'd post to flip
    return (
        f"<div class='rule-row' data-rule-id='{_e(rule.id)}'>"
        f"<div class='rule-head'>"
        f"<b>{_e(rule.name)}</b>"
        f"<span class='severity'>{_e(_severity_label(rule))}</span>"
        f"{_enabled_chip(rule)}"
        f"</div>"
        f"<div class='rule-scope'><b>WHEN</b> {_e(_scope_summary(rule))}</div>"
        f"<div class='rule-intent'><b>ALERT IF</b> {_intent_body(rule)}</div>"
        f"{_last_matched_line(rule, now_ts=now_ts)}"
        f"<div class='rule-actions'>"
        f"<a class='btn' href='intent/rules/{_e(rule.id)}/edit'>Edit</a>"
        f"<form method='post' action='intent/rules/{_e(rule.id)}/enable' "
        f"style='display:inline'>"
        f"<input type='hidden' name='enabled' value='{enabled_str}'>"
        f"<button class='btn' type='submit'>"
        f"{'Enable' if not rule.enabled else 'Disable'}"
        f"</button>"
        f"</form>"
        f"<a class='btn' href='intent/rules/{_e(rule.id)}/matches'>View matches</a>"
        f"<form method='post' action='intent/rules/{_e(rule.id)}/delete' "
        f"style='display:inline' "
        f"onsubmit='return confirm(\"Retire this rule?\")'>"
        f"<button class='btn danger' type='submit'>Delete</button>"
        f"</form>"
        f"</div>"
        f"</div>"
    )


def _preferences_section(prefs) -> str:
    """Live Preferences section (Iter 2.A). ``prefs`` is a Preferences
    dataclass or None — when None, renders a brief notice."""
    if prefs is None:
        return (
            "<section class='card'>"
            "<h2>Preferences</h2>"
            "<div class='sub'>Preferences store not wired in this build.</div>"
            "</section>"
        )
    vigilance = (prefs.vigilance or "normal").lower()
    text = prefs.what_i_care_about or ""
    quiet_count = len(prefs.quiet_hours or [])
    rel_count = len(prefs.relationships or {})
    vigilance_radios = "".join(
        f"<label class='radio'><input type='radio' name='vigilance' "
        f"value='{v}'{' checked' if v == vigilance else ''}> {label}</label>"
        for v, label in (
            ("low", "Low — dismiss generously"),
            ("normal", "Normal — balanced"),
            ("high", "High — err on the side of alerting"),
        )
    )
    quiet_summary = (
        f"{quiet_count} quiet-hour window"
        f"{'s' if quiet_count != 1 else ''} (life-safety areas ignore these)"
        if quiet_count else "No quiet hours — alerts fire any time."
    )
    rel_summary = (
        f"{rel_count} actor relationship"
        f"{'s' if rel_count != 1 else ''} set — Identities → Review to edit."
        if rel_count else "No actor relationships set yet — label "
        "identities under Identities → Review."
    )
    return (
        "<section class='card'>"
        "<h2>Preferences</h2>"
        "<div class='sub'>How the agent reads the rest of your home — "
        "vigilance baseline, what you care about, quiet hours, per-actor "
        "relationships.</div>"
        "<form method='post' action='intent/preferences'>"
        "<h3>Vigilance baseline</h3>"
        f"<div class='mode-radios' style='flex-direction:column;align-items:flex-start'>"
        f"{vigilance_radios}</div>"
        "<h3 style='margin-top:14px'>What I care about</h3>"
        "<textarea name='what_i_care_about' rows='4' "
        "placeholder=\"Free-text guidance the VLM reads on every event. "
        "E.g. 'Winston is our dog — don't alert on him in the backyard.'\">"
        + _e(text) +
        "</textarea>"
        "<div class='hint'>This text is folded into every VLM prompt as "
        "household baseline — concise, plain-language statements work best.</div>"
        "<div class='form-actions' style='justify-content:flex-start;margin-top:14px'>"
        "<button class='btn primary' type='submit'>Save preferences</button>"
        "</div>"
        "</form>"
        "<div class='trust-line' style='margin-top:16px'>"
        f"{_e(quiet_summary)}<br>{_e(rel_summary)}"
        "</div>"
        "</section>"
    )


def render_intent_page(
    rules: list[Rule], *, now_ts: float | None = None, preferences=None,
) -> str:
    """Full /intent page. ``rules`` should be the non-retired set (from
    ``store.all_rules()``); disabled rules still render so they're toggleable
    from the list. ``preferences`` is the live Preferences dataclass; pass
    None to render the storage-unavailable notice."""
    now_ts = now_ts if now_ts is not None else time.time()
    body = (
        "<h1>Intent</h1>"
        "<div class='sub'>How you've told the system what you care about. "
        "Preferences shape the reasoner's baseline; Rules attach named, "
        "scoped intents with explicit severities on top.</div>"
        + _preferences_section(preferences)
        + "<section class='card'>"
        + "<div class='card-head'>"
        + "<h2>Rules</h2>"
        + "<a class='btn primary' href='intent/rules/new'>+ New rule</a>"
        + "</div>"
    )
    if not rules:
        body += (
            "<div class='empty'>No rules yet. The first rule is the "
            "fastest way to teach the system one thing you care about — "
            "*\"alert me when Bob arrives\"* takes 10 seconds.</div>"
        )
    else:
        body += "".join(_rule_row(r, now_ts=now_ts) for r in rules)
    body += (
        "<div class='trust-line'>Every matched rule fires a "
        "<code>kukiihome_alert</code> event with reasoned severity; "
        "your HA automation routes by severity.</div>"
        "</section>"
    )
    return body


# ─── Form (new + edit) ──────────────────────────────────────────────


def _radio(name: str, value: str, label: str, *, checked: bool) -> str:
    chk = " checked" if checked else ""
    return (
        f"<label class='radio'><input type='radio' name='{name}' "
        f"value='{value}'{chk}> {_e(label)}</label>"
    )


def _checkbox(name: str, value: str, label: str, *, checked: bool) -> str:
    chk = " checked" if checked else ""
    return (
        f"<label class='check'><input type='checkbox' name='{name}' "
        f"value='{value}'{chk}> {_e(label)}</label>"
    )


def render_rule_form(
    rule: Rule | None,
    *,
    available_subjects: list[tuple[str, str]] | None = None,
    available_cameras: list[tuple[str, str]] | None = None,
    available_areas: list[tuple[str, str]] | None = None,
) -> str:
    """Render the new/edit form. ``rule=None`` = new rule with defaults.

    The available_* lists are ``(id, display_name)`` pairs the route
    handler pulls from its own caches; both fall back to empty so the form
    still renders in tests with no caches plumbed in."""
    is_new = rule is None
    title = "New rule" if is_new else f"Edit rule · {rule.name}"
    action = "intent/rules" if is_new else f"intent/rules/{rule.id}"
    rid = "" if is_new else rule.id

    mode = rule.mode if rule else "nl"
    name = rule.name if rule else ""
    intent_text = rule.intent_text if rule else ""
    shortcut_subject = rule.shortcut_subject if rule else ""
    severity_static = rule.severity_static if rule else "normal"
    sel_cameras = set(rule.scope.cameras) if rule else set()
    sel_areas = set(rule.scope.areas) if rule else set()

    subj_opts = "".join(
        f"<option value='{_e(sid)}' "
        f"{'selected' if sid == shortcut_subject else ''}>{_e(label)}</option>"
        for sid, label in (available_subjects or [])
    )

    cam_checks = "".join(
        _checkbox("cameras", cid, label, checked=(cid in sel_cameras))
        for cid, label in (available_cameras or [])
    ) or "<div class='hint'>No cameras configured yet — leave blank for any.</div>"

    area_checks = "".join(
        _checkbox("areas", aid, label, checked=(aid in sel_areas))
        for aid, label in (available_areas or [])
    ) or "<div class='hint'>No areas defined yet — leave blank for any.</div>"

    severity_radios = "".join(
        _radio("severity_static", v, v.capitalize(), checked=(v == severity_static))
        for v in ("low", "normal", "critical")
    )

    nl_active = "active" if mode == "nl" else ""
    sc_active = "active" if mode == "shortcut" else ""

    return (
        f"<h1>{_e(title)}</h1>"
        f"<form class='rule-form' method='post' action='{action}'>"
        + (f"<input type='hidden' name='rule_id' value='{_e(rid)}'>" if rid else "")
        + "<section class='card'><h3>Mode</h3>"
        + "<div class='mode-radios'>"
        + _radio("mode", "nl",
                 "Natural-language intent (VLM-evaluated)", checked=(mode == "nl"))
        + _radio("mode", "shortcut",
                 "Identity shortcut (subject seen → alert)",
                 checked=(mode == "shortcut"))
        + "</div></section>"
        + "<section class='card'><h3>Name</h3>"
        + f"<input type='text' name='name' value='{_e(name)}' "
        + "placeholder='e.g. Winston unsupervised in front' required>"
        + "</section>"
        + "<section class='card'><h3>WHEN — scope</h3>"
        + "<div class='sub'>Empty = applies anywhere. Pick specific "
        + "cameras / areas to gate the rule.</div>"
        + "<details><summary>Cameras</summary>"
        + f"<div class='check-list'>{cam_checks}</div></details>"
        + "<details><summary>Areas</summary>"
        + f"<div class='check-list'>{area_checks}</div></details>"
        + "<div class='hint'>Time windows: coming in iteration 2.</div>"
        + "</section>"
        # NL-mode subform (intent textarea + severity radio).
        + f"<section class='card mode-pane {nl_active}' data-mode='nl'>"
        + "<h3>ALERT IF</h3>"
        + "<textarea name='intent_text' rows='4' "
        + "placeholder=\"Winston seems to have gotten outside "
        + "without someone watching him.\">"
        + _e(intent_text)
        + "</textarea>"
        + "<div class='sub'>The VLM reads this and judges match + severity "
        + "per situation. For NL rules, severity is reasoned — not fixed.</div>"
        + "</section>"
        # Shortcut-mode subform (subject + static severity).
        + f"<section class='card mode-pane {sc_active}' data-mode='shortcut'>"
        + "<h3>Trigger</h3>"
        + "<div class='subject-row'>Subject "
        + "<select name='shortcut_subject'>"
        + "<option value=''>—</option>"
        + subj_opts
        + "</select> "
        + "is seen.</div>"
        + "<div class='hint'>Type a custom value below to match a kind "
        + "(e.g. <code>person</code>) instead of a specific actor.</div>"
        + "<input type='text' name='shortcut_subject_custom' "
        + "placeholder='or kind (person, dog, vehicle, …)'>"
        + "<h3>Severity</h3>"
        + f"<div class='severity-radios'>{severity_radios}</div>"
        + "<div class='hint'>Shortcut rules don't call the VLM — severity "
        + "is fixed.</div>"
        + "</section>"
        + "<div class='form-actions'>"
        + "<a class='btn' href='intent'>Cancel</a>"
        + "<button class='btn primary' type='submit'>"
        + ("Save & enable" if is_new else "Save")
        + "</button>"
        + "</div>"
        + "</form>"
    )


# ─── Form parsing (POST body → Rule patches) ────────────────────────


def _multi(form: dict, key: str) -> list[str]:
    if hasattr(form, "getall"):
        return list(form.getall(key, []))
    v = form.get(key)
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def parse_rule_form(form: dict[str, Any]) -> dict[str, Any]:
    """Turn an aiohttp form POST into the kwargs ``RulesStore.create()`` or
    ``RulesStore.update()`` expects. Validation lives here:

      - ``name`` required (else ``ValueError``)
      - ``mode`` in {nl, shortcut} (else default ``nl``)
      - In shortcut mode, prefer ``shortcut_subject_custom`` over the
        dropdown's selected actor — lets the user override the picker.
    """
    from kukiihome_ha_agent.rules_store import RuleScope

    name = (form.get("name") or "").strip()
    if not name:
        raise ValueError("name required")
    mode = (form.get("mode") or "nl").strip()
    if mode not in ("nl", "shortcut"):
        mode = "nl"

    cameras = [c for c in _multi(form, "cameras") if c]
    areas = [a for a in _multi(form, "areas") if a]
    scope = RuleScope(cameras=cameras, areas=areas, time_windows=[])

    out: dict[str, Any] = {
        "name": name,
        "mode": mode,
        "intent_text": (form.get("intent_text") or "").strip(),
        "scope": scope,
    }

    if mode == "shortcut":
        # Custom kind overrides the actor picker if filled in.
        subject_custom = (form.get("shortcut_subject_custom") or "").strip()
        subject_pick = (form.get("shortcut_subject") or "").strip()
        out["shortcut_subject"] = subject_custom or subject_pick or None
        sev = (form.get("severity_static") or "normal").strip()
        if sev not in ("low", "normal", "critical"):
            sev = "normal"
        out["severity_static"] = sev
    else:
        out["shortcut_subject"] = None
        out["severity_static"] = None

    return out


def parse_html_escape(s: str) -> str:
    """Exposed for tests — backward-compat with manual escape needs."""
    return _html.escape(s, quote=True)
