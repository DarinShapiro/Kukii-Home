"""/cameras — list page + per-camera detail (Part II of the design doc).

The list page is a flat grid: one tile per camera, friendly name, state
chip, 24h event count, click-through to detail.

The detail page answers *"what is this camera, how does the system treat
it, is it healthy"*. It explicitly does NOT answer *"what happened on
this camera"* — that's the activity stream, filtered by camera.

Sections (Part II §11), top-to-bottom:
  - At a glance: snapshot link + connection state + 24h event count
  - Identity & role: friendly name, area, role, indoor/outdoor
  - Detection capability matrix: per-signal source-of-truth + delegates
  - Privacy posture: zones, capture flags, retention overrides (Phase 2.B)
  - Tuning: per-camera /tune thresholds (Phase 2.B)
  - Health: stream state + decode + FP rate + VLM quality issues
  - **Authorized actions**: per-camera whitelist editor (Task 10 UI)
  - Active policies: per-camera dismissals + transient intents
  - Activity link out: "N events today" → activity filtered to this camera

The renderer is pure: the route handler in __main__ feeds it the structured
``CameraDetailViewModel`` so the page stays testable without mocking the
camera registry, HA client, action store, or alert log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kukiihome_ha_agent.web_ui.shell import (
    _e,
    camera_display_name,
    friendly_time_html,
)

# ─── View models ────────────────────────────────────────────────────


@dataclass
class CameraSummary:
    """One row in the cameras list. Built from CameraStreamStatus +
    HACameraLoop in the route handler."""

    camera_id: str
    name: str  # friendly name (suffix-stripped)
    state: str  # 'running' / 'opening' / 'error' / ...
    last_error: str = ""
    events_24h: int = 0
    last_motion_ts: float | None = None
    area_id: str | None = None
    role: str | None = None


@dataclass
class CapabilityRow:
    """One row in the per-camera capability matrix (§12)."""

    signal: str  # 'motion' / 'person' / 'vehicle' / 'dog' / 'package' / ...
    source: str  # 'NATIVE' / 'AUGMENTED' / 'SUBSTITUTED' / 'DELEGATED' / 'MISSING'
    detail: str = ""  # human-readable provider (e.g. "Dahua SMD")
    critical_if_missing: bool = False
    needs_action: bool = False  # show ⚠ + drift-to-Needs-Attention hint


@dataclass
class PerceptionEntryView:
    target_kind: str
    target: str
    max_duration_s: int | None = None


@dataclass
class ProtectiveEntryView:
    action_class: str
    service: str
    target: str
    min_severity: str
    min_confidence: float


@dataclass
class CameraDetailViewModel:
    """Bundle of every section's data — the renderer reads this verbatim."""

    camera_id: str
    name: str
    state: str
    last_error: str = ""
    events_24h: int = 0
    last_motion_ts: float | None = None
    snapshot_url: str | None = None
    area_id: str | None = None
    role: str | None = None
    indoor_outdoor: str | None = None
    public_facing: bool | None = None
    capabilities: list[CapabilityRow] = field(default_factory=list)
    perception_whitelist: list[PerceptionEntryView] = field(default_factory=list)
    protective_whitelist: list[ProtectiveEntryView] = field(default_factory=list)
    health: dict[str, Any] = field(default_factory=dict)


# ─── State chip helpers ─────────────────────────────────────────────


_STATE_CLASS = {
    "running": "ok",
    "starting": "warn",
    "opening": "warn",
    "error": "bad",
    "stopped": "muted",
}


def _state_chip(state: str) -> str:
    css = _STATE_CLASS.get(state, "muted")
    label = state.replace("_", " ")
    return f"<span class='chip cam-state {css}'>{_e(label)}</span>"


def _source_chip(row: CapabilityRow) -> str:
    """Color-code each capability source state per §12."""
    base = row.source.lower()
    label = row.source
    css = {
        "native": "ok",
        "augmented": "ok",
        "substituted": "ok",
        "delegated": "ok",
        "missing": "bad" if row.critical_if_missing else "warn",
    }.get(base, "muted")
    return f"<span class='chip cap-src {css}'>{_e(label)}</span>"


