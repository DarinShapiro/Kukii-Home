"""/system — storage usage + retention + privacy operations (Part IX §30).

Three cards top-to-bottom:

  - Storage usage: per-class disk roll-up (events / frames / embeddings
    / audit logs / stores combined).
  - Retention: per-class policy editor backed by RetentionStore.
  - Operations: Erase last hour (panic button), Purge from camera
    between dates, Export (deferred to a follow-up).

Plus the admin audit log at the bottom — read-only list of recent
operations from RetentionStore.

Pure renderer — fed structured view models from system_data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kukiihome_ha_agent.web_ui.shell import _e, friendly_time_html

# ─── View models ────────────────────────────────────────────────────


@dataclass
class StorageClassRow:
    """One storage class's roll-up — events, frames, embeddings, etc."""

    label: str
    count: int = 0
    bytes_used: int = 0
    detail: str = ""                 # one-line breakdown (by camera, etc.)


@dataclass
class SystemViewModel:
    storage_rows: list[StorageClassRow] = field(default_factory=list)
    total_bytes: int = 0
    policy: Any = None               # RetentionPolicy (avoid circular import)
    audit_log: list[Any] = field(default_factory=list)  # AdminAudit rows
    cameras: list[tuple[str, str]] = field(default_factory=list)  # (id, name)
    now_ts: float | None = None


# ─── Helpers ───────────────────────────────────────────────────────


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


# ─── Storage card ──────────────────────────────────────────────────


def _storage_section(vm: SystemViewModel) -> str:
    rows = "".join(
        "<tr>"
        f"<td><b>{_e(r.label)}</b>"
        + (f"<br><span class='muted'>{_e(r.detail)}</span>"
           if r.detail else "")
        + "</td>"
        f"<td>{r.count}</td>"
        f"<td>{_format_bytes(r.bytes_used)}</td>"
        "</tr>"
        for r in vm.storage_rows
    )
    if not vm.storage_rows:
        rows = "<tr><td colspan='3' class='empty'>No usage data yet.</td></tr>"
    return (
        "<section class='card'>"
        "<h2>Storage usage</h2>"
        "<div class='sub'>How much disk each data class is using under "
        "<code>/data/kukiihome/</code>. Identity embeddings never auto-prune "
        "— they're the gallery.</div>"
        "<table class='matrix-table'>"
        "<thead><tr><th>Class</th><th>Count</th><th>Bytes</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f"<div class='trust-line' style='margin-top:12px'>"
        f"<b>Total:</b> {_format_bytes(vm.total_bytes)}"
        "</div></section>"
    )


# ─── Retention card ────────────────────────────────────────────────


def _retention_section(policy: Any) -> str:
    """``policy`` is a RetentionPolicy dataclass; we duck-type its fields
    so we don't need to import the class."""
    if policy is None:
        return (
            "<section class='card'>"
            "<h2>Retention policy</h2>"
            "<div class='empty'>Retention store not wired.</div>"
            "</section>"
        )
    return (
        "<section class='card'>"
        "<h2>Retention policy</h2>"
        "<div class='sub'>How long each class is kept before the nightly "
        "pruner removes it. <i>Identity embeddings are never auto-pruned</i>; "
        "use the Identities page to manage them.</div>"
        "<form method='post' action='system/retention'>"
        "<table class='matrix-table'>"
        "<thead><tr><th>Class</th><th>Knob</th><th>Current</th></tr></thead>"
        "<tbody>"
        "<tr><td><b>Episodic events</b></td>"
        "<td>Keep <input type='number' name='events_days' min='1' max='3650' "
        f"value='{int(policy.events_days)}' style='width:70px'> days, "
        "or <input type='number' name='events_max_gb' min='1' max='1000' "
        f"value='{int(policy.events_max_gb)}' style='width:70px'> GB "
        "(whichever lower)</td>"
        f"<td>{policy.events_days}d / {policy.events_max_gb} GB</td></tr>"
        "<tr><td><b>Frame snapshots</b></td>"
        "<td>Keep <input type='number' name='frames_days' min='1' max='365' "
        f"value='{int(policy.frames_days)}' style='width:70px'> days</td>"
        f"<td>{policy.frames_days}d</td></tr>"
        "<tr><td><b>Identity embeddings</b></td>"
        "<td><span class='muted'>never auto-prune</span></td>"
        "<td class='muted'>—</td></tr>"
        "<tr><td><b>Audit logs</b></td>"
        "<td>Keep <input type='number' name='audit_days' min='1' max='3650' "
        f"value='{int(policy.audit_days)}' style='width:70px'> days</td>"
        f"<td>{policy.audit_days}d</td></tr>"
        "</tbody></table>"
        "<div class='form-actions' style='justify-content:flex-start;margin-top:12px'>"
        "<button class='btn primary' type='submit'>Save policy</button>"
        "</div>"
        "</form>"
        "</section>"
    )


