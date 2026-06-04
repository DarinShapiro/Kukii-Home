"""/areas — list page + create/edit form (Part V of the design doc).

The page is the operator's seam for *"how should the system treat this
zone"* — vigilance posture (AttentionMode), expected normal hours, role
posture (public/shared/private). Cameras pick areas; rules + the
reasoner read areas to shape their judgment.

Pure renderers fed by the route handler. The form is one shape for both
create and edit; the action URL determines which.
"""

from __future__ import annotations

from typing import Any

from kukiihome_ha_agent.area_store import Area
from kukiihome_ha_agent.web_ui.shell import _e

# ─── AttentionMode chip rendering ───────────────────────────────────


_MODE_CSS = {"normal": "muted", "attention": "ok", "unattended": "warn"}
_MODE_LABEL = {
    "normal": "normal",
    "attention": "attention ●",     # solid dot = continuous monitoring
    "unattended": "unattended",
}


def _mode_chip(mode: str) -> str:
    css = _MODE_CSS.get(mode, "muted")
    label = _MODE_LABEL.get(mode, mode)
    return f"<span class='chip cam-state {css}'>{_e(label)}</span>"


# ─── List page ──────────────────────────────────────────────────────


def render_areas_list(areas: list[Area]) -> str:
    if not areas:
        body = (
            "<div class='empty'>No areas defined yet. "
            "Areas are how you tell the system <i>where</i> things happen — "
            "Pool, Backyard, Front porch, … Each area carries a vigilance "
            "mode and expected hours.</div>"
        )
    else:
        ordered = sorted(areas, key=lambda a: a.name.lower())
        tiles = "".join(_area_tile(a) for a in ordered)
        body = f"<div class='cameras-grid'>{tiles}</div>"
    return (
        "<h1>Areas</h1>"
        "<div class='sub'>Conceptual zones (Pool, Driveway, Front porch, "
        "Backyard, …) that group cameras and carry "
        "<b>AttentionMode</b> + normal-hours + role posture. The reasoner "
        "uses these to shape its judgment per area.</div>"
        "<div style='margin:14px 0'><a class='btn primary' "
        "href='areas/new'>+ New area</a></div>"
        + body
    )


def _area_tile(area: Area) -> str:
    role_line = (
        f" · role: <b>{_e(area.role)}</b>" if area.role else ""
    )
    hours_line = (
        f"{len(area.normal_hours)} normal-hour window"
        f"{'s' if len(area.normal_hours) != 1 else ''}"
        if area.normal_hours else "any-time"
    )
    cam_count = len(area.cameras)
    return (
        f"<a class='camera-tile' href='areas/{_e(area.id)}/edit'>"
        f"<div class='cam-head'>"
        f"<b>{_e(area.name)}</b>"
        f"{_mode_chip(area.attention_mode)}"
        "</div>"
        f"<div class='cam-meta'>"
        f"{cam_count} camera{'s' if cam_count != 1 else ''}{role_line}"
        "</div>"
        f"<div class='cam-meta muted'>{hours_line}</div>"
        "</a>"
    )


# ─── Form (create + edit) ───────────────────────────────────────────