# ─── List page ──────────────────────────────────────────────────────


def render_cameras_list(cameras: list[CameraSummary]) -> str:
    """Tile grid of all known cameras."""
    if not cameras:
        body = (
            "<div class='empty'>No cameras configured yet. "
            "Cameras are auto-discovered from Home Assistant — check that "
            "the integration is connected.</div>"
        )
    else:
        # Defensive: builder already sorts, but if a caller hands us an
        # unsorted list we still produce a stable display order.
        ordered = sorted(cameras, key=lambda c: (c.name or c.camera_id).lower())
        tiles = "".join(_camera_tile(c) for c in ordered)
        body = f"<div class='cameras-grid'>{tiles}</div>"
    return (
        "<h1>Cameras</h1>"
        "<div class='sub'>Per-camera identity, detection capabilities, "
        "health, authorized actions. Not an NVR — multi-camera grid + "
        "scrubbing are Agent DVR's job (see the design doc §15).</div>" + body
    )


def _camera_tile(cam: CameraSummary) -> str:
    name = camera_display_name(cam.name) or cam.camera_id
    last_seen = (
        friendly_time_html(cam.last_motion_ts)
        if cam.last_motion_ts
        else "<span class='muted'>no motion yet</span>"
    )
    err_html = f"<div class='err'>{_e(cam.last_error)}</div>" if cam.last_error else ""
    role_line = f" · {_e(cam.role)}" if cam.role else ""
    return (
        f"<a class='camera-tile' href='cameras/{_e(cam.camera_id)}'>"
        f"<div class='cam-head'>"
        f"<b>{_e(name)}</b>"
        f"{_state_chip(cam.state)}"
        f"</div>"
        f"<div class='cam-meta'>"
        f"<span>{cam.events_24h} event"
        f"{'s' if cam.events_24h != 1 else ''} (24h){role_line}</span>"
        f"</div>"
        f"<div class='cam-meta muted'>last motion: {last_seen}</div>"
        f"{err_html}"
        "</a>"
    )


# ─── Detail page ────────────────────────────────────────────────────


def _at_a_glance(vm: CameraDetailViewModel) -> str:
    snap = (
        f"<img class='cam-snap' src='{_e(vm.snapshot_url)}' "
        "alt='camera snapshot' onerror=\"this.style.display='none'\">"
        if vm.snapshot_url
        else ""
    )
    err_html = f"<div class='err'>{_e(vm.last_error)}</div>" if vm.last_error else ""
    return (
        "<section class='card'>"
        "<div class='card-head'>"
        f"<h2>{_e(vm.name)}</h2>"
        f"{_state_chip(vm.state)}"
        f"<span class='muted'>{vm.events_24h} event"
        f"{'s' if vm.events_24h != 1 else ''} (24h)</span>"
        "</div>"
        f"{snap}"
        f"{err_html}"
        "</section>"
    )


def _identity_role(vm: CameraDetailViewModel) -> str:
    bits: list[str] = []
    if vm.area_id:
        bits.append(f"Area: <b>{_e(vm.area_id)}</b>")
    if vm.role:
        bits.append(f"Role: <b>{_e(vm.role)}</b>")
    if vm.indoor_outdoor:
        bits.append(_e(vm.indoor_outdoor))
    if vm.public_facing is not None:
        bits.append("faces public: " + ("<b>yes</b>" if vm.public_facing else "no"))
    body = (
        " · ".join(bits)
        if bits
        else "<span class='muted'>Not configured yet. Iteration 2.C will "
        "let you assign area + role from this card.</span>"
    )
    return (
        "<section class='card'>"
        "<h2>Identity &amp; role</h2>"
        f"<div class='cam-row'>{body}</div>"
        "</section>"
    )