# ─── Operations card ───────────────────────────────────────────────


def _operations_section(vm: SystemViewModel) -> str:
    camera_options = "".join(
        f"<option value='{_e(cid)}'>{_e(name)}</option>"
        for cid, name in vm.cameras
    ) or "<option value=''>(no cameras configured)</option>"
    return (
        "<section class='card'>"
        "<h2>Operations</h2>"
        "<div class='sub'>Surgical privacy controls. Every operation "
        "is logged below and is irreversible — bulk deletes go straight "
        "to disk.</div>"

        # Erase last hour — single-action form
        "<h3 style='margin-top:14px'>Erase last hour</h3>"
        "<form method='post' action='system/erase-last-hour' "
        "style='display:inline' "
        "onsubmit='return confirm(\"Erase the last hour of events + "
        "frames across all cameras? This cannot be undone.\")'>"
        "<button class='btn danger' type='submit'>Erase last hour</button>"
        "</form>"
        "<div class='hint'>Bulk-deletes events + frames + clips across "
        "all cameras in the last 60 minutes.</div>"

        # Purge by camera + date range
        "<h3 style='margin-top:18px'>Purge by camera + date range</h3>"
        "<form method='post' action='system/purge' "
        "onsubmit='return confirm(\"Bulk delete events + frames? "
        "This cannot be undone.\")'>"
        "<div class='cam-row'>"
        f"<label>Camera: <select name='camera_id'>{camera_options}</select></label> "
        "<label>From: <input type='date' name='start_date' required></label> "
        "<label>To: <input type='date' name='end_date' required></label> "
        "<button class='btn danger' type='submit'>Purge</button>"
        "</div>"
        "</form>"

        # Export (deferred)
        "<h3 style='margin-top:18px'>Export</h3>"
        "<div class='empty'>Export everything about an actor / camera "
        "/ time-range lands as a follow-up. The data shape is the same "
        "— this needs a packaging worker that writes the .zip.</div>"
        "</section>"
    )


# ─── Audit log ─────────────────────────────────────────────────────


def _audit_section(audits: list[Any], *, now_ts: float | None) -> str:
    if not audits:
        body = (
            "<div class='empty'>No admin operations recorded yet. "
            "Every privacy / storage operation will appear here.</div>"
        )
    else:
        body = (
            "<table class='matrix-table'>"
            "<thead><tr><th>When</th><th>Actor</th><th>Operation</th>"
            "<th>Scope</th><th>Removed</th></tr></thead>"
            "<tbody>"
            + "".join(
                "<tr>"
                f"<td>{friendly_time_html(a.ts, now=now_ts)}</td>"
                f"<td><b>{_e(a.actor)}</b></td>"
                f"<td>{_e(a.operation)}</td>"
                f"<td class='muted'>{_e(a.scope)}</td>"
                f"<td>{a.rows_removed} rows · {_format_bytes(a.bytes_removed)}</td>"
                "</tr>"
                for a in audits
            )
            + "</tbody></table>"
        )
    return (
        "<section class='card'>"
        "<h2>Admin audit log</h2>"
        "<div class='sub'>Read-only. Every storage + privacy operation "
        "appears here with timestamp + scope + bytes removed. The trust "
        "contract: anything destructive is recorded.</div>"
        f"{body}"
        "</section>"
    )


# ─── Top-level ────────────────────────────────────────────────────


def render_system_page(vm: SystemViewModel) -> str:
    return (
        "<h1>System</h1>"
        "<div class='sub'>Storage + privacy. Diagnostics answers "
        "<i>is it working</i>; this page answers <i>what's it holding "
        "and who can see it</i>.</div>"
        + _storage_section(vm)
        + _retention_section(vm.policy)
        + _operations_section(vm)
        + _audit_section(vm.audit_log, now_ts=vm.now_ts)
    )