def render_area_form(
    area: Area | None,
    *,
    available_cameras: list[tuple[str, str]] | None = None,
) -> str:
    """One form for new + edit. ``available_cameras`` is ``(id, label)``
    pairs from the camera registry; checkboxes pre-checked when in
    ``area.cameras``."""
    is_new = area is None
    title = "New area" if is_new else f"Edit area · {area.name}"
    action = "areas" if is_new else f"areas/{area.id}"
    name = area.name if area else ""
    desc = area.description if area else ""
    mode = area.attention_mode if area else "normal"
    role = area.role if area else ""
    selected_cams = set(area.cameras) if area else set()

    mode_radios = "".join(
        f"<label class='radio'><input type='radio' name='attention_mode' "
        f"value='{m}'{' checked' if m == mode else ''}> "
        f"{label}</label>"
        for m, label in (
            ("normal", "Normal — VLM reasons on triage events"),
            ("attention", "Attention ● — continuous monitoring (pool/fall-risk)"),
            ("unattended", "Unattended — suppress reasoning, log only"),
        )
    )

    role_radios = "".join(
        f"<label class='radio'><input type='radio' name='role' "
        f"value='{r}'{' checked' if r == role else ''}> "
        f"{label}</label>"
        for r, label in (
            ("", "Unset"),
            ("public", "Public (faces street / outsiders)"),
            ("shared", "Shared (visible to household)"),
            ("private", "Private (bedroom / bathroom)"),
        )
    )

    cam_checks = "".join(
        f"<label class='check'><input type='checkbox' name='cameras' "
        f"value='{_e(cid)}'{' checked' if cid in selected_cams else ''}> "
        f"{_e(label)}</label>"
        for cid, label in (available_cameras or [])
    ) or "<div class='hint'>No cameras configured yet.</div>"

    return (
        f"<a class='back-link' href='areas'>← All areas</a>"
        f"<h1>{_e(title)}</h1>"
        f"<form class='rule-form' method='post' action='{action}'>"
        "<section class='card'><h3>Name</h3>"
        f"<input type='text' name='name' value='{_e(name)}' "
        "placeholder='Pool, Backyard, Front porch, …' required></section>"
        "<section class='card'><h3>AttentionMode</h3>"
        f"<div class='mode-radios' style='flex-direction:column;align-items:flex-start'>"
        f"{mode_radios}</div>"
        "</section>"
        "<section class='card'><h3>Role posture</h3>"
        f"<div class='mode-radios' style='flex-direction:column;align-items:flex-start'>"
        f"{role_radios}</div>"
        "<div class='hint'>Role narrows VLM persona prompts + tunes "
        "default capture/retention. Optional.</div>"
        "</section>"
        "<section class='card'><h3>Cameras in this area</h3>"
        f"<div class='check-list'>{cam_checks}</div>"
        "<div class='hint'>A camera can belong to more than one area.</div>"
        "</section>"
        "<section class='card'><h3>Description</h3>"
        f"<textarea name='description' rows='2' "
        "placeholder='Free-text — the VLM reads this as context.'>"
        f"{_e(desc)}</textarea></section>"
        "<div class='form-actions'>"
        f"<a class='btn' href='areas'>Cancel</a>"
        + (
            f"<form method='post' action='areas/{area.id}/delete' "
            f"style='display:inline' "
            f"onsubmit='return confirm(\"Retire this area?\")'>"
            f"<button class='btn danger' type='submit'>Delete</button></form>"
            if not is_new else ""
        )
        + "<button class='btn primary' type='submit'>"
        + ("Create" if is_new else "Save")
        + "</button>"
        + "</div>"
        + "</form>"
    )


# ─── Form parsing ───────────────────────────────────────────────────


def _multi(form: dict, key: str) -> list[str]:
    if hasattr(form, "getall"):
        return list(form.getall(key, []))
    v = form.get(key)
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def parse_area_form(form: dict[str, Any]) -> dict[str, Any]:
    """Form POST → kwargs for ``AreaStore.create()`` / ``update()``.

    ``cameras`` comes back as a list (HTML multi-value); empty role is
    normalized to None so the SQL column stores NULL.
    """
    name = (form.get("name") or "").strip()
    if not name:
        raise ValueError("name required")
    mode = (form.get("attention_mode") or "normal").strip()
    if mode not in ("normal", "attention", "unattended"):
        mode = "normal"
    role = (form.get("role") or "").strip() or None
    if role and role not in ("public", "shared", "private"):
        role = None
    return {
        "name": name,
        "attention_mode": mode,
        "role": role,
        "description": (form.get("description") or "").strip(),
        "cameras": [c for c in _multi(form, "cameras") if c],
    }