def _capability_matrix(vm: CameraDetailViewModel) -> str:
    if not vm.capabilities:
        rows = (
            "<tr><td colspan='3' class='empty'>"
            "Capability matrix populates as events arrive — the system "
            "infers source-of-truth per signal from observed classifications."
            "</td></tr>"
        )
    else:
        rows = "".join(
            "<tr>"
            f"<td><b>{_e(c.signal)}</b></td>"
            f"<td>{_source_chip(c)}</td>"
            f"<td>{_e(c.detail)}"
            f"{'  ⚠' if c.needs_action else ''}"
            "</td>"
            "</tr>"
            for c in vm.capabilities
        )
    return (
        "<section class='card'>"
        "<h2>Detection capability matrix</h2>"
        "<div class='sub'>Five states per signal (§12): NATIVE "
        "(camera produces it), AUGMENTED (camera triggers, we enrich), "
        "SUBSTITUTED (we run our own), DELEGATED (NVR runs it), "
        "MISSING (nobody does).</div>"
        f"<table class='matrix-table'>"
        "<thead><tr><th>Signal</th><th>Source</th><th>Provider</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</section>"
    )


def _whitelist_editor(vm: CameraDetailViewModel) -> str:
    """Per-camera Authorized actions block (Task 10 UI). Edit goes to a
    dedicated form page — this card is read + add-link + delete-button."""
    perc = (
        "".join(
            f"<tr>"
            f"<td><b>{_e(p.target_kind)}</b></td>"
            f"<td>{_e(p.target)}</td>"
            f"<td class='muted'>"
            f"{p.max_duration_s if p.max_duration_s is not None else '—'} s max"
            f"</td>"
            f"<td>"
            f"<form method='post' style='display:inline' "
            f"action='cameras/{_e(vm.camera_id)}/whitelist/perception/delete'>"
            f"<input type='hidden' name='target_kind' value='{_e(p.target_kind)}'>"
            f"<input type='hidden' name='target' value='{_e(p.target)}'>"
            f"<button class='btn danger' type='submit'>Remove</button>"
            f"</form>"
            f"</td>"
            f"</tr>"
            for p in vm.perception_whitelist
        )
        or "<tr><td colspan='4' class='empty'>No perception actions "
        "authorized. Add lights or PTZ ops the agent may briefly use "
        "during its own reasoning loop.</td></tr>"
    )
    prot = (
        "".join(
            f"<tr>"
            f"<td><b>{_e(p.action_class)}</b></td>"
            f"<td>{_e(p.service)}</td>"
            f"<td>{_e(p.target)}</td>"
            f"<td class='muted'>≥ {_e(p.min_severity)} · conf ≥ {p.min_confidence:.2f}</td>"
            f"<td>"
            f"<form method='post' style='display:inline' "
            f"action='cameras/{_e(vm.camera_id)}/whitelist/protective/delete'>"
            f"<input type='hidden' name='action_class' value='{_e(p.action_class)}'>"
            f"<input type='hidden' name='service' value='{_e(p.service)}'>"
            f"<input type='hidden' name='target' value='{_e(p.target)}'>"
            f"<button class='btn danger' type='submit'>Remove</button>"
            f"</form>"
            f"</td>"
            f"</tr>"
            for p in vm.protective_whitelist
        )
        or "<tr><td colspan='5' class='empty'>No protective actions "
        "authorized. Locks, sirens, floods etc. require explicit "
        "opt-in per camera per action — see §7.7.</td></tr>"
    )
    return (
        "<section class='card'>"
        "<h2>Authorized actions</h2>"
        "<div class='sub'>What the agent may do directly on this camera. "
        "Perception is transient and auto-reverts; Protective is "
        "persistent and gated by severity + confidence + blackouts.</div>"
        "<h3>Perception (transient — class 2)</h3>"
        "<table class='matrix-table'>"
        "<thead><tr><th>Kind</th><th>Target</th><th>Max duration</th><th></th></tr></thead>"
        f"<tbody>{perc}</tbody></table>"
        f"<a class='btn' href='cameras/{_e(vm.camera_id)}/whitelist/perception/new'>"
        "+ Add perception action</a>"
        "<h3 style='margin-top:18px'>Protective (persistent — class 3)</h3>"
        "<table class='matrix-table'>"
        "<thead><tr><th>Class</th><th>Service</th><th>Target</th>"
        "<th>Policy</th><th></th></tr></thead>"
        f"<tbody>{prot}</tbody></table>"
        f"<a class='btn' href='cameras/{_e(vm.camera_id)}/whitelist/protective/new'>"
        "+ Add protective action</a>"
        "</section>"
    )


