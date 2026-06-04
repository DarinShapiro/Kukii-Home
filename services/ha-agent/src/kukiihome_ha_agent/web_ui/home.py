"""Home page — the front door of the add-on (Part III, ratified).

Three zones top-to-bottom (per spec §18):

1. **Top-line state** in plain English.
2. **Needs Attention** — drift, identity inbox, capability gaps, pending
   alerts. Empty state IS the win-state.
3. **Activity stream** (Part III §19-21) — N most recent reasoned incidents,
   action rows foregrounded, passive rows muted. Includes the trust-contract
   summary line so a quiet day still proves the system is reasoning.
4. **System stripe** at the bottom — collapsed by default, expandable.

Real data sources today (the page degrades gracefully when something's
missing):
- ``alert_log.recent()`` → activity rows (passive vs action derived from
  ``triage_status``).
- ``boot.preprocessor_client.list_identity_tracks(status='unresolved')`` →
  identity inbox row.
- ``boot.preprocessor_client.healthz()`` → preprocessor reachability for the
  system stripe.

Mocked / placeholder until the backend lands (clearly labelled with ``data: stub``
attrs for the test harness):
- Drift rows (Part II §16 — drift detection plumbing not built yet).
- Capability-gap rows (Part II §11 — capability matrix not built yet).
- Computational-dependency stripe per camera (Part II §16).
"""

from __future__ import annotations

from datetime import UTC, datetime

from kukiihome_ha_agent.web_ui.shell import _e, camera_display_name, friendly_time_html


def _seconds_today(now_ts: float) -> float:
    """Unix-ts of the start of today (local), used for 'today' counts."""
    now_dt = datetime.fromtimestamp(now_ts, UTC).astimezone()
    start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


def _alert_when_ts(alert: dict) -> float:
    """Best-effort timestamp for an alert (recorded_at ISO-8601 or 0)."""
    raw = alert.get("recorded_at") or alert.get("trigger_ts")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _alert_is_action(alert: dict) -> bool:
    """Action lane: the system reached out (alerted/asked) or executed.
    Passive lane: dismissed/no-op. Maps the existing AlertLog vocabulary
    (``triage_status``, ``acknowledged``) onto Part III §20-21."""
    status = (alert.get("triage_status") or "").lower()
    if status == "dismissed":
        return False
    # alerted / unset → treated as action (the system did reach out)
    return True


def _alert_headline(alert: dict) -> str:
    """Verb-phrased headline. Once the VLM is live this is its
    ``findings.scene_description`` verbatim (Part III §20). Until then we
    compose a clean fallback from the alert's existing fields, normalizing
    the camera name so we don't read like a config file.

    Examples:
        ``Person detected at Front South Camera``
        ``Dog detected at Backyard Camera``
        ``Motion at Front South Camera``  (when no kind classification)
        ``Bob arrived``  (when an actor name is resolved)
    """
    # 1. The VLM verb-phrasing is the canonical answer once it flows.
    h = alert.get("headline")
    if h:
        return str(h)

    cam_name = camera_display_name(
        alert.get("camera_friendly_name") or alert.get("camera_name")
    )
    kind = (alert.get("kind") or "").strip().lower()
    actor_name = alert.get("actor_name") or alert.get("actor_friendly_name")
    rule_name = alert.get("rule_name") or alert.get("rule")

    # 2. Identified actor + camera ("Bob arrived at Front South Camera")
    if actor_name:
        if cam_name:
            return f"{actor_name} at {cam_name}"
        return f"{actor_name} seen"

    # 3. Detected kind + camera ("Person detected at Front South Camera")
    if kind:
        kind_phrase = kind.capitalize()
        if cam_name:
            return f"{kind_phrase} detected at {cam_name}"
        return f"{kind_phrase} detected"

    # 4. Rule name (when explicit, no other context) — e.g. "Mail policy"
    if rule_name and cam_name:
        return f"{rule_name} at {cam_name}"
    if rule_name:
        return str(rule_name)

    # 5. Bare motion fallback
    if cam_name:
        return f"Motion at {cam_name}"
    return "Motion"


