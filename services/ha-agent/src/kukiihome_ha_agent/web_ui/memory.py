"""/memory — unified guidance browse (Part IX §28).

Replaces `/intent` + `/policies` with one list. Every guidance entry —
Rules, Preferences, DismissalPolicies, TransientIntents,
SituationalContexts, area postures — renders with the same row schema:

    name · type-chip · scope · lifecycle · last-applied · provenance-icon

Two cuts the user can toggle between:

  - **by context** (default) — *"About Winston"*, *"About the Pool"*,
    *"About tonight"*, *"About my preferences"*. How humans think.
  - **by type** — grouped by storage class. Power-user / debugging cut.

Pure renderers fed structured view models. The route handler in
__main__ assembles the entry list by polling every store + the
provenance store.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from kukiihome_ha_agent.web_ui.shell import _e, friendly_time_html

# ─── View models ────────────────────────────────────────────────────


@dataclass
class GuidanceEntry:
    """A flattened row across every guidance store. The route handler
    builds one of these per row."""

    guidance_id: str
    name: str
    storage_class: str                  # 'rule' | 'preference' | 'dismissal_policy' | ...
    scope_summary: str                  # "Front yard · Winston"
    scope_fields: dict[str, Any] = field(default_factory=dict)
    lifecycle: str = "persistent"       # 'persistent' | 'temporal' | 'fire_once'
    expires_at: float | None = None
    last_applied_ts: float | None = None
    apply_count: int = 0
    provenance_origin: str = "pre_provenance"
    detail_url: str = ""                # link target for the row (existing per-type form)
    # Free-form context labels — populated by ``classify_to_contexts``.
    contexts: list[str] = field(default_factory=list)


# ─── Storage-class chip styling ────────────────────────────────────


_CLASS_CSS = {
    "rule": "ok",
    "preference": "ok",
    "dismissal_policy": "warn",
    "transient_intent": "warn",
    "situational_context": "warn",
    "area_posture": "muted",
    "access_profile": "muted",
}

_CLASS_LABEL = {
    "rule": "Rule",
    "preference": "Preference",
    "dismissal_policy": "Dismissal",
    "transient_intent": "Transient",
    "situational_context": "Situational",
    "area_posture": "Area posture",
    "access_profile": "Access profile",
}


def _class_chip(storage_class: str) -> str:
    css = _CLASS_CSS.get(storage_class, "muted")
    label = _CLASS_LABEL.get(storage_class, storage_class)
    return f"<span class='chip cap-src {css}'>{_e(label)}</span>"


def _origin_icon(origin: str) -> str:
    icon = {
        "conversation": "💬",
        "form": "✎",
        "system_proposed": "✨",
        "pre_provenance": "·",
    }.get(origin, "·")
    title = {
        "conversation": "Authored via drawer conversation",
        "form": "Authored via form",
        "system_proposed": "System-proposed + user-approved",
        "pre_provenance": "Pre-existing (before provenance tracking)",
    }.get(origin, origin)
    return f"<span class='origin-icon' title='{_e(title)}'>{icon}</span>"


# ─── Context classification ───────────────────────────────────────


def classify_to_contexts(
    entry: GuidanceEntry, *, known_actor_names: set[str] | None = None,
) -> list[str]:
    """Assign one entry to one or more *"About X"* groups. An entry can
    surface under multiple contexts — a rule about Winston in the Pool
    area shows up under both *"About Winston"* and *"About the Pool"*.

    ``known_actor_names`` is the set of currently-enrolled actor/pet
    names so we can resolve actor mentions in scope fields.
    """
    known_actor_names = known_actor_names or set()
    out: list[str] = []

    sc = entry.storage_class
    s = entry.scope_fields

    # Preferences → "About my preferences" — global, no other context.
    if sc == "preference":
        out.append("About my preferences")
        return out

    # Temporal → "About tonight" / "About this period" group.
    if entry.lifecycle in ("temporal", "fire_once"):
        out.append("Temporal watches")

    # Actor scope → "About <ActorName>"
    actor_id = (s.get("actor") or "").strip()
    actor_name = (s.get("actor_name") or actor_id).strip()
    if actor_name:
        # Prefer canonical display name when known; otherwise fall back to id.
        if actor_name.lower() in {n.lower() for n in known_actor_names}:
            display = next(
                (n for n in known_actor_names
                 if n.lower() == actor_name.lower()),
                actor_name,
            )
            out.append(f"About {display}")
        else:
            out.append(f"About {actor_name}")

    # Area scope → "About the <Area>"
    area_id = (s.get("area") or "").strip()
    if area_id:
        # We don't have a slug-to-name resolver here; the route handler
        # may pre-resolve into ``scope_fields['area_name']``.
        area_name = (s.get("area_name") or area_id).strip()
        out.append(f"About the {area_name}")

    # Camera scope (no area) → "About <Camera>"
    camera_id = (s.get("camera") or "").strip()
    if camera_id and not area_id:
        camera_name = (s.get("camera_name") or camera_id).strip()
        out.append(f"About {camera_name}")

    # If we got nothing, file under Other.
    if not out:
        out.append("Other")

    return out


def group_by_context(
    entries: list[GuidanceEntry], *, known_actor_names: set[str] | None = None,
) -> dict[str, list[GuidanceEntry]]:
    """Bucket entries by context label. Each entry may appear in
    multiple buckets — that's the point. Preserves entry order within
    each bucket."""
    out: dict[str, list[GuidanceEntry]] = {}
    for e in entries:
        contexts = e.contexts or classify_to_contexts(
            e, known_actor_names=known_actor_names,
        )
        e.contexts = contexts
        for ctx in contexts:
            out.setdefault(ctx, []).append(e)
    return out


def group_by_type(entries: list[GuidanceEntry]) -> dict[str, list[GuidanceEntry]]:
    """Storage-class bucket. Order preserved within each."""
    out: dict[str, list[GuidanceEntry]] = {}
    for e in entries:
        out.setdefault(_CLASS_LABEL.get(e.storage_class, e.storage_class), []).append(e)
    return out


# ─── Row rendering ────────────────────────────────────────────────


def _row(entry: GuidanceEntry, *, now_ts: float | None) -> str:
    last_html = (
        friendly_time_html(entry.last_applied_ts, now=now_ts)
        if entry.last_applied_ts and now_ts
        else "<span class='muted'>never applied</span>"
    )
    expires_html = (
        f" · expires {friendly_time_html(entry.expires_at, now=now_ts)}"
        if entry.expires_at and now_ts
        else ""
    )
    name_html = (
        f"<a href='{_e(entry.detail_url)}'><b>{_e(entry.name)}</b></a>"
        if entry.detail_url else f"<b>{_e(entry.name)}</b>"
    )
    scope_html = (
        f"<span class='muted'>{_e(entry.scope_summary)}</span>"
        if entry.scope_summary else ""
    )
    return (
        "<div class='rule-row'>"
        f"<div class='rule-head'>"
        f"{name_html} {_class_chip(entry.storage_class)} {_origin_icon(entry.provenance_origin)}"
        f"</div>"
        f"<div class='rule-meta muted'>"
        f"{scope_html}"
        f"{' · ' if scope_html else ''}"
        f"applied {entry.apply_count} time"
        f"{'s' if entry.apply_count != 1 else ''} · last: {last_html}{expires_html}"
        f"</div>"
        "</div>"
    )


def _group_section(title: str, entries: list[GuidanceEntry], *, now_ts: float | None) -> str:
    body = "".join(_row(e, now_ts=now_ts) for e in entries)
    return (
        "<section class='card'>"
        f"<h2>{_e(title)} <span class='muted'>"
        f"({len(entries)} item{'s' if len(entries) != 1 else ''})</span></h2>"
        f"{body}"
        "</section>"
    )


# ─── Page assembly ────────────────────────────────────────────────


def render_memory_page(
    entries: list[GuidanceEntry], *,
    cut: str = "by_context",
    known_actor_names: set[str] | None = None,
    drift_suggestions: list[Any] | None = None,
    now_ts: float | None = None,
) -> str:
    """Render the unified `/memory` page.

    ``cut`` is one of ``'by_context'`` (default) or ``'by_type'``. The
    page renders the same entries — different grouping.
    ``drift_suggestions`` (Part X §39 backstop #3) renders as a banner
    above the entries when non-empty.
    """
    now_ts = now_ts if now_ts is not None else time.time()

    toggle = (
        "<div class='memory-cut'>"
        f"<a class='{'active' if cut == 'by_context' else ''}' "
        "href='memory?cut=by_context'>by context</a>"
        f"<a class='{'active' if cut == 'by_type' else ''}' "
        "href='memory?cut=by_type'>by type</a>"
        "</div>"
    )

    drawer_trigger = (
        "<div class='memory-drawer-trigger'>"
        "<a class='btn primary' href='memory?drawer=1'>"
        "✨ Tell me what to watch for…</a>"
        "</div>"
    )

    if not entries:
        sections = (
            "<div class='empty'>No guidance yet. Tap the ✨ button above to "
            "tell the system what to watch for, or refine the rule that fired "
            "on any alert page.</div>"
        )
    elif cut == "by_type":
        groups = group_by_type(entries)
        sections = "".join(
            _group_section(label, items, now_ts=now_ts)
            for label, items in groups.items()
        )
    else:
        groups = group_by_context(entries, known_actor_names=known_actor_names)
        # Pin "About my preferences" to the bottom; "Temporal watches" near it
        # for stable + meaningful ordering of the household-level contexts.
        ordered_keys = sorted(
            groups.keys(),
            key=lambda k: (
                k == "About my preferences",     # last
                k == "Temporal watches",         # second-to-last
                k == "Other",                    # near the end
                k,                                # alphabetical within tiers
            ),
        )
        sections = "".join(
            _group_section(k, groups[k], now_ts=now_ts) for k in ordered_keys
        )

    drift_html = _render_drift_banner(drift_suggestions or [])

    return (
        "<h1>Memory</h1>"
        "<div class='sub'>Every rule, preference, policy, transient intent, "
        "and situational context the agent reads when reasoning. One list, "
        "two cuts — by what you're talking <i>about</i>, or by storage "
        "<i>type</i>.</div>"
        + drift_html
        + drawer_trigger
        + toggle
        + sections
    )


def _render_drift_banner(suggestions: list[Any]) -> str:
    """Drift suggestions banner (Part X §39 backstop #3). Empty string
    when nothing's drifting."""
    if not suggestions:
        return ""
    bullets = "".join(
        "<div class='drift-row'>"
        f"<b>{_e(s.name)}</b> "
        f"<span class='chip cap-src muted'>{_e(s.kind)}</span>"
        f"<div class='muted'>{_e(s.summary)}</div>"
        "</div>"
        for s in suggestions
    )
    return (
        "<section class='card drift-banner'>"
        "<h3>Suggestions "
        f"<span class='muted'>({len(suggestions)} item"
        f"{'s' if len(suggestions) != 1 else ''})</span></h3>"
        "<div class='sub'>The system noticed these entries aren't "
        "earning their placement. Each suggestion is just a hint — "
        "click an entry to confirm or dismiss.</div>"
        f"{bullets}"
        "</section>"
    )