def _health(vm: CameraDetailViewModel) -> str:
    h = vm.health
    rows = []
    rows.append(f"Stream state: <b>{_e(vm.state)}</b>")
    if "frames_read" in h:
        rows.append(f"Frames read: <b>{h['frames_read']}</b>")
    if "motion_events" in h:
        rows.append(f"Motion events: <b>{h['motion_events']}</b>")
    if h.get("last_error"):
        rows.append(f"<span class='err'>Last error: {_e(h['last_error'])}</span>")
    return (
        "<section class='card'>"
        "<h2>Health</h2>"
        "<div class='cam-row'>" + " · ".join(rows) + "</div>"
        "<div class='sub'>FP rate trend + VLM quality issues land here "
        "once the dev loop is wired in Iteration 2.E (Diagnostics).</div>"
        "</section>"
    )


def render_camera_detail(vm: CameraDetailViewModel) -> str:
    """Full per-camera page."""
    return (
        "<a class='back-link' href='cameras'>← All cameras</a>"
        + _at_a_glance(vm)
        + _identity_role(vm)
        + _capability_matrix(vm)
        + _whitelist_editor(vm)
        + _health(vm)
        + "<div class='trust-line'>"
        f"<a href='activity?cam={_e(vm.camera_id)}'>"
        f"See {vm.events_24h} event"
        f"{'s' if vm.events_24h != 1 else ''} (24h) for this camera "
        "in Activity →</a></div>"
    )


# ─── Whitelist forms ────────────────────────────────────────────────


def render_perception_form(
    camera_id: str, *, available_targets: list[tuple[str, str, str]] | None = None
) -> str:
    """Form for adding a perception whitelist entry. ``available_targets``
    is a list of ``(target_kind, target_value, display_label)`` to seed the
    dropdown — pulled from HA entities + the preprocessor's tune ops.

    When empty, the form still accepts free-text input — power users can
    paste an entity_id directly.
    """
    options = "".join(
        f"<option value='{_e(kind)}::{_e(value)}'>{_e(label)}</option>"
        for kind, value, label in (available_targets or [])
    )
    return (
        f"<a class='back-link' href='cameras/{_e(camera_id)}'>← Back to camera</a>"
        f"<h1>Add perception action</h1>"
        "<div class='sub'>Authorize a transient action the agent may take "
        "during its own reasoning loop (e.g. flick the porch light to see). "
        "Reverts automatically after the configured duration.</div>"
        f"<form class='rule-form' method='post' "
        f"action='cameras/{_e(camera_id)}/whitelist/perception'>"
        "<section class='card'><h3>Target kind</h3>"
        "<label class='radio'>"
        "<input type='radio' name='target_kind' value='ha_service' checked> "
        "HA service (e.g. light.turn_on, switch.turn_on)</label>"
        "<label class='radio'>"
        "<input type='radio' name='target_kind' value='camera_api'> "
        "Camera API op (e.g. ptz_zoom, ir_cut_off)</label>"
        "</section>"
        "<section class='card'><h3>Target</h3>"
        f"<input type='text' name='target' placeholder='"
        'ha_service: "light.turn_on:light.front_porch" — '
        'camera_api: "ptz_zoom"\' required>'
        + (
            f"<datalist>{options}</datalist><div class='hint'>"
            "Suggested entities pulled from HA + preprocessor tune ops.</div>"
            if options
            else ""
        )
        + "</section>"
        "<section class='card'><h3>Max duration (seconds)</h3>"
        "<input type='number' name='max_duration_s' min='1' max='600' value='45'>"
        "<div class='hint'>How long the action stays applied before "
        "auto-revert. Hard cap; the VLM may request shorter.</div>"
        "</section>"
        "<div class='form-actions'>"
        f"<a class='btn' href='cameras/{_e(camera_id)}'>Cancel</a>"
        "<button class='btn primary' type='submit'>Authorize</button>"
        "</div></form>"
    )