def _outcome_chip(alert: dict) -> str:
    """One-line outcome (Part III §20). Reads triage status + acknowledgement
    to render the chip."""
    status = (alert.get("triage_status") or "").lower()
    if status == "dismissed":
        why = alert.get("triage_explanation") or "auto-dismissed"
        return f"<span class='chip-out'>{_e(why)}</span>"
    if alert.get("acknowledged"):
        fb = alert.get("feedback") or "acknowledged"
        return f"<span class='chip-out action'>{_e(fb)}</span>"
    return "<span class='chip-out action'>alerted</span>"


def _render_activity_row(alert: dict, *, now_ts: float) -> str:
    when_html = friendly_time_html(_alert_when_ts(alert), now=now_ts)
    headline = _alert_headline(alert)
    cam_slug = alert.get("camera_id", "")
    is_action = _alert_is_action(alert)
    eid = alert.get("event_id") or alert.get("alert_id") or ""
    klass = "activity-row" if is_action else "activity-row passive"
    trace_link = (
        f"<a class='trace' href='alert/{_e(eid)}'>trace</a>" if eid else ""
    )
    # Only show the slug as a separate where-line if the headline doesn't
    # already mention the camera (the friendly name normalizer in Task 3
    # usually means it does). Drops "Person detected at Front South · front_south"
    # to just "Person detected at Front South Camera."
    cam_name = camera_display_name(
        alert.get("camera_friendly_name") or alert.get("camera_name")
    )
    headline_lower = headline.lower()
    camera_already_in_headline = bool(
        cam_name and (cam_name.lower() in headline_lower)
    ) or (cam_slug and cam_slug.lower() in headline_lower)
    where = (
        f"<span class='where'> · {_e(cam_slug)}</span>"
        if cam_slug and not camera_already_in_headline
        else ""
    )
    # when_html is already HTML-safe (escaped + wrapped in <span title=...>)
    return (
        f"<div class='{klass}'>"
        f"<div class='when'>{when_html}</div>"
        f"<div class='what'>{_e(headline)}{where}</div>"
        f"{_outcome_chip(alert)}"
        f"{trace_link}"
        "</div>"
    )


# ─── Needs Attention rows ──────────────────────────────────────────


def _attention_row(glyph: str, body: str, actions: list[tuple[str, str]],
                   *, meta: str | None = None) -> str:
    """One row in the Needs Attention zone. ``actions`` is a list of
    (label, href) buttons."""
    btns = "".join(
        f"<a href='{_e(href)}'>{_e(label)}</a>" for label, href in actions
    )
    meta_html = f"<div class='meta'>{_e(meta)}</div>" if meta else ""
    return (
        "<div class='attention-row'>"
        f"<div class='glyph'>{glyph}</div>"
        f"<div class='body'>{body}{meta_html}</div>"
        f"<div class='actions'>{btns}</div>"
        "</div>"
    )


def _render_attention(unresolved_tracks: int) -> tuple[str, int]:
    """Render the Needs Attention zone + return (html, count). Today only the
    identity inbox row is real; drift + capability rows arrive when the
    backend lands."""
    rows: list[str] = []

    if unresolved_tracks > 0:
        rows.append(_attention_row(
            "👤",
            f"<b>{unresolved_tracks}</b> unnamed track"
            f"{'s' if unresolved_tracks != 1 else ''} to review",
            [("Review", "review")],
            meta="People + pets the cameras captured but couldn't name yet.",
        ))

    # Stub rows — clearly marked, so the test harness can verify they DON'T
    # show up by default (and so we don't ship dead UI). Re-enable per row
    # once each backend lands (Part II §16).

    if not rows:
        return ("<div class='empty'>Nothing needs you. "
                "The system handled everything.</div>"), 0
    return "".join(rows), len(rows)


# ─── System stripe ─────────────────────────────────────────────────