def render_protective_form(camera_id: str) -> str:
    """Form for adding a protective whitelist entry."""
    return (
        f"<a class='back-link' href='cameras/{_e(camera_id)}'>← Back to camera</a>"
        "<h1>Add protective action</h1>"
        "<div class='sub'>Authorize a persistent mitigation action — "
        "lock door, trigger siren, turn on floods. Gated by severity + "
        "confidence + (optionally) blackout windows.</div>"
        f"<form class='rule-form' method='post' "
        f"action='cameras/{_e(camera_id)}/whitelist/protective'>"
        "<section class='card'><h3>Action class</h3>"
        "<input type='text' name='action_class' "
        "placeholder='lock | siren | spotlight | announcement | ...' required>"
        "<div class='hint'>The category the VLM's recommendation will "
        "carry. The classifier doesn't need to be in any predefined list.</div>"
        "</section>"
        "<section class='card'><h3>HA service</h3>"
        "<input type='text' name='service' placeholder='lock.lock' required>"
        "</section>"
        "<section class='card'><h3>Target entity</h3>"
        "<input type='text' name='target' placeholder='lock.back_door' required>"
        "</section>"
        "<section class='card'><h3>Policy</h3>"
        "<label>Minimum severity</label>"
        "<div class='severity-radios'>"
        "<label class='radio'><input type='radio' name='min_severity' "
        "value='low'> Low</label>"
        "<label class='radio'><input type='radio' name='min_severity' "
        "value='normal'> Normal</label>"
        "<label class='radio'><input type='radio' name='min_severity' "
        "value='critical' checked> Critical</label>"
        "</div>"
        "<label style='margin-top:14px;display:block'>Minimum confidence</label>"
        "<input type='number' name='min_confidence' min='0' max='1' "
        "step='0.05' value='0.8'>"
        "<label style='margin-top:14px;display:block'>"
        "Redundancy (consecutive distinct incidents required, "
        "0 = single-call sufficient)</label>"
        "<input type='number' name='redundancy_required' min='0' max='5' value='0'>"
        "</section>"
        "<div class='form-actions'>"
        f"<a class='btn' href='cameras/{_e(camera_id)}'>Cancel</a>"
        "<button class='btn primary' type='submit'>Authorize</button>"
        "</div></form>"
    )


# ─── Form parsing ───────────────────────────────────────────────────


def parse_perception_form(form: dict) -> dict:
    """POST body → kwargs for ``PerceptionEntry``. Raises ValueError on
    missing required fields."""
    target_kind = (form.get("target_kind") or "ha_service").strip()
    if target_kind not in ("ha_service", "camera_api"):
        target_kind = "ha_service"
    target = (form.get("target") or "").strip()
    if not target:
        raise ValueError("target required")
    try:
        max_dur = int(form.get("max_duration_s") or 45)
    except (TypeError, ValueError):
        max_dur = 45
    max_dur = max(1, min(600, max_dur))
    return {
        "target_kind": target_kind,
        "target": target,
        "max_duration_s": max_dur,
    }


def parse_protective_form(form: dict) -> dict:
    """POST body → kwargs for ``ProtectiveEntry``."""
    action_class = (form.get("action_class") or "").strip()
    service = (form.get("service") or "").strip()
    target = (form.get("target") or "").strip()
    if not (action_class and service and target):
        raise ValueError("action_class, service, target all required")
    sev = (form.get("min_severity") or "critical").strip()
    if sev not in ("low", "normal", "critical"):
        sev = "critical"
    try:
        conf = float(form.get("min_confidence") or 0.8)
    except (TypeError, ValueError):
        conf = 0.8
    conf = max(0.0, min(1.0, conf))
    try:
        redundancy = int(form.get("redundancy_required") or 0)
    except (TypeError, ValueError):
        redundancy = 0
    redundancy = max(0, min(5, redundancy))
    return {
        "action_class": action_class,
        "service": service,
        "target": target,
        "min_severity": sev,
        "min_confidence": conf,
        "redundancy_required": redundancy,
    }