def _render_system_stripe(*, cameras_total: int, cameras_active: int,
                          preprocessor_ok: bool | None, ha_connected: bool,
                          ha_entities: int) -> str:
    """Bottom zone — collapsed by default, summary line always visible."""
    cam_line = (
        f"● {cameras_active}/{cameras_total} cameras online"
        if cameras_total else "● No cameras configured"
    )
    if preprocessor_ok is None:
        prep_line = "● Preprocessor not configured"
    elif preprocessor_ok:
        prep_line = "● Preprocessor reachable"
    else:
        prep_line = "● Preprocessor unreachable"
    ha_line = (
        f"● HA connected · {ha_entities} entities watched"
        if ha_connected else "● HA disconnected"
    )
    summary = " · ".join([cam_line, prep_line, ha_line])
    return (
        "<details class='system-stripe'>"
        f"<summary>{_e(summary)}</summary>"
        "<div class='lines'>"
        f"<div>{_e(cam_line)}</div>"
        f"<div>{_e(prep_line)}</div>"
        f"<div>{_e(ha_line)}</div>"
        "<div class='muted'>Computational-dependency stripe + drift "
        "detection arrive with Part II §16.</div>"
        "</div>"
        "</details>"
    )


# ─── Page assembly ─────────────────────────────────────────────────


def render_home_page(
    *,
    alerts_recent: list[dict],
    unresolved_tracks: int,
    cameras_total: int,
    cameras_active: int,
    preprocessor_ok: bool | None,
    ha_connected: bool,
    ha_entities: int,
    now_ts: float,
    show_recent: int = 6,
) -> str:
    """Assemble the home page content (without the shell). The shell wraps
    this via :func:`shell.render_shell`."""
    today_start = _seconds_today(now_ts)
    today_alerts = [a for a in alerts_recent if _alert_when_ts(a) >= today_start]
    today_total = len(today_alerts)
    today_action = sum(1 for a in today_alerts if _alert_is_action(a))
    today_passive = today_total - today_action
    today_unhandled = sum(
        1 for a in today_alerts
        if _alert_is_action(a) and not a.get("acknowledged")
    )

    # ── Top-line state (plain English, not a dot) ──
    if today_total == 0:
        status_line = (
            "<div class='status-line'>🟢 All quiet — nothing yet today.</div>"
        )
    elif today_unhandled == 0:
        status_line = (
            f"<div class='status-line'>🟢 All quiet · {today_total} "
            f"event{'s' if today_total != 1 else ''} today · "
            f"0 unhandled.</div>"
        )
    else:
        status_line = (
            f"<div class='status-line'>⚠ {today_unhandled} unhandled · "
            f"{today_total} events today.</div>"
        )

    # ── Needs Attention ──
    attention_html, attention_count = _render_attention(unresolved_tracks)
    attention_heading = (
        f"<h2>Needs attention ({attention_count})</h2>"
        if attention_count else "<h2>Needs attention</h2>"
    )

    # ── Activity (N most recent) ──
    recent = sorted(alerts_recent, key=_alert_when_ts, reverse=True)[:show_recent]
    if not recent:
        activity_html = (
            "<div class='empty'>Nothing yet — the system is watching.</div>"
        )
    else:
        activity_html = "".join(_render_activity_row(a, now_ts=now_ts) for a in recent)
        if len(alerts_recent) > show_recent:
            activity_html += (
                "<div class='trust-line'>"
                "<a href='activity'>↓ See all activity</a></div>"
            )

    trust_line = ""
    if today_total > 0:
        trust_line = (
            f"<div class='trust-line'>Today: {today_action} "
            f"action{'s' if today_action != 1 else ''} · "
            f"{today_passive} passive — system is reasoning.</div>"
        )

    # ── System stripe ──
    system_html = _render_system_stripe(
        cameras_total=cameras_total, cameras_active=cameras_active,
        preprocessor_ok=preprocessor_ok, ha_connected=ha_connected,
        ha_entities=ha_entities,
    )

    return (
        status_line
        + attention_heading
        + attention_html
        + "<h2>Activity</h2>"
        + activity_html
        + trust_line
        + system_html
    )
