"""ha-agent service entry point.

Bootstrap pattern: **HTTP server first, then everything else.**

The aiohttp server on ``0.0.0.0:8765`` is bound and serving *before* we
try to load the topology or open an HA connection. Failures in those
later steps become visible bullet items on the status page instead of
silent process crashes. This is how we turn "connection refused" into
"connection succeeded — page shows error" so misconfiguration is
diagnosable from the Web UI without needing the add-on Log tab.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import os
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from aiohttp import web

from kukiihome_ha_agent.camera_config_publisher import (
    CameraConfigPublisher,
    ChainProvider,
    JsonFileProvider,
    StreamSourceAttrProvider,
)
from kukiihome_ha_agent.camera_loop import (
    CameraLoop,
    CameraLoopRegistry,
    HACameraLoop,
    build_camera_loops_from_topology,
    build_ha_camera_loops_from_topology,
)
from kukiihome_ha_agent.client import HAClient, HAClientSettings
from kukiihome_ha_agent.config import HAAgentSettings
from kukiihome_ha_agent.discovery import DiscoveryDecision, build_decisions
from kukiihome_ha_agent.enricher import AlertEnricher
from kukiihome_ha_agent.event_store import EventStore
from kukiihome_ha_agent.health_app import attach_health_routes, build_health_service
from kukiihome_ha_agent.http_api import AlertLog, HAAgentAPI
from kukiihome_ha_agent.mcp_tools import HATools
from kukiihome_ha_agent.notifier import AlertNotifier
from kukiihome_ha_agent.notify_overrides import (
    resolve_initial_services,
    save_notify_services,
)
from kukiihome_ha_agent.overrides import (
    load_overrides,
    reset_device,
    save_overrides,
    set_device_override,
)
from kukiihome_ha_agent.preprocessor_client import PreprocessorClient
from kukiihome_ha_agent.reconciler import Reconciler
from kukiihome_ha_agent.review_page import (
    parse_label_form,
    parse_merge_form,
    parse_reject_form,
    render_review_html,
    render_track_detail_html,
)
from kukiihome_ha_agent.supervisor import (
    get_ingress_url_prefix,
    get_panel_url_base,
)

logger = structlog.get_logger(__name__)


LISTEN_HOST = "0.0.0.0"  # noqa: S104 — bind all interfaces inside the container
LISTEN_PORT = 8765


# ─────────────────────────────────────────────────────────────────────
# In-memory log ring buffer
# ─────────────────────────────────────────────────────────────────────
# Captures every structlog event into a deque so /logs and the Web UI's
# "Recent logs" card can show them without needing to scrape the add-on
# Log tab. Bounded — won't grow unbounded.

_LOG_RING: deque[dict[str, Any]] = deque(maxlen=500)


def _ring_buffer_processor(_logger, _method, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: capture each event into _LOG_RING then pass through."""
    record = dict(event_dict)
    record.setdefault("ts", datetime.now(UTC).isoformat())
    _LOG_RING.append(record)
    return event_dict


# Wire the processor into structlog's default chain. Done at import time so
# every logger anywhere in ha-agent flows through the ring buffer.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _ring_buffer_processor,
        structlog.dev.ConsoleRenderer(colors=False),
    ]
)


@dataclass
class BootState:
    """Mutable state the status-page renderer reads.

    Holds whatever stage we're in, plus any error messages from steps
    that failed. The HTTP server is always up; this dataclass tells the
    UI what's wrong (if anything).
    """

    stage: str = "starting"
    """One of: starting, topology_loaded, ha_connected, ha_failed, fatal."""
    topology_error: str | None = None
    ha_error: str | None = None
    topology_summary: dict[str, Any] = field(default_factory=dict)
    tools: HATools | None = None
    client: HAClient | None = None
    ha_url: str = "(not loaded)"
    camera_registry: CameraLoopRegistry = field(default_factory=CameraLoopRegistry)
    camera_loops: list[CameraLoop] = field(default_factory=list)
    ha_camera_loops: list[HACameraLoop] = field(default_factory=list)
    topology: Any | None = None

    # ─── v0.3.11: zero-config onboarding state ────────────────────
    # When `auto_discover` is True (the default), the bootstrap runs
    # discovery on the live HA camera list, merges per-device overrides
    # from /data/kukiihome/adapter_overrides.json, and hands the
    # resulting specs to `reconciler.apply()` — which starts/stops
    # HACameraLoops to match.
    reconciler: Reconciler | None = None
    discovery_handle: Any | None = None
    """Epic 10.8.4: zeroconf publisher handle. Held to keep the mDNS
    registration alive for the add-on's lifetime; close()d at shutdown."""
    camera_publisher: CameraConfigPublisher | None = None
    """Epic 10.1.6.3: NATS publisher that fans Reconciler diffs out
    as CameraConfigEvents to the preprocessor. None when
    PREPROCESSOR_PUBLISH_ENABLED is off (default) or when the NATS
    connection fails — the reconciler still works locally."""
    stream_source_provider: StreamSourceAttrProvider | None = None
    """Held on boot so :func:`_reconcile_discovery` can register
    each spec's device_id -> camera_entity mapping before publishing."""
    discovery_decisions: list[DiscoveryDecision] = field(default_factory=list)
    discovery_error: str | None = None
    auto_discover: bool = True
    periodic_rediscover_task: asyncio.Task | None = None
    notifier: Any | None = None
    """v0.3.12: live AlertNotifier when topology.notify.alert_services
    is non-empty. Subscribes to AlertLog and fans each alert out to
    HA notify.* services. Held here so we can surface its config on
    the status page + report send errors."""
    last_notify_test: dict[str, Any] | None = None
    """v0.3.14: most recent "Send test notification" result. Cleared
    after 60 s of display (or on next test). Rendered inline on the
    Notifications card so the user sees per-service success/failure
    immediately after clicking Send."""
    last_camera_test: dict[str, Any] | None = None
    """v0.3.14: most recent "Send test alert" result for a specific
    device. Same lifecycle as last_notify_test but rendered on the
    HA cameras card."""
    ingress_url_base: str = ""
    """v0.3.15: this add-on's HA Ingress URL prefix, e.g.
    `/api/hassio_ingress/<token>/`. Empty when not under Supervisor.
    Used by AlertNotifier so tap-actions open Kukii-Home (not HA
    root) and image attachments resolve via HA's auth session."""
    panel_url_base: str = ""
    """Epic 10.8.6: this add-on's HA frontend panel route, e.g.
    `/app/<slug>`. This is the notification tap target — it opens
    the Kukii-Home panel IN-APP with the user's session (never 401s,
    unlike the /api/ and ingress-token URLs we tried in v0.3.15-27).
    Empty when not under Supervisor."""
    preprocessor_client: PreprocessorClient | None = None
    """Epic 10.9: HTTP client to the preprocessor (inference box).
    None when KUKIIHOME_PREPROCESSOR_URL isn't set. Held so its
    httpx session can be closed cleanly at shutdown."""
    enricher: AlertEnricher | None = None
    """Epic 10.9: AlertEnricher subscribed to AlertLog. Pulls
    recognition for each alert from the preprocessor and folds it
    into the stored event. None when no preprocessor is configured."""
    event_store: Any | None = None
    """Epic 10.8.1: per-event durable store. Held on boot so the triage
    gate (built after HA connects) can persist reasoning decisions."""
    triage_gate: Any | None = None
    """Epic 10.6: reasoning gate subscribed to AlertLog in place of the
    notifier — reasons about each event and notifies only when warranted.
    None when KUKIIHOME_TRIAGE_REASONING=off (legacy direct-notify)."""
    rules_store: Any | None = None
    """Task 9: SQLite-backed rules + rule_matches persistence. Created on
    boot and shared between the /intent web pages, the /api/intent/rules
    HTTP routes, and the triage gate's per-event evaluation."""
    action_store: Any | None = None
    """Task 10: SQLite-backed perception + protective whitelists + audit log.
    Read by the cameras page's whitelist editor and the action runtimes."""
    area_store: Any | None = None
    """Iter 2.C: SQLite-backed conceptual zones (Pool, Backyard, ...) +
    camera assignments + AttentionMode + normal-hours. Read by /areas,
    /intent rule scope picker, and the reasoner."""
    preferences_store: Any | None = None
    """Iter 2.A: household-wide reasoner guidance — vigilance baseline,
    'what I care about' free text, quiet hours, per-actor relationships."""
    policy_store: Any | None = None
    """Iter 2.D: dismissal policies + transient intents + policy_hits audit
    log. Read by /policies, reverse-linked from passive activity rows."""
    retention_store: Any | None = None
    """Iter 3 (Part IX §30): per-class retention policy + admin audit log.
    Read by /system + the (future) nightly pruner."""
    dispatcher: Any | None = None
    """Iter 3 (Part X §35): the active DispatcherProvider — either
    CompositeDispatcherProvider (when LLM is configured) or
    HeuristicDispatcherProvider directly. Drawer's POST /api/drawer/turn
    handler calls .propose_async on this."""
    llm_health: Any | None = None
    """Iter 3 (Part X §35): LLMHealthTracker the composite reports
    success/failure to. /memory reads .status to decide whether to
    render the degraded-mode banner."""
    provenance_store: Any | None = None
    """Iter 3 (Part X §36): sessions + transcripts + guidance_provenance.
    Single source of truth for *how* each guidance entry came to exist —
    conversation / form / system_proposed — and the audit thread it traces
    back to. Read by /memory + the alert detail audit chain + the drawer."""
    health_service: Any | None = None
    """Epic 15: resilience watchdog + health registry for this add-on
    process. Drives the F4 (HA down) probe and backs the /health +
    /diagnostics endpoints (which the HA integration's health card reads).
    Built in main(); its watchdog runs as a background task."""


_STATUS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Kukii-Home</title>
<!-- v0.3.12: meta-refresh removed. Filling out an override form
     while a 10s refresh was pending kept clobbering the inputs;
     using a manual "Refresh" button is friendlier. The refresh
     button is in the page header below. -->
<!-- base href="./" makes all relative URLs resolve against the current
     document's directory. Belt-and-suspenders for HA Ingress, which
     may or may not preserve the trailing slash on the page URL. -->
<base href="./">
<!-- ── Notification deep-link reader (Epic 10.8.7) ─────────────────
     A tapped alert notification opens /app/<slug>#alert=<id> — the HA
     ingress panel, authenticated in-app. HA renders THIS page in a
     same-origin iframe. Depending on how HA forwards the fragment it
     may land on our own URL or only on the parent /app/<slug> URL, so
     we check both (the parent read is try/caught for the cross-origin
     case) plus a ?alert= query fallback. When an id is found we send
     the iframe to the per-alert detail page via a RELATIVE url, so it
     stays under the ingress prefix and keeps the session (no 401).
     Runs in <head>, after <base>, so it redirects before the status
     body paints — no flash of the generic page. No id → no-op, page
     renders normally. -->
<script>
  (function () {
    function findAlertId() {
      var candidates = [];
      try { candidates.push(window.location.hash); } catch (e) {}
      try { candidates.push(window.location.search); } catch (e) {}
      try {
        if (window.top && window.top !== window) {
          candidates.push(window.top.location.hash);
        }
      } catch (e) { /* cross-origin parent — ignore */ }
      for (var i = 0; i < candidates.length; i++) {
        var m = /[#&?]alert=([^&]+)/.exec(candidates[i] || "");
        if (m && m[1]) {
          try { return decodeURIComponent(m[1]); } catch (e) { return m[1]; }
        }
      }
      return null;
    }
    var id = findAlertId();
    if (id) {
      window.location.replace("alert/" + encodeURIComponent(id));
    }
  })();
</script>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
         max-width: 720px; margin: 2rem auto; padding: 0 1rem; color: #222; }
  h1 { font-weight: 600; }
  .card { border: 1px solid #e1e4e8; border-radius: 8px; padding: 1rem 1.25rem;
          margin-bottom: 1rem; background: #fafbfc; }
  .ok { color: #28a745; font-weight: 600; }
  .bad { color: #d73a49; font-weight: 600; }
  .warn { color: #e36209; font-weight: 600; }
  .muted { color: #6a737d; font-size: 0.9rem; }
  table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
  th, td { text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid #eaecef; }
  th { font-weight: 600; color: #586069; font-size: 0.85rem; }
  code { background: #f6f8fa; padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.9rem; }
  pre { background: #f6f8fa; padding: 0.75rem; border-radius: 6px;
        overflow-x: auto; font-size: 0.85rem; }
  .footer { color: #6a737d; font-size: 0.85rem; margin-top: 2rem; }
  a { color: #0366d6; text-decoration: none; }
  a:hover { text-decoration: underline; }
  /* Lightbox: click a thumbnail to view the full snapshot in-page.
     Fixed overlay covers the viewport; click anywhere (or press Esc)
     to dismiss. Pure CSS+vanilla JS — no library deps so it works
     identically under HA Ingress and direct port access. */
  .thumb { cursor: zoom-in; transition: transform 0.08s ease; }
  .thumb:hover { transform: scale(1.03); }
  #lightbox {
    display: none; position: fixed; inset: 0; z-index: 9999;
    background: rgba(0, 0, 0, 0.85); align-items: center;
    justify-content: center; cursor: zoom-out; padding: 2rem;
  }
  #lightbox.open { display: flex; }
  #lightbox img {
    max-width: 100%; max-height: 100%;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
    border-radius: 6px;
  }
  #lightbox .lb-hint {
    position: fixed; bottom: 1rem; left: 0; right: 0;
    text-align: center; color: #ddd; font-size: 0.85rem;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    pointer-events: none;
  }
</style>
</head>
<body>
<h1>Kukii-Home
<a href="." style="font-size:0.6em;font-weight:normal;margin-left:1rem;
  background:#f6f8fa;padding:0.25rem 0.6rem;border-radius:6px;
  border:1px solid #d1d5da;text-decoration:none">↻ Refresh</a>
<a href="review" style="font-size:0.6em;font-weight:normal;margin-left:0.5rem;
  background:#eef4ff;padding:0.25rem 0.6rem;border-radius:6px;
  border:1px solid #c7dbff;text-decoration:none">🔎 Review identities</a>
<a href="home" style="font-size:0.6em;font-weight:normal;margin-left:0.5rem;
  background:#fff5e6;padding:0.25rem 0.6rem;border-radius:6px;
  border:1px solid #ffd9a8;text-decoration:none">✨ Try the new UI</a>
</h1>
<p class="muted">v__VERSION__ &middot; stage: <code>__STAGE__</code></p>

__TOPOLOGY_CARD__
__HA_CAMERAS_CARD__
__CAMERAS_CARD__
__NOTIFICATIONS_CARD__
__HA_CARD__
__ALERTS_CARD__
__CAPABILITIES_CARD__
__LOGS_CARD__

<div class="footer">
<p>Next step: install the
<a href="https://github.com/DarinShapiro/Kukii-Home/blob/main/docs/install.md">Kukii-Home custom integration</a>
in HA so entities populate.</p>
<p>API: <a href="healthz">healthz</a> &middot; <a href="snapshot">snapshot</a> &middot;
<a href="capabilities">capabilities</a> &middot; <a href="recent_alerts">recent_alerts</a> &middot;
<a href="ha_cameras">ha_cameras</a> &middot; <a href="logs">logs</a></p>
</div>

<!-- Lightbox overlay: hidden until openLightbox(url) is called.
     The meta refresh re-renders the page every 10s; the overlay
     element is re-created with display:none on each render, so a
     stale lightbox can't persist past a refresh. -->
<div id="lightbox" onclick="closeLightbox()">
  <img id="lightbox-img" alt="full snapshot"/>
  <div class="lb-hint">Click anywhere or press Esc to close</div>
</div>
<script>
  function openLightbox(url) {
    var lb = document.getElementById('lightbox');
    var img = document.getElementById('lightbox-img');
    img.src = url;
    lb.classList.add('open');
    return false;  // prevent the anchor's default navigation
  }
  function closeLightbox() {
    var lb = document.getElementById('lightbox');
    lb.classList.remove('open');
    document.getElementById('lightbox-img').src = '';
  }
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeLightbox();
  });
</script>
</body>
</html>
"""


def _escape(s: str) -> str:
    """Minimal HTML escaping for entity ids / friendly names that land
    in attribute values and text. Avoids pulling in html.escape so this
    file stays self-contained."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


async def _render_notifications_card(boot: BootState) -> str:
    """Render the Notifications card with available HA notify services
    as checkboxes (v0.3.13 — no YAML editing required).

    Always-on per design: even when no services are picked, the card
    shows the discovered list so the user can opt in with a click.
    """
    header = '<div class="card"><h3>Notifications</h3>'
    if boot.tools is None:
        return header + '<p class="muted">Connect to HA first.</p></div>'

    try:
        available = await boot.tools.list_notify_services()
    except Exception as e:
        return f'{header}<p class="bad">Failed to list notify services: {_escape(str(e))}</p></div>'

    active: list[str] = []
    if boot.notifier is not None:
        active = list(boot.notifier.notify_services)
    active_set = set(active)

    if not available:
        return (
            f'{header}<p class="muted">HA exposes no <code>notify.*</code> services. '
            "Install the HA Companion app on a phone (creates "
            "<code>notify.mobile_app_*</code>) or another notify integration, "
            "then click <strong>Refresh</strong>.</p></div>"
        )

    parts = [header]
    parts.append(
        '<p class="muted">Kukii-Home pushes every alert to the checked services. '
        "Each notification includes the alert headline, camera, time, and a "
        "tap-through to the snapshot.</p>"
    )

    # Status line above the checkboxes — instant confirmation that
    # changes saved.
    if active:
        active_html = ", ".join(f"<code>{_escape(s)}</code>" for s in active)
        parts.append(f'<p><span class="ok">● Active:</span> {active_html}</p>')
    else:
        parts.append('<p class="muted"><span class="warn">○ No services selected</span></p>')

    parts.append('<form method="post" action="notify/services" style="margin-top:0.5rem">')
    for svc in available:
        svc_esc = _escape(svc)
        chk = " checked" if svc in active_set else ""
        parts.append(
            f'<label style="display:block;padding:0.2rem 0">'
            f'<input type="checkbox" name="service" value="{svc_esc}"{chk}/> '
            f"<code>{svc_esc}</code></label>"
        )
    parts.append('<button type="submit" style="margin-top:0.5rem">Save selection</button>')
    parts.append("</form>")

    # v0.3.14: Send test notification button + last result inline.
    parts.append(
        '<form method="post" action="notify/test" '
        'style="margin-top:0.75rem;display:inline-block">'
        '<button type="submit">Send test notification</button></form>'
        '<span class="muted" style="margin-left:0.5rem;font-size:0.85rem">'
        "Pushes a [TEST] alert to every checked service — verifies "
        "service is reachable + your phone is enrolled.</span>"
    )
    if boot.last_notify_test:
        parts.append(_render_notify_test_result(boot.last_notify_test))

    parts.append("</div>")
    return "".join(parts)


def _erase_recent_event_dirs(
    events_root: Any, cutoff: float, shutil_mod: Any, log: Any,
) -> tuple[int, int]:
    """Sync helper for the /system erase-last-hour endpoint. Walks the
    events directory and removes event-dirs whose mtime is newer than
    ``cutoff``, returning (bytes_removed, rows_removed). Runs in a
    thread via asyncio.to_thread from the async handler — keeps the
    event loop responsive during the disk walk."""
    bytes_removed = 0
    rows_removed = 0
    if not events_root.exists():
        return 0, 0
    for ev_dir in events_root.iterdir():
        if not ev_dir.is_dir():
            continue
        try:
            if ev_dir.stat().st_mtime < cutoff:
                continue
            for p in ev_dir.rglob("*"):
                if p.is_file():
                    try:
                        bytes_removed += p.stat().st_size
                    except OSError:
                        pass
            shutil_mod.rmtree(ev_dir, ignore_errors=True)
            rows_removed += 1
        except OSError as e:
            log.warning(
                "system.erase_last_hour.dir_skipped",
                path=str(ev_dir), error=str(e),
            )
    return bytes_removed, rows_removed


def _render_alert_404(event_id: str) -> str:
    """The notification deep-link landed on an unknown event_id —
    common when an alert was purged before the user got around to
    tapping. Show a brief, friendly page (not a stack trace) and a
    link back to recent alerts."""
    safe = html.escape(event_id, quote=True)
    return (
        "<!doctype html><html><head><title>Alert not found</title>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>body{font-family:system-ui;padding:24px;max-width:600px;"
        "margin:0 auto;color:#333}h1{font-size:1.3em}a{color:#06c}</style>"
        "</head><body>"
        f"<h1>Alert not found</h1>"
        f"<p>No alert with id <code>{safe}</code>.</p>"
        f"<p>It may have been purged, or the link may be from an older "
        f"version of the Kukii-Home notification.</p>"
        f"<p><a href='../'>See recent alerts</a></p>"
        "</body></html>"
    )


def _render_alert_page(
    event: dict[str, Any], event_id: str,
    *, audit_chain_html: str = "",
) -> str:
    """Render the per-alert page the notification tap opens to.

    Three zones (per the design discussion):
      1. Hero — annotated frame (falls back to raw if no markup yet)
         + caption.
      2. Detail — identity strip, detection list, VLM analysis when
         present, link to historical context.
      3. Action row — Dismiss / Live view / False positive. The FP
         button reveals an inline form; submitting POSTs to
         /alert/<id>/feedback.

    The page uses relative URLs throughout so it works the same
    under HA Ingress (/api/hassio_ingress/<token>/alert/<id>) and
    direct port-8765 access.
    """
    safe_id = html.escape(event_id, quote=True)
    headline = html.escape(event.get("headline") or "Alert", quote=True)
    camera_label = html.escape(
        event.get("camera_name") or event.get("camera_id") or "camera",
        quote=True,
    )
    recorded_at = event.get("recorded_at") or ""
    when = recorded_at[11:19] if recorded_at else ""
    triage = event.get("triage_decision") or "alert_fired"
    dismissed = event.get("dismissed") is True
    feedback = event.get("feedback") or {}
    vlm = event.get("vlm_response")

    # Identity strip from identified_entities (preferred) or
    # actor_matches as a fallback.
    identities = event.get("identified_entities") or []
    if not identities:
        identities = [
            {
                "actor_name": m.get("actor_id"),
                "identity_method": m.get("match_method"),
                "identity_confidence": m.get("confidence"),
            }
            for m in event.get("actor_matches") or []
        ]
    identity_html = _render_identity_strip(identities)

    detections = event.get("detections") or []
    detection_html = _render_detection_list(detections)

    # Flash messages from the redirect-back-with-query-param flow.
    # ?dismissed=1 means the user just hit Dismiss; ?fp=1 means
    # they just submitted the FP form. Plain banners, no JS.
    flash_html = ""
    # Note: aiohttp parses query into request.rel_url.query; the
    # render function doesn't get the request, so we rely on the
    # caller-side info already in `event` (dismissed flag, feedback).
    if dismissed:
        flash_html = "<div class='flash ok'>Marked dismissed.</div>"
    if feedback:
        reason = html.escape(feedback.get("reason") or "", quote=True)
        flash_html += f"<div class='flash ok'>Feedback recorded: {reason}.</div>"

    vlm_html = ""
    if vlm:
        # Compact rendering of whatever VLM response shape Phase 11
        # eventually settles on. Show the text content if it has
        # a `.text` key (common); else dump as JSON.
        if isinstance(vlm, dict) and "text" in vlm:
            # Epic 10.6: the reasoning decision. Show the explanation,
            # the criticality (why it did/didn't notify), and a marker
            # when the decision came from the stub reasoner rather than a
            # real VLM backend — so an operator never mistakes a
            # heuristic for model output.
            crit = str(vlm.get("criticality", "")).strip()
            crit_badge = (
                f" <span class='triage'>criticality: {html.escape(crit)}</span>" if crit else ""
            )
            stub_note = (
                "<p class='muted'>Stub reasoner (no VLM backend configured) — "
                "decision from coarse classification, not a vision model.</p>"
                if vlm.get("stub")
                else ""
            )
            vlm_html = (
                "<section class='card'><h2>Reasoning</h2>"
                f"<p>{html.escape(str(vlm['text']), quote=True)}{crit_badge}</p>"
                f"{stub_note}"
                "</section>"
            )
        else:
            vlm_html = (
                "<section class='card'><h2>VLM analysis</h2>"
                f"<pre>{html.escape(json.dumps(vlm, indent=2), quote=True)}</pre>"
                "</section>"
            )
    else:
        vlm_html = (
            "<section class='card muted-card'>"
            "<h2>VLM analysis</h2>"
            "<p class='muted'>Not yet analyzed.</p>"
            "</section>"
        )

    # User-review fixup #3: alert page returns body-only HTML so the
    # caller wraps it in render_shell() for the main nav. The alert-
    # specific CSS rides inline as a <style> block in the body — the
    # shell's stylesheet remains the base and these rules layer on top.
    return (
        _ALERT_PAGE_CSS
        # Hero
        + "<div class='hero'>"
        # User-review fixup #1+#2: render the static frame as the
        # primary hero element. The clip-mux pipeline (Task 1) doesn't
        # reliably produce MP4s on every event yet — the previous
        # <video> tag would attempt to load clip.mp4, get 404, and
        # show a permanent loading spinner without falling through to
        # the static frame (browser fallback for missing <source>
        # doesn't reliably swap content). Static frame is always
        # available via /alert/{id}/annotated.jpg → frame.jpg fallback.
        # The video player will return when the preprocessor exposes a
        # reliable per-event clip endpoint + we proxy with Range
        # support; until then, frame-only is the working UX.
        f"<img class='event-frame' src='{safe_id}/annotated.jpg' "
        f"alt='alert frame' "
        f"onerror=\"this.src='{safe_id}/frame.jpg'\"/>"
        f"<div class='hero-caption'>"
        f"<h1>{headline}</h1>"
        f"<div class='meta'>{camera_label} · {when} · "
        f"<span class='triage {triage}'>{triage.replace('_', ' ')}</span>"
        "</div>"
        "</div></div>"
        # Flash banners.
        + flash_html
        # Rule that fired (Epic 10.9 Part A).
        + _render_trigger_card(event)
        # Identities + detections.
        + "<section class='card'><h2>Identities</h2>"
        + identity_html
        + "</section>"
        + "<section class='card'><h2>Detections</h2>"
        + detection_html
        + "</section>"
        + vlm_html
        # Iter 2.F: trace audit chain — matched rules + protective actions
        # + policy hits. Empty when no stores wired (older boot paths).
        + audit_chain_html
        # Action row — sticky bottom.
        + "<div class='actions'>"
        + (
            "<button disabled class='btn'>Dismissed</button>"
            if dismissed
            else (
                f"<form method='post' action='{safe_id}/dismiss' "
                "style='display:inline'>"
                "<button type='submit' class='btn btn-secondary'>"
                "Dismiss</button></form>"
            )
        )
        + "<a href='#fp' class='btn btn-warn'>False positive</a>"
        + "</div>"
        # FP form, in-page (sticky button anchors here).
        + ("" if feedback else _render_fp_form(safe_id))
        # NOTE: no </body></html> — the caller wraps with render_shell,
        # which also handles the drawer aside (auto-opened by the
        # ?drawer=1 push-reply flow via _shell_response).
    )


def _render_trigger_card(event: dict[str, Any]) -> str:
    """The rule/sensor that fired this alert (Epic 10.9 Part A).

    Surfaces *why* the user got pinged — the HA AI classification
    (person / vehicle / animal) and the underlying binary_sensor
    entity — so the alert page explains itself before any
    preprocessor enrichment lands. Omitted entirely for alerts with
    no trigger info (shouldn't happen for HA-camera alerts, but keeps
    synthetic/legacy alerts clean)."""
    classification = event.get("sensor_classification")
    sensor = event.get("triggering_sensor")
    if not classification and not sensor:
        return ""
    bits = []
    if classification:
        label = html.escape(str(classification).replace("_", " ").title(), quote=True)
        bits.append(f"<strong>{label}</strong> detected")
    else:
        bits.append("Motion detected")
    if sensor:
        bits.append(f"by <code>{html.escape(str(sensor), quote=True)}</code>")
    return f"<section class='card'><h2>Triggered by</h2><p>{' '.join(bits)}</p></section>"


def _render_identity_strip(identities: list[dict[str, Any]]) -> str:
    if not identities:
        return "<p class='muted'>No confirmed identities in this frame.</p>"
    rows = []
    for i in identities:
        name = html.escape(str(i.get("actor_name") or "?"), quote=True)
        method = html.escape(str(i.get("identity_method") or "?"), quote=True)
        conf = i.get("identity_confidence")
        conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else "?"
        rows.append(
            f"<li><strong>{name}</strong> <span class='muted'>({method} · {conf_str})</span></li>"
        )
    return "<ul class='identity-list'>" + "".join(rows) + "</ul>"


def _render_detection_list(detections: list[dict[str, Any]]) -> str:
    if not detections:
        return "<p class='muted'>No detections recorded.</p>"
    # Collapse by kind for brevity — most frames have multiples of
    # the same class.
    by_kind: dict[str, int] = {}
    for d in detections:
        kind = str(d.get("kind") or "?")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    rows = [f"<li>{html.escape(k, quote=True)} x {n}</li>" for k, n in sorted(by_kind.items())]
    return "<ul>" + "".join(rows) + "</ul>"


def _render_fp_form(event_id: str) -> str:
    """The structured FP capture form. Inline on the page (anchored
    at #fp). Five preset categories + free text + (eventually) an
    actor picker when reason=wrong_identity."""
    return (
        f"<form id='fp' class='fp-form' method='post' "
        f"action='{event_id}/feedback'>"
        "<h2>Report false positive</h2>"
        "<p>What was wrong with this alert?</p>"
        "<label><input type='radio' name='reason' value='empty_frame' required> "
        "Nothing was actually happening (empty frame)</label>"
        "<label><input type='radio' name='reason' value='wrong_identity'> "
        "Wrong identity — this isn't who Kukii-Home said</label>"
        "<label>If wrong identity, who was it actually? "
        "<input type='text' name='actual_actor_id' "
        "placeholder='actor id or name (optional)'></label>"
        "<label><input type='radio' name='reason' value='known_event'> "
        "Right detection, but I don't need to be alerted "
        "(e.g., I came home)</label>"
        "<label><input type='radio' name='reason' value='camera_glitch'> "
        "Camera glitch (weather, lighting, false motion)</label>"
        "<label><input type='radio' name='reason' value='other'> Other</label>"
        "<label>Anything else? (optional)"
        "<textarea name='notes' rows='3' "
        "placeholder='Free text — helps tune the system'></textarea></label>"
        "<button type='submit' class='btn'>Submit feedback</button>"
        "</form>"
    )


_ALERT_PAGE_CSS = """<style>
:root{color-scheme:light dark}
body{font-family:system-ui;margin:0;padding:0 0 96px;color:#222;background:#f7f7f8}
.hero{background:#000;display:flex;flex-direction:column;align-items:center}
.hero img{max-width:100%;max-height:60vh;display:block}
.hero-caption{padding:12px 16px;background:rgba(0,0,0,.7);color:#fff;width:100%;box-sizing:border-box}
.hero-caption h1{margin:0 0 4px 0;font-size:1.2em}
.meta{font-size:.9em;opacity:.8}
.triage{padding:2px 6px;border-radius:3px;font-size:.85em;background:#e35;color:#fff}
.triage.alert_fired{background:#e35}
.triage.near_miss{background:#fc3;color:#333}
.triage.alert_suppressed{background:#888}
.card{background:#fff;margin:12px;padding:14px;border-radius:8px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.card h2{margin:0 0 8px;font-size:1em;color:#555}
.muted-card{opacity:.7}
.muted{color:#888}
.identity-list{margin:0;padding-left:18px}
.identity-list li{margin:4px 0}
.flash{margin:12px;padding:10px 14px;border-radius:6px;background:#dfd;color:#252}
.actions{position:fixed;bottom:0;left:0;right:0;padding:10px;background:#fff;border-top:1px solid #ddd;display:flex;gap:8px;justify-content:space-around}
.btn{display:inline-block;padding:10px 16px;border:none;border-radius:6px;background:#06c;color:#fff;text-decoration:none;font-size:1em;cursor:pointer}
.btn:disabled{background:#aaa;cursor:default}
.btn-secondary{background:#666}
.btn-warn{background:#fc3;color:#333}
.fp-form{background:#fff;margin:12px;padding:14px;border-radius:8px}
.fp-form label{display:block;margin:8px 0}
.fp-form input[type='text'],.fp-form textarea{width:100%;padding:6px;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;font-family:inherit;font-size:1em}
.fp-form button{margin-top:12px;width:100%}
@media (prefers-color-scheme:dark){body{background:#1a1a1c;color:#eee}.card,.fp-form{background:#2a2a2d;color:#eee}.actions{background:#222;border-top-color:#333}.muted{color:#888}.flash{background:#243;color:#9d9}}
</style>"""


def _render_notify_test_result(result: dict[str, Any]) -> str:
    """Render the last 'Send test notification' result inline."""
    ts = result.get("ts", "")
    when = ts[11:19] if ts else "—"
    parts = [
        f'<div style="margin-top:0.75rem;padding:0.5rem 0.75rem;'
        'background:#f6f8fa;border-radius:6px;border-left:3px solid #0366d6">'
        f'<strong>Test result</strong> <span class="muted">at {_escape(when)} UTC</span><br/>'
    ]
    if result.get("error"):
        parts.append(f'<span class="bad">✗ {_escape(result["error"])}</span>')
    else:
        services = result.get("services", [])
        had_image = result.get("alert", {}).get("had_image", False)
        if had_image:
            parts.append(
                '<span class="muted">Included a real snapshot from the most '
                "recent alert as the image attachment.</span><br/>"
            )
        else:
            parts.append(
                '<span class="muted">No image attached '
                "(no real alerts yet to borrow a snapshot from).</span><br/>"
            )
        for svc in services:
            if svc["ok"]:
                parts.append(
                    f'<span class="ok">✓</span> <code>{_escape(svc["service"])}</code><br/>'
                )
            else:
                parts.append(
                    f'<span class="bad">✗</span> <code>{_escape(svc["service"])}</code>'
                    f': <span class="muted">{_escape(svc.get("error") or "")}</span><br/>'
                )
    parts.append("</div>")
    return "".join(parts)


def _render_ha_cameras_card(boot: BootState) -> str:
    """Render the HA cameras card.

    Auto-discover ON (the default):
        Interactive per-device cards with Enable/Disable + Override
        controls. Each control posts to /discovery/* and triggers a
        live reconcile — no add-on restart needed.

    Auto-discover OFF (legacy / advanced):
        Read-only discovery dump; user is hand-writing adapters.
    """
    header = '<div class="card"><h3>HA cameras</h3>'

    if boot.tools is None:
        return header + '<p class="muted">Connect to HA first (see card below).</p></div>'

    if not boot.auto_discover:
        # Legacy path — kept for power users who want the read-only
        # discovery view + hand-written adapters.
        legacy = (
            '<p class="muted">Auto-discover is <strong>off</strong>. '
            "Hand-write <code>adapters</code> in the add-on Configuration tab.</p>"
        )
        return header + legacy + "</div>"

    parts = [header]
    parts.append(
        '<p class="muted">Auto-discover is <strong>on</strong>. '
        "Kukii-Home AI-picks the best stream + motion sensors per device. "
        "Override any choice below — changes apply live.</p>"
    )

    if boot.discovery_error:
        parts.append(
            f'<p class="bad">Last discovery refresh failed: {_escape(boot.discovery_error)}</p>'
        )

    if not boot.discovery_decisions:
        parts.append(
            '<p class="muted">No HA cameras discovered yet. Add one via an HA '
            "integration (Reolink, Dahua, ONVIF, Generic Camera, ...) and this "
            "card will populate within 5 minutes (or click Refresh below).</p>"
        )
        parts.append(_refresh_form_html())
        return "".join(parts) + "</div>"

    for decision in boot.discovery_decisions:
        parts.append(_render_device_block(decision))
        # If the user just clicked "Send test alert" on THIS device,
        # render the result inline beneath the device block.
        if boot.last_camera_test and boot.last_camera_test.get("device_id") == decision.device_id:
            parts.append(_render_camera_test_result(boot.last_camera_test))

    parts.append(_refresh_form_html())
    return "".join(parts) + "</div>"


def _render_camera_test_result(result: dict[str, Any]) -> str:
    """Render the last 'Send test alert' result for a device."""
    ts = result.get("ts", "")
    when = ts[11:19] if ts else "—"
    parts = [
        '<div style="margin-top:0.25rem;margin-bottom:0.5rem;'
        "padding:0.5rem 0.75rem;background:#f6f8fa;border-radius:6px;"
        'border-left:3px solid #0366d6">'
        f"<strong>Test alert result</strong> "
        f'<span class="muted">at {_escape(when)} UTC</span><br/>'
    ]
    if result.get("error"):
        parts.append(f'<span class="bad">✗ {_escape(result["error"])}</span><br/>')
    snap_bytes = result.get("snapshot_bytes", 0)
    if snap_bytes:
        parts.append(f'<span class="ok">✓</span> snapshot captured: {snap_bytes:,} bytes<br/>')
    elif not result.get("error"):
        parts.append('<span class="warn">no snapshot captured</span><br/>')
    alert_id = result.get("alert_id")
    if alert_id:
        parts.append(
            f'<span class="ok">✓</span> alert recorded: <code>{_escape(alert_id)}</code>'
            f' &middot; <a href="alerts/{_escape(alert_id)}/snapshot" target="_blank">view snapshot</a><br/>'
        )
    notify_services = result.get("notify_services", [])
    if notify_services:
        for svc in notify_services:
            if svc["ok"]:
                parts.append(
                    f'<span class="ok">✓</span> sent to <code>{_escape(svc["service"])}</code><br/>'
                )
            else:
                parts.append(
                    f'<span class="bad">✗</span> <code>{_escape(svc["service"])}</code>'
                    f': <span class="muted">{_escape(svc.get("error") or "")}</span><br/>'
                )
    elif alert_id and not result.get("error"):
        parts.append(
            '<span class="muted">No notify services selected — '
            "alert was recorded but nothing was pushed to HA.</span>"
        )
    parts.append("</div>")
    return "".join(parts)


def _refresh_form_html() -> str:
    return (
        '<form method="post" action="discovery/refresh" '
        'style="margin-top:1rem;display:inline-block">'
        '<button type="submit">Re-discover now</button>'
        "</form>"
    )


def _render_device_block(d: DiscoveryDecision) -> str:
    """Render one device's card with Enable/Disable + Override forms."""
    dev_id = _escape(d.device_id)
    name = _escape(d.friendly_name)
    border_color = "#28a745" if d.enabled else "#d73a49"
    badge = (
        '<span class="ok">● Enabled</span>' if d.enabled else '<span class="bad">○ Disabled</span>'
    )

    out: list[str] = [
        f'<div style="border-left: 3px solid {border_color}; '
        'padding: 0.5rem 0.75rem; margin: 0.5rem 0; background: #fff;">'
        f"<strong>{name}</strong> &middot; {badge}"
        f' &middot; <span class="muted">device id: <code>{dev_id}</code></span>'
    ]

    # Auto-disabled reason banner.
    if not d.enabled and d.auto_disabled_reason:
        out.append(
            f'<br/><span class="warn">Auto-disabled: {_escape(d.auto_disabled_reason)}</span>'
        )

    # Active spec (when enabled).
    if d.enabled and d.spec is not None:
        spec = d.spec
        source_badge = (
            '<span class="muted">(AI pick)</span>'
            if spec.source == "auto"
            else '<span class="warn">(override)</span>'
        )
        motion_html = ", ".join(f"<code>{_escape(m)}</code>" for m in spec.motion_entities)
        out.append(
            f'<br/><span class="muted">Stream:</span> '
            f"<code>{_escape(spec.camera_entity)}</code> {source_badge}"
            f'<br/><span class="muted">Motion:</span> {motion_html or "<em>none</em>"}'
            f'<br/><span class="muted">Cooldown:</span> '
            f"<code>{spec.cooldown_seconds:g}s</code>"
        )

    # Enable / Disable toggle.
    out.append('<div style="margin-top:0.5rem">')
    if d.enabled:
        out.append(
            f'<form method="post" action="discovery/enable" style="display:inline">'
            f'<input type="hidden" name="device_id" value="{dev_id}"/>'
            f'<input type="hidden" name="enabled" value="false"/>'
            f'<button type="submit">Disable</button></form>'
        )
    else:
        out.append(
            f'<form method="post" action="discovery/enable" style="display:inline">'
            f'<input type="hidden" name="device_id" value="{dev_id}"/>'
            f'<input type="hidden" name="enabled" value="true"/>'
            f'<button type="submit">Enable</button></form>'
        )
    out.append(
        f' <form method="post" action="discovery/reset" style="display:inline">'
        f'<input type="hidden" name="device_id" value="{dev_id}"/>'
        f'<button type="submit">Reset to AI defaults</button></form>'
    )
    # v0.3.14: per-device "Send test alert" — captures a real snapshot,
    # records a [TEST] alert, fires the notifier. Verifies the full
    # camera → alert → notify pipeline without waiting for motion.
    if d.enabled:
        out.append(
            f' <form method="post" action="discovery/test_alert" style="display:inline">'
            f'<input type="hidden" name="device_id" value="{dev_id}"/>'
            f'<button type="submit">Send test alert</button></form>'
        )
    out.append("</div>")

    # ─── v0.3.16: motion-switch + fallback banners ────────────────
    if d.enabled and d.spec is not None:
        # 1. Any parent HA motion-detection switch in "off"? Surface
        #    a one-click Turn-on form. (Common misconfig: AI sensors
        #    can't fire if the device-level switch is off.)
        off_switches = [s for s in (d.motion_switches or []) if s.get("state") == "off"]
        for sw in off_switches:
            eid = _escape(sw["entity_id"])
            name = _escape(sw.get("friendly_name") or sw["entity_id"])
            out.append(
                '<div style="margin-top:0.5rem;padding:0.5rem 0.75rem;'
                'background:#fff8e1;border-radius:6px;border-left:3px solid #e36209">'
                f"<strong>⚠ HA switch is off:</strong> <code>{eid}</code> "
                f"({name})<br/>"
                '<span class="muted">Motion sensors won\'t fire while this '
                "switch is off.</span><br/>"
                '<form method="post" action="discovery/switch_toggle" '
                'style="display:inline;margin-top:0.3rem">'
                f'<input type="hidden" name="entity_id" value="{eid}"/>'
                f'<input type="hidden" name="action" value="turn_on"/>'
                '<button type="submit">Turn on</button></form>'
                "</div>"
            )

        # 2. AI sensors picked but a generic _motion_alarm is also
        #    available? Offer one-click fallback — useful when the
        #    camera-side AI Plan isn't enabled (Dahua Smart Plan
        #    trap) and the user wants alerts NOW regardless of
        #    noise.
        if d.suggest_generic_motion:
            alarm = _escape(d.suggest_generic_motion)
            out.append(
                '<div style="margin-top:0.5rem;padding:0.5rem 0.75rem;'
                'background:#f1f8ff;border-radius:6px;border-left:3px solid #0366d6">'
                "<strong>💡 AI sensors not firing?</strong> "
                "If you've waited a few minutes and nothing's coming through "
                "(common cause: camera-side AI plan not configured), "
                f"you can fall back to <code>{alarm}</code> — fires on any "
                "motion (noisier, but works without camera-side setup).<br/>"
                '<form method="post" action="discovery/use_generic_motion" '
                'style="display:inline;margin-top:0.3rem">'
                f'<input type="hidden" name="device_id" value="{dev_id}"/>'
                f'<input type="hidden" name="motion_entity" value="{alarm}"/>'
                '<button type="submit">Use generic motion alarm</button></form>'
                "</div>"
            )

    # ─── Override section (collapsed by default) ──────────────────
    out.append(
        '<details style="margin-top:0.5rem"><summary>Override stream / motion / cooldown</summary>'
    )
    out.append(
        f'<form method="post" action="discovery/override" style="margin-top:0.5rem">'
        f'<input type="hidden" name="device_id" value="{dev_id}"/>'
    )

    # Stream radio list.
    current_stream = d.spec.camera_entity if d.spec else ""
    out.append('<div style="margin-bottom:0.5rem"><strong>Stream</strong><br/>')
    out.append(
        '<label><input type="radio" name="stream" value=""'
        + (" checked" if not current_stream else "")
        + "/> Use AI pick</label><br/>"
    )
    for s in d.candidate_streams:
        s_esc = _escape(s)
        chk = " checked" if s == current_stream else ""
        out.append(
            f'<label><input type="radio" name="stream" value="{s_esc}"{chk}/> '
            f"<code>{s_esc}</code></label><br/>"
        )
    out.append("</div>")

    # Motion checkboxes.
    current_motion = set(d.spec.motion_entities) if d.spec else set()
    out.append(
        '<div style="margin-bottom:0.5rem"><strong>Motion sensors</strong>'
        '<br/><label><input type="checkbox" name="motion_use_ai" value="1"/> '
        "Leave blank to use AI pick</label><br/>"
    )
    for m in d.candidate_motions:
        m_esc = _escape(m)
        chk = " checked" if m in current_motion else ""
        out.append(
            f'<label><input type="checkbox" name="motion" value="{m_esc}"{chk}/> '
            f"<code>{m_esc}</code></label><br/>"
        )
    out.append("</div>")

    # Cooldown.
    cooldown_val = d.spec.cooldown_seconds if d.spec else 10.0
    out.append(
        '<div style="margin-bottom:0.5rem"><strong>Cooldown seconds</strong> '
        f'<input type="number" name="cooldown" step="0.5" min="1" max="3600" '
        f'value="{cooldown_val:g}" style="width:6rem"/> '
        '<span class="muted">(blank = AI default of 10s)</span></div>'
    )

    out.append('<button type="submit">Save override</button>')
    out.append("</form></details>")
    out.append("</div>")
    return "".join(out)


async def _render_status(boot: BootState, alert_log: AlertLog) -> str:
    from kukiihome_ha_agent import __version__

    # ─── topology card ─────────────────────────────────────────────
    if boot.topology_error:
        topology_card = (
            '<div class="card"><h3>Topology config</h3>'
            f'<p class="bad">Failed to load.</p><pre>{boot.topology_error}</pre>'
            "</div>"
        )
    else:
        rows = "".join(
            f"<tr><td>{k}</td><td><code>{v}</code></td></tr>"
            for k, v in boot.topology_summary.items()
        )
        topology_card = (
            '<div class="card"><h3>Topology config</h3>'
            '<p class="ok">Loaded.</p>'
            f"<table>{rows}</table></div>"
        )

    # ─── HA connection card ────────────────────────────────────────
    if boot.tools is None:
        ha_card = (
            '<div class="card"><h3>Connection to Home Assistant</h3>'
            f'<p class="bad">{boot.ha_error or "not connected"}</p>'
            f'<p class="muted">URL: <code>{boot.ha_url}</code></p>'
            '<p class="muted">Set <code>ha_token</code> in the add-on '
            "Configuration tab. (Supervisor add-ons normally inject "
            "<code>SUPERVISOR_TOKEN</code> automatically; if you see this "
            "message, the bootstrap script didn't pick it up — check the "
            "add-on Log tab for <code>[bootstrap]</code> lines.)</p></div>"
        )
    else:
        entity_count: int | str = "—"
        caps_html = "—"
        try:
            states = await boot.tools.get_snapshot()
            entity_count = len(states)
            caps = await boot.tools.list_capabilities()
            caps_html = ", ".join(f"{c.domain} ({c.entity_count})" for c in caps) or "none"
            status_html = '<span class="ok">OK</span>'
        except Exception as e:
            status_html = f'<span class="bad">unreachable: {e}</span>'
        ha_card = (
            '<div class="card"><h3>Connection to Home Assistant</h3>'
            f"<p>Status: {status_html}</p>"
            f'<p class="muted">URL: <code>{boot.ha_url}</code></p>'
            f'<p class="muted">Entities visible: <strong>{entity_count}</strong></p>'
            "</div>"
        )
        # We also use caps_html in the capability card below — stash it.
        boot.topology_summary["__caps_html"] = caps_html

    # ─── alerts card ───────────────────────────────────────────────
    alerts = alert_log.recent(10)
    if alerts:
        row_strs = []
        for a in reversed(alerts):
            alert_id = a.get("alert_id", "?")
            headline = a.get("headline", "")
            # Link the headline to the per-alert detail page (the same
            # page a notification tap deep-links to). Relative URL so it
            # resolves under the ingress prefix. Unknown id → plain text.
            if alert_id and alert_id != "?":
                headline = f"<a href='alert/{_escape(alert_id)}'>{headline or 'View alert'}</a>"
            tier = a.get("tier", "")
            # Status reflects the triage decision (Epic 10.6): alerts the
            # reasoner dismissed show "dismissed" + why, so the timeline
            # is complete but the user sees what was silenced and the
            # reason. User-acknowledged still wins as the latest state.
            triage_status = a.get("triage_status")
            triage_expl = a.get("triage_explanation") or ""
            if a.get("acknowledged"):
                status = "ack"
            elif triage_status == "dismissed":
                tip = f' title="{_escape(triage_expl)}"' if triage_expl else ""
                status = f"<span class='muted'{tip}>dismissed</span>"
            elif triage_status == "alerted":
                status = "<span class='ok'>alerted</span>"
            else:
                status = "open"

            # Time: show HH:MM:SS from recorded_at. Old alerts without
            # the field (logged before v0.3.4) render an em-dash.
            recorded_at = a.get("recorded_at")
            if recorded_at:
                try:
                    from datetime import datetime as _dt

                    when = _dt.fromisoformat(recorded_at).strftime("%H:%M:%S")
                except (ValueError, TypeError):
                    when = recorded_at[:8]
            else:
                when = "—"

            # Thumbnail. Click → in-page lightbox overlay with full-size
            # image (vanilla JS, see openLightbox in the page template).
            # The anchor's href is kept as a fallback so middle-click /
            # right-click → "open in new tab" still works.
            #
            # IMPORTANT: relative URLs (no leading slash). When the page is
            # served through HA ingress at /api/hassio_ingress/<token>/,
            # absolute paths resolve to the HA host root (not the ingress
            # prefix) and 404. Relative URLs work both via ingress AND via
            # direct port 8765 access.
            if a.get("evidence_ref"):
                snap_url = f"alerts/{alert_id}/snapshot"
                thumb = (
                    f"<a href='{snap_url}' target='_blank' "
                    f"onclick=\"return openLightbox('{snap_url}')\">"
                    f"<img class='thumb' src='{snap_url}' "
                    "style='max-width: 96px; max-height: 54px; "
                    "border-radius: 4px; vertical-align: middle;' "
                    "onerror=\"this.style.display='none'\"/></a>"
                )
            else:
                thumb = '<span class="muted">—</span>'

            # v0.3.19: latency summary inline. Most useful single
            # number: HA's view of motion → snapshot in hand. Helps
            # answer "is this snapshot likely to still reflect what
            # triggered the alert?"
            timings = a.get("timings") or {}
            total_ms = timings.get("ha_to_snapshot_complete_ms")
            ha_lag_ms = timings.get("ha_to_received_ms")
            if total_ms is None:
                latency_html = '<span class="muted">—</span>'
            else:
                total_s = total_ms / 1000.0
                cls = "ok" if total_s < 1.5 else ("warn" if total_s < 4.0 else "bad")
                lag_str = (
                    f"<br/><span class='muted'>HA→us {ha_lag_ms:.0f}ms</span>"
                    if ha_lag_ms is not None
                    else ""
                )
                latency_html = f"<span class='{cls}'>{total_s:.1f}s</span>{lag_str}"

            row_strs.append(
                f"<tr><td>{thumb}</td>"
                f"<td>{when}</td>"
                f"<td>{headline}</td>"
                f"<td>{tier}</td>"
                f"<td>{status}</td>"
                f"<td>{latency_html}</td></tr>"
            )
        alerts_card = (
            '<div class="card"><h3>Recent alerts</h3>'
            "<table><tr><th>Snapshot</th><th>Time</th><th>Headline</th>"
            "<th>Tier</th><th>Status</th><th>Latency</th></tr>" + "".join(row_strs) + "</table>"
            '<p class="muted" style="font-size:0.8rem;margin-top:0.5rem">'
            "Latency = HA's view of motion → snapshot in hand. "
            "Green &lt;1.5s · orange &lt;4s · red &gt;4s. "
            "HA→us = WebSocket lag. The camera→HA leg (real motion → "
            "HA seeing the sensor flip) isn't shown — we can't measure "
            "it without camera-side instrumentation."
            "</p></div>"
        )
    else:
        alerts_card = (
            '<div class="card"><h3>Recent alerts</h3><p class="muted">No alerts yet.</p></div>'
        )

    # ─── capabilities card ─────────────────────────────────────────
    caps_html = boot.topology_summary.pop("__caps_html", "—")
    # Notification config now lives in the dedicated Notifications
    # card (v0.3.13). Capabilities is back to its original purpose.
    caps_card = (
        '<div class="card"><h3>Capabilities</h3>'
        '<p class="muted">Domains Kukii-Home can act on in your HA:</p>'
        f"<p>{caps_html}</p></div>"
    )

    # ─── HA cameras detected card ─────────────────────────────────
    # When auto_discover is on (the default), this becomes the SETUP
    # SURFACE: per-device cards with Enable/Disable + Override controls
    # that POST to /discovery/* — no YAML editing needed.
    #
    # When auto_discover is off, falls back to the legacy read-only
    # discovery view (the user has opted into hand-writing `adapters`).
    ha_cameras_card = _render_ha_cameras_card(boot)

    # ─── Notifications card (v0.3.13) ─────────────────────────────
    # Lists every notify.* service HA exposes as a checkbox. Save
    # selection → POST /notify/services → notifier.set_services live.
    notifications_card = await _render_notifications_card(boot)

    # ─── cameras card ──────────────────────────────────────────────
    cam_statuses = boot.camera_registry.all()
    if not cam_statuses:
        cameras_card = (
            '<div class="card"><h3>Cameras</h3>'
            '<p class="muted">No cameras configured. Paste an <code>adapters</code> '
            "block into the add-on Configuration tab. Example:</p>"
            "<pre>adapters:\n  - name: front-cam\n    kind: rtsp-direct\n"
            "    streams:\n      - id: cam_front\n"
            "        rtsp_url: rtsp://user:pass@192.168.1.50/stream</pre></div>"
        )
    else:
        rows = []
        for cs in cam_statuses:
            state_class = {
                "running": "ok",
                "subscribed": "ok",
                "starting": "muted",
                "opening": "muted",
                "error": "bad",
                "stopped": "muted",
            }.get(cs.state, "muted")
            last_motion = cs.last_motion_at.strftime("%H:%M:%S") if cs.last_motion_at else "—"
            detail = f"<br/><span class='muted'>{cs.last_error}</span>" if cs.last_error else ""
            # Inline snapshot thumbnail when one exists for this camera.
            # Cache-bust on motion count so a new snapshot replaces the old
            # without manual reload. Click → lightbox (see status page
            # template); fallback link opens raw image in new tab.
            if cs.motion_events > 0:
                cam_snap_url = f"cameras/{cs.camera_id}/snapshot?v={cs.motion_events}"
                thumb_html = (
                    # Relative URL — see comment on alerts-table thumbnails about
                    # why absolute paths break under HA ingress.
                    f"<a href='{cam_snap_url}' target='_blank' "
                    f"onclick=\"return openLightbox('{cam_snap_url}')\">"
                    f"<img class='thumb' src='{cam_snap_url}' "
                    "style='max-width: 160px; max-height: 90px; border-radius: 4px;'"
                    " onerror=\"this.style.display='none'\"/></a>"
                )
            else:
                thumb_html = '<span class="muted">no snapshot yet</span>'
            rows.append(
                f"<tr><td>{cs.camera_id}<br/>{thumb_html}</td>"
                f"<td><span class='{state_class}'>{cs.state}</span>{detail}</td>"
                f"<td>{cs.motion_events}</td>"
                f"<td>{last_motion}</td></tr>"
            )
        cameras_card = (
            '<div class="card"><h3>Cameras configured for Kukii-Home</h3>'
            "<table><tr><th>ID</th><th>State</th><th>Motion events</th>"
            "<th>Last motion</th></tr>" + "".join(rows) + "</table></div>"
        )

    # ─── recent logs card ──────────────────────────────────────────
    # Last 30 lines from the in-memory ring buffer. Surfaces failures
    # right next to the state, so debugging doesn't require the add-on
    # Log tab. Level color-coded; warning/error highlighted.
    recent_logs = list(_LOG_RING)[-30:]
    if recent_logs:
        log_rows = []
        for entry in reversed(recent_logs):
            level = str(entry.get("level", "info"))
            color_class = {
                "warning": "warn",
                "error": "bad",
                "critical": "bad",
            }.get(level, "muted")
            event = entry.get("event", "")
            ts = entry.get("timestamp") or entry.get("ts", "")
            # Trim ts to HH:MM:SS for display
            short_ts = ts[11:19] if len(ts) >= 19 else ts
            # Extra key=value pairs (everything except meta keys)
            meta_keys = {"event", "level", "timestamp", "ts", "logger", "_record"}
            extras = " ".join(f"{k}={v}" for k, v in entry.items() if k not in meta_keys)
            log_rows.append(
                f"<tr><td><code>{short_ts}</code></td>"
                f"<td><span class='{color_class}'>{level}</span></td>"
                f"<td><code>{event}</code></td>"
                f"<td><span class='muted' style='font-size:0.85rem;'>{extras}</span></td></tr>"
            )
        logs_card = (
            '<div class="card"><h3>Recent logs</h3>'
            "<table><tr><th>Time</th><th>Level</th><th>Event</th><th>Fields</th></tr>"
            + "".join(log_rows)
            + '</table><p class="muted">Full log: '
            '<a href="logs?limit=200">/logs?limit=200</a> · '
            '<a href="logs?level=warning">/logs?level=warning</a></p></div>'
        )
    else:
        logs_card = (
            '<div class="card"><h3>Recent logs</h3>'
            '<p class="muted">No log entries captured yet.</p></div>'
        )

    return (
        _STATUS_PAGE.replace("__VERSION__", __version__)
        .replace("__STAGE__", boot.stage)
        .replace("__TOPOLOGY_CARD__", topology_card)
        .replace("__HA_CARD__", ha_card)
        .replace("__ALERTS_CARD__", alerts_card)
        .replace("__CAPABILITIES_CARD__", caps_card)
        .replace("__CAMERAS_CARD__", cameras_card)
        .replace("__HA_CAMERAS_CARD__", ha_cameras_card)
        .replace("__NOTIFICATIONS_CARD__", notifications_card)
        .replace("__LOGS_CARD__", logs_card)
    )


def _build_app(*, boot: BootState, alert_log: AlertLog, event_store: EventStore) -> web.Application:
    api = HAAgentAPI(
        tools=None, alert_log=alert_log,
        rules_store=boot.rules_store,  # may be None pre-Task9 boot paths
    )  # tools rebound below

    async def status_page(_request: web.Request) -> web.Response:
        body = await _render_status(boot, alert_log)
        return web.Response(text=body, content_type="text/html")

    async def snapshot_for_camera(request: web.Request) -> web.Response:
        """Return the latest snapshot captured for ``camera_id``.

        File path is taken from the most recent alert whose
        ``camera_id`` matches and whose ``evidence_ref`` is set. This
        lets the Web UI embed inline thumbnails per camera without us
        tracking snapshot state separately.
        """
        cam_id = request.match_info["camera_id"]
        recent = alert_log.recent(50)
        for alert in reversed(recent):
            if alert.get("camera_id") == cam_id and alert.get("evidence_ref"):
                path = alert["evidence_ref"]
                try:
                    return web.FileResponse(path)
                except FileNotFoundError:
                    return web.Response(status=404, text=f"snapshot file missing: {path}")
        return web.Response(status=404, text=f"no snapshots for {cam_id}")

    async def logs_handler(request: web.Request) -> web.Response:
        """Return recent log entries as JSON.

        Query params:
          limit=N (default 100) — max entries to return
          level=warning — minimum level (debug/info/warning/error)
        """
        try:
            limit = int(request.rel_url.query.get("limit", "100"))
        except ValueError:
            limit = 100
        level_filter = request.rel_url.query.get("level", "").lower()
        level_rank = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
        min_rank = level_rank.get(level_filter, 0)

        items = list(_LOG_RING)[-limit:]
        if level_filter:
            items = [e for e in items if level_rank.get(str(e.get("level", "info")), 0) >= min_rank]
        return web.json_response({"logs": items, "count": len(items)})

    async def debug_topology(_request: web.Request) -> web.Response:
        """Return the loaded topology as JSON (current in-memory state)."""
        if boot.topology is None:
            return web.json_response({"loaded": False, "error": boot.topology_error}, status=503)
        return web.json_response({"loaded": True, "topology": boot.topology.model_dump()})

    async def debug_alert(request: web.Request) -> web.Response:
        """Return one alert's full JSON payload (no snapshot bytes)."""
        alert = alert_log.get(request.match_info["alert_id"])
        if alert is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(alert)

    # ─── Epic 10.8.1: per-alert page + actions ───────────────────────
    #
    # These are the routes the notification tap UX hits:
    #   GET  /alert/<id>           → HTML page (the tap target)
    #   GET  /alert/<id>/frame.jpg → raw snapshot at alert time
    #   POST /alert/<id>/dismiss   → mark dismissed (no UI)
    #   POST /alert/<id>/feedback  → structured FP form submission
    #
    # All four go through EventStore (not the lightweight AlertLog).
    # Unknown event_ids return 404 with a brief HTML message rather
    # than blank pages.

    async def alert_page(request: web.Request) -> web.Response:
        event_id = request.match_info["event_id"]
        event = event_store.get(event_id)
        if event is None:
            return web.Response(
                status=404,
                text=_render_alert_404(event_id),
                content_type="text/html",
            )
        # Iter 2.F: trace audit chain — matched rules + protective actions +
        # policy hits, all keyed on incident/event_id. Empty when none of
        # the audit stores have a record for this incident.
        import time as _time

        from kukiihome_ha_agent.web_ui.trace import build_audit_chain_html
        audit_html = build_audit_chain_html(
            incident_id=event_id,
            rules_store=getattr(boot, "rules_store", None),
            action_store=getattr(boot, "action_store", None),
            policy_store=getattr(boot, "policy_store", None),
            now_ts=_time.time(),
        )
        # Iter 3 / Part X §40: push-reply fragment-load. _shell_response
        # auto-builds the drawer when ?drawer=1 is in the query; the
        # alert_context override pins the conversation to this event_id
        # (which lives in the path, not the query — so the generic
        # ?alert=… extraction in _shell_response wouldn't find it).
        body = _render_alert_page(
            event, event_id, audit_chain_html=audit_html,
        )
        return _shell_response(
            request, "", body, alert_context=event_id,
        )

    async def alert_frame(request: web.Request) -> web.Response:
        path = event_store.frame_path(request.match_info["event_id"])
        if path is None:
            return web.Response(status=404, text="no frame for this alert")
        return web.FileResponse(path)

    async def alert_clip(request: web.Request) -> web.Response:
        """Task 1 stop-gap: proxy the preprocessor's on-demand-muxed clip.

        Three paths in priority order (mirrors planning/web-ui-iteration-1.md
        Task 1 §Stop-gap):
          1. external_clip (Agent DVR delegated mode) — not yet implemented;
             returns 404 so the UI falls through.
          2. Local cached event-dir clip.mp4 (Design A future) — not present
             today, also passes through.
          3. Preprocessor mux of legacy JPEGs (the stop-gap path) — what
             this implementation actually does today.

        Returns raw bytes for now (no range-request support on this proxy
        leg). For long clips this means the browser's <video> seek bar will
        work but seeking re-fetches the whole file; acceptable for stop-gap
        scope. A streaming proxy with Range pass-through is the obvious
        follow-up if events grow long.
        """
        event_id = request.match_info["event_id"]
        if boot.preprocessor_client is None:
            return web.Response(status=503, text="preprocessor not configured")
        data = await boot.preprocessor_client.fetch_event_clip_mp4(event_id)
        if data is None:
            return web.Response(status=404, text="no clip for this event")
        return web.Response(body=data, content_type="video/mp4")

    async def alert_annotated_frame(request: web.Request) -> web.Response:
        """The preprocessor's marked-up version of the frame. Falls
        back to the raw frame when no annotated artifact exists
        (Phase 10.3.3 wired markup but the HA-agent doesn't yet
        receive annotated bytes — that's a future Phase 11+ hop)."""
        event_id = request.match_info["event_id"]
        path = event_store.frame_path(event_id, annotated=True)
        if path is None:
            path = event_store.frame_path(event_id)
        if path is None:
            return web.Response(status=404, text="no frame for this alert")
        return web.FileResponse(path)

    async def alert_dismiss(request: web.Request) -> web.Response:
        """Mark the event dismissed. No UI response — the iOS action
        button fires this in the background, and the FP-form page POSTs
        here on success. Returns 303 to the alert page for browser
        callers; JSON {ok: True} for programmatic ones."""
        event_id = request.match_info["event_id"]
        ok = event_store.mark_dismissed(event_id)
        # Also propagate to AlertLog so /recent_alerts polling reflects it.
        alert_log.acknowledge(event_id, feedback="dismissed")
        if request.headers.get("Accept", "").startswith("application/json"):
            return web.json_response({"ok": ok}, status=(200 if ok else 404))
        # Browser POST → redirect back to the alert page with a flash.
        # Redirect to ../{event_id} (not ../alert/{event_id}) — the
        # POST is at /alert/{id}/dismiss, so ../{id} resolves to
        # /alert/{id} correctly.
        target = f"../{event_id}?dismissed=1" if ok else f"../{event_id}"
        raise web.HTTPSeeOther(location=target)

    async def alert_feedback(request: web.Request) -> web.Response:
        """Record structured FP feedback from the form.

        Form fields (all optional except reason):
          reason: empty_frame | wrong_identity | known_event | camera_glitch | other
          actual_actor_id: only when reason=wrong_identity
          notes: free text
        """
        event_id = request.match_info["event_id"]
        try:
            form = await request.post()
        except Exception:
            return web.Response(status=400, text="malformed form")
        reason = form.get("reason") or ""
        if reason not in (
            "empty_frame",
            "wrong_identity",
            "known_event",
            "camera_glitch",
            "other",
        ):
            return web.Response(status=400, text=f"unknown reason: {reason}")

        from datetime import UTC, datetime

        feedback = {
            "reason": reason,
            "actual_actor_id": form.get("actual_actor_id") or None,
            "notes": form.get("notes") or "",
            "submitted_at": datetime.now(UTC).isoformat(),
            "kind": "false_positive",
        }
        ok = event_store.record_feedback(event_id, feedback=feedback)
        if not ok:
            return web.Response(status=404, text=f"no event {event_id}")
        # Also drop a hint into the legacy AlertLog so the recent-
        # alerts table can show "FP reported".
        alert_log.acknowledge(event_id, feedback=f"fp:{reason}")
        target = f"../{event_id}?fp=1"
        raise web.HTTPSeeOther(location=target)

    async def debug_test_snapshot(request: web.Request) -> web.Response:
        """Force a snapshot fetch on demand. Reports which path won + the
        first bytes (hex) so we know if it's a real JPEG, HTML masquerading
        as JPEG, or something else.

        Usage:
          GET /debug/test_snapshot?camera_entity=camera.front_south_camera_profile000_mainstream
        """
        entity_id = request.rel_url.query.get("camera_entity") or ""
        if not entity_id:
            return web.json_response({"error": "missing ?camera_entity=camera.X"}, status=400)
        if boot.client is None:
            return web.json_response({"error": "HA client not connected"}, status=503)
        try:
            blob = await boot.client.fetch_camera_snapshot(entity_id)
        except Exception as e:
            return web.json_response(
                {
                    "camera_entity": entity_id,
                    "success": False,
                    "error_class": type(e).__name__,
                    "error": str(e),
                }
            )
        magic_hex = " ".join(f"{b:02X}" for b in blob[:16])
        if blob.startswith(b"\xff\xd8\xff"):
            looks_like = "jpeg"
        elif blob.startswith(b"\x89PNG"):
            looks_like = "png"
        elif blob.lstrip().startswith(b"<"):
            looks_like = "html"
        else:
            looks_like = "unknown"
        return web.json_response(
            {
                "camera_entity": entity_id,
                "success": True,
                "bytes": len(blob),
                "first_16_hex": magic_hex,
                "looks_like": looks_like,
            }
        )

    async def debug_version(_request: web.Request) -> web.Response:
        """Return package + add-on versions so we can verify what's
        actually running vs what was last committed."""
        from pathlib import Path

        from kukiihome_ha_agent import __version__ as pkg_version

        addon_version = "unknown"
        version_file = Path("/app/.kukiihome_addon_version")
        try:
            # tiny one-line read, OK to do sync inside async — not worth
            # pulling in anyio.path
            addon_version = version_file.read_text().strip()  # noqa: ASYNC240
        except FileNotFoundError:
            pass
        return web.json_response({"package_version": pkg_version, "addon_version": addon_version})

    async def snapshot_for_alert(request: web.Request) -> web.Response:
        """Return the snapshot captured for a specific alert.

        Used by the Recent alerts table to embed per-alert thumbnails —
        each row links to the snapshot of THAT alert, not just the
        latest snapshot for its camera.
        """
        alert_id = request.match_info["alert_id"]
        # Log every fetch so /logs shows browser GETs for snapshots.
        # Useful for diagnosing "thumbnail not visible" complaints —
        # if the browser doesn't even hit this endpoint, we know the
        # issue is HTML/URL/cache, not backend.
        logger.info(
            "snapshot.request",
            alert_id=alert_id,
            user_agent=request.headers.get("User-Agent", "")[:80],
            referer=request.headers.get("Referer", ""),
        )
        alert = alert_log.get(alert_id)
        if alert is None:
            return web.Response(status=404, text=f"no alert {alert_id}")
        path = alert.get("evidence_ref")
        if not path:
            return web.Response(status=404, text=f"alert {alert_id} has no snapshot")
        try:
            return web.FileResponse(path)
        except FileNotFoundError:
            return web.Response(status=404, text=f"snapshot file missing: {path}")

    # ─── discovery overrides (v0.3.11 zero-config UI) ─────────────
    #
    # All four endpoints share the same pattern:
    #   1. Parse the form body.
    #   2. Mutate the persistent overrides file
    #      (/data/kukiihome/adapter_overrides.json).
    #   3. Call _reconcile_discovery to apply the change live.
    #   4. Redirect back to "/" so the user lands on a fresh status
    #      page — POST-Redirect-GET so reload doesn't re-submit.
    #
    # Form parsing uses aiohttp's request.post() (handles
    # application/x-www-form-urlencoded out of the box).

    async def _redirect_home() -> web.Response:
        # The POST URL is always /discovery/<verb>, so "../" resolves
        # to "/" — both under HA Ingress (where the browser sees
        # /api/hassio_ingress/<token>/discovery/<verb>) and under
        # direct port access. Using "./" (the previous version) is
        # wrong: it resolves to /discovery/ which has no GET route
        # and returns 404. See v0.3.12 fix.
        return web.HTTPSeeOther(location="../")

    async def discovery_enable(request: web.Request) -> web.Response:
        form = await request.post()
        device_id = str(form.get("device_id", "")).strip()
        enabled_str = str(form.get("enabled", "")).strip().lower()
        if not device_id:
            return web.Response(status=400, text="missing device_id")
        enabled = enabled_str == "true"
        overrides = load_overrides()
        set_device_override(overrides, device_id, enabled=enabled)
        save_overrides(overrides)
        await _reconcile_discovery(boot)
        return await _redirect_home()

    async def discovery_override(request: web.Request) -> web.Response:
        form = await request.post()
        device_id = str(form.get("device_id", "")).strip()
        if not device_id:
            return web.Response(status=400, text="missing device_id")

        stream_raw = str(form.get("stream", "")).strip()
        # "motion_use_ai" checkbox lets the user clear the motion
        # override even after they previously set one. If checked,
        # we clear; otherwise the submitted motion checkboxes become
        # the new override (empty list = no motion = disabled effect).
        motion_use_ai = form.get("motion_use_ai") == "1"
        motion_list = [m for m in form.getall("motion") if m]
        cooldown_raw = str(form.get("cooldown", "")).strip()

        overrides = load_overrides()
        kwargs: dict[str, Any] = {}
        # Stream: empty string → clear (use AI); else override.
        if stream_raw:
            kwargs["stream_override"] = stream_raw
        else:
            kwargs["clear_stream"] = True
        # Motion: explicit AI checkbox wins; else the checkbox list.
        if motion_use_ai:
            kwargs["clear_motion"] = True
        elif motion_list:
            kwargs["motion_override"] = motion_list
        else:
            kwargs["clear_motion"] = True
        # Cooldown: blank → clear; else override.
        if cooldown_raw:
            try:
                kwargs["cooldown_override"] = float(cooldown_raw)
            except ValueError:
                kwargs["clear_cooldown"] = True
        else:
            kwargs["clear_cooldown"] = True

        set_device_override(overrides, device_id, **kwargs)
        save_overrides(overrides)
        await _reconcile_discovery(boot)
        return await _redirect_home()

    async def discovery_reset(request: web.Request) -> web.Response:
        form = await request.post()
        device_id = str(form.get("device_id", "")).strip()
        if not device_id:
            return web.Response(status=400, text="missing device_id")
        overrides = load_overrides()
        reset_device(overrides, device_id)
        save_overrides(overrides)
        await _reconcile_discovery(boot)
        return await _redirect_home()

    async def discovery_refresh(_request: web.Request) -> web.Response:
        await _reconcile_discovery(boot)
        return await _redirect_home()

    async def notify_services_save(request: web.Request) -> web.Response:
        """Save the user's notify-service checkbox selection (v0.3.13).

        Empty checkbox set = no notifications. Both legal. The UI
        choice always wins over the YAML seed once this file exists.
        """
        form = await request.post()
        # form.getall("service") returns every checked checkbox value;
        # unchecked checkboxes don't appear in the form at all (HTML
        # form semantics), so dedupe via set just in case.
        chosen = sorted({s for s in form.getall("service") if isinstance(s, str)})
        save_notify_services(chosen)
        if boot.notifier is not None:
            boot.notifier.set_services(chosen)
        else:
            logger.warning(
                "notify_services_save.no_notifier",
                hint="HA not connected yet — selection saved to disk, will apply on next boot",
            )
        return await _redirect_home()

    async def notify_test(_request: web.Request) -> web.Response:
        """Send a synthetic notification to verify the wiring (v0.3.14).

        Builds a fake alert (with [TEST] in the headline so it's
        distinguishable on the phone), calls
        :meth:`AlertNotifier.test_send` (which awaits each dispatch
        and returns per-service results), stashes the results on
        boot.last_notify_test for the Notifications card to render.
        """
        ts = datetime.now(UTC)
        result: dict[str, Any] = {
            "ts": ts.isoformat(),
            "services": [],
            "error": None,
        }
        if boot.notifier is None:
            result["error"] = "Notifier not wired yet (HA not connected). Try again in a moment."
        elif not boot.notifier.notify_services:
            result["error"] = "No notify services selected. Check at least one box below first."
        else:
            # Use the most recent real alert's snapshot if available,
            # so the user sees a real image attachment in the test
            # notification. Otherwise no image attached.
            recent = alert_log.recent(1)
            evidence_ref = recent[0].get("evidence_ref") if recent else None
            test_alert = {
                "alert_id": f"test_{uuid.uuid4().hex[:8]}",
                "headline": "[TEST] Kukii-Home notification",
                "camera_id": "test",
                "sensor_classification": "test",
                "recorded_at": ts.isoformat(),
                "evidence_ref": evidence_ref,
                "area": "",
                "source": "notify_test",
            }
            try:
                services_result = await boot.notifier.test_send(test_alert)
                result["services"] = services_result
                result["alert"] = {
                    "headline": test_alert["headline"],
                    "had_image": bool(evidence_ref),
                }
            except Exception as e:
                result["error"] = f"Dispatch raised: {e}"
        boot.last_notify_test = result
        logger.info(
            "notify_test.completed",
            had_error=bool(result["error"]),
            services_tried=len(result.get("services", [])),
        )
        return await _redirect_home()

    async def discovery_switch_toggle(request: web.Request) -> web.Response:
        """Toggle a HA switch entity from a per-device card (v0.3.16).

        Posts ``entity_id`` + ``action`` (``turn_on`` / ``turn_off``).
        Validates: entity must be a switch, action must be one of the
        two service names. After the call, re-runs discovery so the
        updated switch state is reflected on the next render.
        """
        form = await request.post()
        entity_id = str(form.get("entity_id", "")).strip()
        action = str(form.get("action", "")).strip()
        if not entity_id.startswith("switch.") or action not in {"turn_on", "turn_off"}:
            return web.Response(
                status=400,
                text=f"bad params: entity_id={entity_id!r} action={action!r}",
            )
        if boot.client is None:
            return web.Response(status=503, text="HA client not connected")
        try:
            await boot.client.call_service("switch", action, entity_id=entity_id)
            logger.info("discovery.switch_toggled", entity_id=entity_id, action=action)
        except Exception as e:
            logger.warning(
                "discovery.switch_toggle_failed",
                entity_id=entity_id,
                action=action,
                error=str(e),
            )
            # Don't 500 — surface it via the discovery_error banner on
            # the next render.
            boot.discovery_error = f"switch {entity_id} {action} failed: {e}"
        # Re-poll discovery so the user sees the new switch state.
        await _reconcile_discovery(boot)
        return await _redirect_home()

    async def discovery_use_generic_motion(request: web.Request) -> web.Response:
        """Override a device's motion sensors to a single generic
        ``_motion_alarm`` entity (v0.3.16).

        One-click recovery from the Dahua Smart Plan trap (and
        equivalents): AI sensors picked but silent. Persists the
        override + triggers a live reconcile so the camera loop
        re-subscribes to the new sensor without restart.
        """
        form = await request.post()
        device_id = str(form.get("device_id", "")).strip()
        motion_entity = str(form.get("motion_entity", "")).strip()
        if not device_id or not motion_entity:
            return web.Response(status=400, text="missing device_id or motion_entity")
        overrides = load_overrides()
        set_device_override(overrides, device_id, motion_override=[motion_entity])
        save_overrides(overrides)
        await _reconcile_discovery(boot)
        return await _redirect_home()

    async def camera_test_alert(request: web.Request) -> web.Response:
        """Fire a synthetic alert for a specific device (v0.3.14).

        Captures a real snapshot from the device's chosen stream,
        records an alert in the AlertLog (which triggers persistence
        + the notifier), and stashes the outcome on
        boot.last_camera_test so the HA cameras card can render the
        result inline. Verifies the full pipeline without waiting for
        real motion.
        """
        form = await request.post()
        device_id = str(form.get("device_id", "")).strip()
        ts = datetime.now(UTC)
        result: dict[str, Any] = {
            "ts": ts.isoformat(),
            "device_id": device_id,
            "alert_id": None,
            "snapshot_bytes": 0,
            "notify_services": [],
            "error": None,
        }
        if not device_id:
            result["error"] = "missing device_id"
            boot.last_camera_test = result
            return await _redirect_home()

        # Find the spec for this device from the current discovery.
        decision = next(
            (d for d in boot.discovery_decisions if d.device_id == device_id),
            None,
        )
        if decision is None or decision.spec is None:
            result["error"] = (
                f"device {device_id!r} not enabled or not discovered — enable it first, then retry"
            )
            boot.last_camera_test = result
            return await _redirect_home()

        spec = decision.spec
        if boot.client is None:
            result["error"] = "HA client not connected"
            boot.last_camera_test = result
            return await _redirect_home()

        # Capture a real snapshot so the test alert looks like the
        # real thing (including image attachment in the notification).
        snapshot_path: str | None = None
        try:
            blob = await boot.client.fetch_camera_snapshot(spec.camera_entity)
            result["snapshot_bytes"] = len(blob)
            from pathlib import Path

            snap_dir = Path("/data/kukiihome/snapshots")
            # tiny sync mkdir + write; not worth pulling in anyio for
            # a one-off test path. Other camera paths already do this.
            snap_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
            fname = f"test_{device_id}_{ts.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
            snapshot_path = str(snap_dir / fname)
            (snap_dir / fname).write_bytes(blob)
        except Exception as e:
            result["error"] = f"snapshot failed: {e}"
            # Continue anyway so the user gets a notification without
            # image — useful for debugging notify-only issues.

        alert_id = f"test_{device_id}_{uuid.uuid4().hex[:8]}"
        alert = {
            "alert_id": alert_id,
            "headline": f"[TEST] Motion at {spec.friendly_name}",
            "camera_id": device_id,
            "camera_entity": spec.camera_entity,
            "camera_name": spec.friendly_name,
            "sensor_classification": "test",
            "triggering_sensor": "(test trigger)",
            "evidence_ref": snapshot_path,
            "source": "camera_test_alert",
            # We dispatch this alert's notification explicitly via
            # test_send() below (so the UI gets per-service results).
            # Flag it so AlertLog.record's auto-notify path (the
            # notifier's on_alert callback) skips it — otherwise the
            # user gets the notification TWICE per service.
            "suppress_auto_notify": True,
        }
        # AlertLog.record persists the alert + fires its callbacks
        # (EventStore record, enricher, notifier). The notifier callback
        # no-ops here thanks to suppress_auto_notify; we send once,
        # below, via test_send so the user sees per-service results. The
        # record() call still makes the alert tappable (deep-link target)
        # and lets other downstreams (EventStore, enricher) see it.
        alert_log.record(alert)
        result["alert_id"] = alert_id
        if boot.notifier is not None and boot.notifier.notify_services:
            try:
                result["notify_services"] = await boot.notifier.test_send(alert)
            except Exception as e:
                result["error"] = (
                    f"alert recorded but notify dispatch raised: {e}"
                    if result["error"] is None
                    else f"{result['error']}; also notify dispatch raised: {e}"
                )
        boot.last_camera_test = result
        logger.info(
            "camera_test_alert.completed",
            device_id=device_id,
            had_error=bool(result["error"]),
            had_snapshot=bool(snapshot_path),
        )
        return await _redirect_home()

    async def api_get(request: web.Request) -> web.Response:
        body: dict = dict(request.rel_url.query)
        # Always re-bind tools off the current boot state so the API
        # picks up late-arriving HA connections without a restart.
        api._tools = boot.tools
        status, payload = await api.dispatch(method="GET", path=request.path, body=body)
        return web.json_response(payload, status=status)

    async def api_post(request: web.Request) -> web.Response:
        try:
            body = await request.json() if request.body_exists else {}
        except json.JSONDecodeError:
            body = {}
        api._tools = boot.tools
        status, payload = await api.dispatch(method="POST", path=request.path, body=body)
        return web.json_response(payload, status=status)

    # ── Identity Review UI (Build #292) ──────────────────────────────
    # Server-rendered Inbox over the preprocessor's /identity surface. All
    # data lives on the inference box; these handlers proxy it so the page
    # works under HA Ingress auth. Fail-soft: no preprocessor → setup notice.

    async def review_page(request: web.Request) -> web.Response:

        client = boot.preprocessor_client
        configured = client is not None
        tracks: list = []
        subjects: list = []
        if client is not None:
            tracks = await client.list_identity_tracks(limit=200)
            subjects = await client.list_identity_subjects()
        q = request.rel_url.query
        flash = None
        if "labeled" in q:
            flash = f"Labelled “{q.get('labeled')}” — resolved {q.get('n', '0')} appearance(s)."
        elif "rejected" in q:
            flash = "Cleared — track returned to the queue. Re-label it as the right one."
        elif "merged" in q:
            flash = "Merged — the two labels are now one subject."
        elif "err" in q:
            flash = "That action failed (preprocessor unreachable or rejected it)."

        body = render_review_html(
            tracks, subjects, configured=configured, flash=flash,
        )
        return _shell_response(request, "identities", body)

    async def review_thumb(request: web.Request) -> web.Response:
        client = boot.preprocessor_client
        if client is None:
            return web.Response(status=404, text="no preprocessor configured")
        data = await client.fetch_track_thumb(
            request.match_info["event_id"], request.match_info["track_id"]
        )
        if not data:
            return web.Response(status=404, text="no thumbnail")
        return web.Response(body=data, content_type="image/jpeg")

    async def review_label(request: web.Request) -> web.Response:
        from urllib.parse import quote

        client = boot.preprocessor_client
        form = await request.post()
        payload = parse_label_form({k: str(v) for k, v in form.items()})
        # PRG: redirect to /review so the rendered page's relative thumb URLs
        # resolve from /review, not /review/label.
        if client is None or payload is None:
            raise web.HTTPSeeOther(location="../review?err=1")
        result = await client.label_track(payload)
        if not result:
            raise web.HTTPSeeOther(location="../review?err=1")
        loc = f"../review?labeled={quote(payload['name'])}&n={result.get('matched', 0)}"
        raise web.HTTPSeeOther(location=loc)

    async def review_reject(request: web.Request) -> web.Response:
        client = boot.preprocessor_client
        form = await request.post()
        parsed = parse_reject_form({k: str(v) for k, v in form.items()})
        if client is None or parsed is None:
            raise web.HTTPSeeOther(location="../review?err=1")
        await client.reject_track(parsed["event_id"], parsed["track_id"])
        raise web.HTTPSeeOther(location="../review?rejected=1")

    async def review_merge(request: web.Request) -> web.Response:
        client = boot.preprocessor_client
        form = await request.post()
        parsed = parse_merge_form({k: str(v) for k, v in form.items()})
        if client is None or parsed is None:
            raise web.HTTPSeeOther(location="../review?err=1")
        result = await client.merge_subjects(parsed["from_id"], parsed["into_id"])
        raise web.HTTPSeeOther(location="../review?merged=1" if result else "../review?err=1")

    async def review_track(request: web.Request) -> web.Response:
        # Track-detail page (depth-1 path + query so base-href relatives resolve
        # under ingress): animated clip + ranked candidates with one-tap Confirm.

        client = boot.preprocessor_client
        e = request.rel_url.query.get("e", "")
        t = request.rel_url.query.get("t", "")
        if client is None or not e or not t:
            raise web.HTTPSeeOther(location="review")
        detail = await client.get_track_detail(e, t)
        if detail is None:
            raise web.HTTPSeeOther(location="review?err=1")

        body = render_track_detail_html(detail)
        return _shell_response(request, "identities", body)

    async def review_track_clip(request: web.Request) -> web.Response:
        client = boot.preprocessor_client
        e = request.rel_url.query.get("e", "")
        t = request.rel_url.query.get("t", "")
        if client is None or not e or not t:
            return web.Response(status=404, text="no track")
        data = await client.fetch_track_clip(e, t)
        if not data:
            return web.Response(status=404, text="no clip")
        return web.Response(body=data, content_type="image/gif")

    # ── v2 product UI (Web UI skeleton) ──────────────────────────────
    # Lives at top-level paths (/home, /activity, /areas, /intent, /policies,
    # /cameras, /diagnostics). Home page is fleshed out using real alert_log
    # + identity-tracks data; the rest are credible 'Coming soon' skeletons
    # tied to ratified sections of planning/web-ui-design.md. The legacy /
    # status page stays untouched during the transition — see /diagnostics
    # for the link back.

    async def v2_home(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.web_ui.home import render_home_page

        # Pull real data — degrade gracefully on any failure.
        recent = alert_log.recent(50)
        unresolved = 0
        prep_ok: bool | None = None
        if boot.preprocessor_client is not None:
            try:
                tracks = await boot.preprocessor_client.list_identity_tracks(
                    status="unresolved", limit=200,
                )
                unresolved = len(tracks)
            except Exception as e:
                logger.info("v2.home.unresolved_failed", error=str(e))
            try:
                prep_ok = await boot.preprocessor_client.healthz()
            except Exception as e:
                logger.info("v2.home.healthz_failed", error=str(e))
                prep_ok = False
        cameras = list(boot.cameras) if getattr(boot, "cameras", None) else []
        ha_connected = bool(getattr(boot, "ha_client", None))
        ha_entities = 0
        try:
            snap = await boot.tools.get_snapshot() if getattr(boot, "tools", None) else []
            ha_entities = len(list(snap))
        except Exception as e:
            logger.info("v2.home.snapshot_failed", error=str(e))
        import time as _time
        content = render_home_page(
            alerts_recent=recent,
            unresolved_tracks=unresolved,
            cameras_total=len(cameras),
            cameras_active=len(cameras),  # no per-cam health yet; same count
            preprocessor_ok=prep_ok,
            ha_connected=ha_connected,
            ha_entities=ha_entities,
            now_ts=_time.time(),
        )
        return _shell_response(request, "home", content)

    def _intent_known_subjects(_boot: BootState) -> list[tuple[str, str]]:
        """Subject dropdown source for shortcut rules. Pulls enrolled actors
        from the preprocessor's identity store when reachable; falls back to
        a small set of kind-keyed shortcuts so the form is useful even when
        the identity store is empty (greenfield install). Each tuple is
        ``(id_for_form, display_label)``."""
        defaults = [
            ("person", "Any person (kind)"),
            ("dog", "Any dog (kind)"),
            ("cat", "Any cat (kind)"),
            ("vehicle", "Any vehicle (kind)"),
        ]
        # Enrolled actors from the preprocessor identity store would slot
        # in here once we add a sync read on the BootState; for now the
        # kind-keyed shortcuts cover the MVP path (Task 9 §Done when).
        return defaults

    def _intent_known_cameras(boot: BootState) -> list[tuple[str, str]]:
        """Camera dropdown source for rule scopes. Uses the camera registry's
        live entries, falling back to ``camera_id`` when there's no friendly
        name to display."""
        from kukiihome_ha_agent.web_ui.shell import camera_display_name
        out: list[tuple[str, str]] = []
        try:
            for loop in (boot.ha_camera_loops or []):
                cid = getattr(loop, "camera_id", None) or getattr(loop, "id", "")
                friendly = getattr(loop, "friendly_name", "") or cid
                if cid:
                    out.append((str(cid), camera_display_name(str(friendly)) or str(cid)))
        except Exception as e:
            # registry shape evolves; this read is best-effort
            logger.debug("intent.known_cameras_failed", error=str(e))
        return out

    # _v2_mock_response was a helper for the placeholder pages in Iter 2;
    # every v2_* nav target now has a real renderer so the helper has no
    # remaining callers. Removed in the user-review fixup pass.

    async def v2_activity(request: web.Request) -> web.Response:
        # Task 7 / Part IV: real activity page with filter chips + pagination.
        import time as _time

        from kukiihome_ha_agent.web_ui.activity import (
            parse_filters,
            render_activity_page,
        )

        # Pull the full alert log (capped at 500 — alert_log itself trims; this
        # is the page-side over-fetch ceiling for the filter+page pipeline).
        all_alerts = alert_log.recent(500)
        filters = parse_filters(dict(request.rel_url.query))
        content = render_activity_page(
            alerts_all=all_alerts, now_ts=_time.time(), **filters,
        )
        return _shell_response(request, "activity", content)

    async def v2_areas(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.web_ui.areas import render_areas_list

        if getattr(boot, "area_store", None) is None:
            body = "<h1>Areas</h1><div class='empty'>Area store unavailable.</div>"
        else:
            body = render_areas_list(boot.area_store.all_areas())
        return _shell_response(request, "areas", body)

    async def v2_area_new(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.web_ui.areas import render_area_form

        body = render_area_form(
            None, available_cameras=_intent_known_cameras(boot),
        )
        return _shell_response(request, "areas", body)

    async def v2_area_edit(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.web_ui.areas import render_area_form

        if getattr(boot, "area_store", None) is None:
            return web.HTTPServiceUnavailable(text="area store unavailable")
        area_id = request.match_info["area_id"]
        area = boot.area_store.get(area_id)
        if area is None:
            return web.HTTPNotFound(text="area not found")
        body = render_area_form(
            area, available_cameras=_intent_known_cameras(boot),
        )
        return _shell_response(request, "areas", body)

    async def v2_area_save(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.area_store import Area
        from kukiihome_ha_agent.web_ui.areas import parse_area_form

        if getattr(boot, "area_store", None) is None:
            return web.HTTPServiceUnavailable(text="area store unavailable")
        try:
            patch = parse_area_form(dict(await request.post()))
        except ValueError as e:
            return web.HTTPBadRequest(text=str(e))
        area_id = request.match_info.get("area_id") or ""
        if area_id:
            boot.area_store.update(area_id, **patch)
        else:
            cameras = patch.pop("cameras", [])
            new_area = Area(id="", cameras=cameras, **patch)
            boot.area_store.create(new_area)
        raise web.HTTPSeeOther(location="areas")

    async def v2_area_delete(request: web.Request) -> web.Response:
        if getattr(boot, "area_store", None) is None:
            return web.HTTPServiceUnavailable(text="area store unavailable")
        boot.area_store.soft_delete(request.match_info["area_id"])
        raise web.HTTPSeeOther(location="areas")

    async def v2_memory(request: web.Request) -> web.Response:
        """Iter 3 / Part IX §28: unified guidance browse. Aggregates rules
        + preferences + policies + area postures, classifies each entry
        to one or more contexts, renders grouped. Optionally embeds the
        conversational drawer when ?drawer=1 is present."""
        import time as _time

        from kukiihome_ha_agent.web_ui.memory import render_memory_page
        from kukiihome_ha_agent.web_ui.memory_data import (
            build_guidance_entries,
        )

        cut = request.rel_url.query.get("cut") or "by_context"
        if cut not in ("by_context", "by_type"):
            cut = "by_context"

        rules = (
            boot.rules_store.all_rules()
            if getattr(boot, "rules_store", None) else []
        )
        prefs = (
            boot.preferences_store.get()
            if getattr(boot, "preferences_store", None) else None
        )
        # Surface BOTH active dismissals AND TIs in /memory. Stale-revoked
        # entries hide naturally because all_policies filters them by default.
        pols: list = []
        if getattr(boot, "policy_store", None):
            pols.extend(boot.policy_store.all_policies(kind="dismissal"))
            pols.extend(boot.policy_store.all_policies(kind="transient_intent"))
        areas = (
            boot.area_store.all_areas()
            if getattr(boot, "area_store", None) else []
        )
        entries = build_guidance_entries(
            rules=rules, preferences=prefs, policies=pols, areas=areas,
            provenance_store=getattr(boot, "provenance_store", None),
        )

        # Iter 3 / Part X §39: drift detection — surface guidance entries
        # that haven't earned their placement. Cheap inline call; a
        # nightly background sweep lands as a follow-up if the cost grows.
        from kukiihome_ha_agent.drift_detector import detect_all_drift
        drift = detect_all_drift(
            rules=rules, policies=pols, now_ts=_time.time(),
        )
        # Iter 3 (Part X §35): degraded-mode banner when LLM is down.
        llm_health = (
            boot.llm_health.status
            if getattr(boot, "llm_health", None) else None
        )
        body = render_memory_page(
            entries, cut=cut, now_ts=_time.time(),
            drift_suggestions=drift,
            llm_health=llm_health,
        )
        return _shell_response(request, "memory", body)

    # ── Drawer helpers (Iter 3 / Part X §34) ──────────────────────

    def _user_id_for(request: web.Request) -> str:
        """Pull the HA user id from ingress headers; fall back to a
        constant per-instance id when running outside ingress (tests,
        direct port-8765 access)."""
        return (
            request.headers.get("X-Remote-User-Id")
            or request.headers.get("X-Hass-User-Id")
            or "default"
        )

    def _build_drawer_html(
        request: web.Request, *,
        alert_context: str = "", page_context: str = "",
    ) -> str:
        """Compose the drawer aside for a route handler. Re-attaches the
        active session (opening a fresh one if idle) and renders the
        full transcript. Returns "" if the provenance store is not
        wired (the shell will then render without the drawer slot)."""
        import time as _time

        from kukiihome_ha_agent.web_ui.drawer import render_drawer

        prov = getattr(boot, "provenance_store", None)
        if prov is None:
            return ""
        sess = prov.get_or_open_session(
            _user_id_for(request),
            page_context=page_context,
            alert_context=alert_context,
            now_ts=_time.time(),
        )
        turns = prov.turns_for_session(sess.id)
        return render_drawer(
            session=sess, turns=turns,
            alert_context=alert_context,
            request_path=request.path,
            now_ts=_time.time(),
        )

    def _shell_response(
        request: web.Request, active: str, body_html: str, *,
        flash: str | None = None,
        alert_context: str = "",
    ) -> web.Response:
        """Universal v2 page response. Wraps body_html in the shell with
        - correct depth-aware <base href> from request.path
        - drawer aside auto-built when ?drawer=1 is in the query
        - active nav highlight + version string + flash banner

        Page-context awareness (Part X §34): every page can host the
        drawer, and the drawer's page_context is the request path so
        the dispatcher knows where the user was when they opened it.
        ``alert_context`` is an explicit override the alert_page route
        uses to pin the drawer to the alert id (which lives in the
        path, not the query — the route knows it, the generic helper
        can't derive it from the URL alone).
        """
        from kukiihome_ha_agent import __version__
        from kukiihome_ha_agent.web_ui.drawer import is_drawer_requested
        from kukiihome_ha_agent.web_ui.shell import render_shell

        drawer_html = ""
        if is_drawer_requested(dict(request.rel_url.query)):
            drawer_html = _build_drawer_html(
                request,
                page_context=request.path,
                alert_context=(
                    alert_context
                    or request.rel_url.query.get("alert", "")
                ),
            )
        return web.Response(
            text=render_shell(
                active, body_html, version=__version__,
                drawer_html=drawer_html, flash=flash,
                request_path=request.path,
            ),
            content_type="text/html",
        )

    async def api_drawer_turn(request: web.Request) -> web.Response:
        """POST /api/drawer/turn — user-submitted utterance. Appends the
        user's turn, calls the dispatcher, appends the system's proposal
        turn, then redirects back to /memory?drawer=1."""
        import time as _time

        from kukiihome_ha_agent.dispatcher import (
            HeuristicDispatcherProvider,
            context_from_boot,
        )

        prov = getattr(boot, "provenance_store", None)
        if prov is None:
            return web.HTTPServiceUnavailable(text="provenance store unavailable")

        form = await request.post()
        utterance = (form.get("utterance") or "").strip()
        alert_context = (form.get("alert_context") or "").strip()
        if not utterance:
            raise web.HTTPSeeOther(location="memory?drawer=1")

        sess = prov.get_or_open_session(
            _user_id_for(request),
            page_context="memory",
            alert_context=alert_context,
            now_ts=_time.time(),
        )
        prov.append_turn(
            sess.id, role="user", utterance=utterance, now_ts=_time.time(),
        )

        # Iter 3 (Part X §35): use boot.dispatcher — Composite (LLM →
        # heuristic fallback) when LLM is configured, raw Heuristic when
        # not. Composite reports health via boot.llm_health for the
        # /memory degraded-mode banner.
        dispatcher = getattr(boot, "dispatcher", None) or HeuristicDispatcherProvider()
        ctx = context_from_boot(
            boot, session_id=sess.id,
        )
        ctx.page_context = "memory"
        ctx.alert_context = alert_context
        if hasattr(dispatcher, "propose_async"):
            proposal = await dispatcher.propose_async(utterance, ctx=ctx)
        else:
            proposal = dispatcher.propose(utterance, ctx=ctx)
        prov.append_turn(
            sess.id, role="system",
            utterance=proposal.reasoning,
            proposal_json=proposal.to_json(),
            now_ts=_time.time(),
        )
        raise web.HTTPSeeOther(location="memory?drawer=1")

    async def api_drawer_confirm(request: web.Request) -> web.Response:
        """POST /api/drawer/confirm — user confirmed a proposal. Routes
        the placement through commit_guidance and marks the proposal turn
        with the new guidance entry id."""
        import time as _time

        from kukiihome_ha_agent.commit_guidance import (
            GuidanceStores,
            commit_guidance,
        )
        from kukiihome_ha_agent.provenance_store import PlacementProposal

        prov = getattr(boot, "provenance_store", None)
        if prov is None:
            return web.HTTPServiceUnavailable(text="provenance store unavailable")

        form = await request.post()
        turn_id = (form.get("turn_id") or "").strip()
        session_id = (form.get("session_id") or "").strip()
        if not turn_id or not session_id:
            return web.HTTPBadRequest(text="turn_id + session_id required")

        proposal_turn = prov.get_turn(turn_id)
        if proposal_turn is None or not proposal_turn.proposal_json:
            return web.HTTPNotFound(text="proposal turn not found")
        try:
            proposal = PlacementProposal.from_json(proposal_turn.proposal_json)
        except Exception as e:
            return web.HTTPBadRequest(text=f"malformed proposal: {e}")

        # Find the user turn that generated this proposal (most recent
        # user turn before the proposal turn in the same session).
        all_turns = prov.turns_for_session(session_id)
        prior_user_utterance = ""
        for t in all_turns:
            if t.turn_index >= proposal_turn.turn_index:
                break
            if t.role == "user":
                prior_user_utterance = t.utterance

        stores = GuidanceStores(
            rules=getattr(boot, "rules_store", None),
            preferences=getattr(boot, "preferences_store", None),
            policies=getattr(boot, "policy_store", None),
            actions=getattr(boot, "action_store", None),
            areas=getattr(boot, "area_store", None),
            provenance=prov,
        )
        try:
            gid = commit_guidance(
                proposal, stores=stores, origin="conversation",
                transcript_id=turn_id,
                user_utterance=prior_user_utterance,
                now_ts=_time.time(),
            )
        except Exception as e:
            return web.HTTPBadRequest(text=f"commit failed: {e}")

        # Append a committed-marker turn so the drawer thread shows the result.
        prov.append_turn(
            session_id, role="system",
            utterance=f"committed as {gid}",
            committed_to=gid,
            now_ts=_time.time(),
        )
        raise web.HTTPSeeOther(location="memory?drawer=1")

    async def v2_intent_redirect(request: web.Request) -> web.Response:
        """301 redirect from old /intent → /memory?cut=by_type. The Iter 2
        /intent and /policies pages collapse into one under Part IX §28."""
        qs = request.rel_url.query_string
        target = "memory?cut=by_type"
        if qs:
            target = f"{target}&{qs}"
        raise web.HTTPMovedPermanently(location=target)

    async def v2_policies_redirect(request: web.Request) -> web.Response:
        qs = request.rel_url.query_string
        target = "memory?cut=by_type"
        if qs:
            target = f"{target}&{qs}"
        raise web.HTTPMovedPermanently(location=target)

    async def v2_intent(request: web.Request) -> web.Response:
        # Task 9: real page from RulesStore. Falls back to a brief "rules
        # storage unavailable" notice when the store isn't wired (older
        # boot path / tests).
        import time as _time

        from kukiihome_ha_agent.web_ui.intent import render_intent_page

        if boot.rules_store is None:
            body = (
                "<h1>Intent</h1>"
                "<div class='empty'>Rules storage isn't wired in this build.</div>"
            )
        else:
            rules = boot.rules_store.all_rules()
            prefs = (
                boot.preferences_store.get()
                if getattr(boot, "preferences_store", None) else None
            )
            body = render_intent_page(
                rules, now_ts=_time.time(), preferences=prefs,
            )
        return _shell_response(request, "intent", body)

    async def v2_intent_rule_new(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.web_ui.intent import render_rule_form

        body = render_rule_form(
            None,
            available_subjects=_intent_known_subjects(boot),
            available_cameras=_intent_known_cameras(boot),
            available_areas=[],
        )
        return _shell_response(request, "intent", body)

    async def v2_intent_rule_edit(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.web_ui.intent import render_rule_form

        rule_id = request.match_info["rule_id"]
        if boot.rules_store is None:
            return web.HTTPNotFound(text="rules store unavailable")
        rule = boot.rules_store.get(rule_id)
        if rule is None:
            return web.HTTPNotFound(text="rule not found")
        body = render_rule_form(
            rule,
            available_subjects=_intent_known_subjects(boot),
            available_cameras=_intent_known_cameras(boot),
            available_areas=[],
        )
        return _shell_response(request, "intent", body)

    async def v2_intent_rule_save(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.rules_store import Rule, RuleScope
        from kukiihome_ha_agent.web_ui.intent import parse_rule_form

        if boot.rules_store is None:
            return web.HTTPServiceUnavailable(text="rules store unavailable")

        form = await request.post()
        try:
            patch = parse_rule_form(dict(form))
        except ValueError as e:
            return web.HTTPBadRequest(text=str(e))

        rule_id = request.match_info.get("rule_id") or ""
        if rule_id:
            boot.rules_store.update(rule_id, **patch)
        else:
            boot.rules_store.create(Rule(
                id="", name=patch["name"], mode=patch["mode"],
                intent_text=patch.get("intent_text", ""),
                scope=patch.get("scope") or RuleScope(),
                shortcut_subject=patch.get("shortcut_subject"),
                severity_static=patch.get("severity_static"),
            ))
        raise web.HTTPSeeOther(location="intent")

    async def v2_intent_rule_enable(request: web.Request) -> web.Response:
        if boot.rules_store is None:
            return web.HTTPServiceUnavailable(text="rules store unavailable")
        rule_id = request.match_info["rule_id"]
        form = await request.post()
        enabled = (form.get("enabled") or "1") == "1"
        boot.rules_store.set_enabled(rule_id, enabled)
        raise web.HTTPSeeOther(location="intent")

    async def v2_intent_rule_delete(request: web.Request) -> web.Response:
        if boot.rules_store is None:
            return web.HTTPServiceUnavailable(text="rules store unavailable")
        rule_id = request.match_info["rule_id"]
        boot.rules_store.soft_delete(rule_id)
        raise web.HTTPSeeOther(location="intent")

    async def v2_intent_preferences_save(request: web.Request) -> web.Response:
        """Iter 2.A: save Preferences section of /intent. POST handler that
        validates incoming fields, persists, then 303-redirects back to /intent."""
        if getattr(boot, "preferences_store", None) is None:
            return web.HTTPServiceUnavailable(text="preferences store unavailable")
        form = await request.post()
        vigilance = (form.get("vigilance") or "normal").strip()
        if vigilance not in ("low", "normal", "high"):
            vigilance = "normal"
        what = (form.get("what_i_care_about") or "").strip()
        boot.preferences_store.update(
            vigilance=vigilance, what_i_care_about=what,
        )
        raise web.HTTPSeeOther(location="intent")

    async def v2_intent_rule_matches(request: web.Request) -> web.Response:
        # Lightweight matches list — table of recent evaluations. Full Part
        # VI matches UI lands when the VLM-side recording loop does.
        from kukiihome_ha_agent.web_ui.shell import _e

        if boot.rules_store is None:
            return web.HTTPServiceUnavailable(text="rules store unavailable")
        rule_id = request.match_info["rule_id"]
        rule = boot.rules_store.get(rule_id)
        if rule is None:
            return web.HTTPNotFound(text="rule not found")
        matches = boot.rules_store.matches_for_rule(rule_id, limit=50)
        rows_html = "".join(
            "<tr>"
            f"<td>{_e(m.matched_at)}</td>"
            f"<td>{_e(m.severity or '—')}</td>"
            f"<td>{_e(round(m.confidence or 0.0, 2))}</td>"
            f"<td>{_e(m.reasoning or '')}</td>"
            f"<td>{'matched' if m.matched else 'non-match'}</td>"
            "</tr>"
            for m in matches
        ) or "<tr><td colspan='5' class='empty'>No matches yet.</td></tr>"
        body = (
            f"<h1>Matches · {_e(rule.name)}</h1>"
            "<div class='sub'>Most recent rule evaluations — match and "
            "non-match, newest first.</div>"
            "<table class='matches-table'>"
            "<thead><tr><th>When</th><th>Severity</th><th>Conf</th>"
            "<th>Reasoning</th><th>Status</th></tr></thead>"
            f"<tbody>{rows_html}</tbody></table>"
            "<a class='btn' href='../../../intent'>← Back to rules</a>"
        )
        return _shell_response(request, "intent", body)

    async def v2_policies(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.web_ui.policies import render_policies_page

        if getattr(boot, "policy_store", None) is None:
            body = "<h1>Policies</h1><div class='empty'>Policy store unavailable.</div>"
        else:
            dismissals = boot.policy_store.all_policies(kind="dismissal")
            transients = boot.policy_store.all_policies(kind="transient_intent")
            body = render_policies_page(
                dismissals=dismissals, transient_intents=transients,
            )
        return _shell_response(request, "policies", body)

    async def v2_policy_revoke(request: web.Request) -> web.Response:
        if getattr(boot, "policy_store", None) is None:
            return web.HTTPServiceUnavailable(text="policy store unavailable")
        boot.policy_store.revoke(request.match_info["policy_id"])
        raise web.HTTPSeeOther(location="policies")

    async def v2_cameras(request: web.Request) -> web.Response:
        # Iter 2.B: live list page reading the registry + alert log.
        import time as _time

        from kukiihome_ha_agent.web_ui.camera_data import (
            build_camera_summaries,
        )
        from kukiihome_ha_agent.web_ui.cameras import render_cameras_list

        statuses = (
            list(boot.camera_registry.all())
            if getattr(boot, "camera_registry", None) else []
        )
        ha_loops = list(getattr(boot, "ha_camera_loops", []) or [])
        summaries = build_camera_summaries(
            registry_statuses=statuses, ha_loops=ha_loops,
            alerts=alert_log.recent(500), now_ts=_time.time(),
        )
        body = render_cameras_list(summaries)
        return _shell_response(request, "cameras", body)

    async def v2_camera_detail(request: web.Request) -> web.Response:
        import time as _time

        from kukiihome_ha_agent.web_ui.camera_data import build_camera_detail
        from kukiihome_ha_agent.web_ui.cameras import render_camera_detail

        camera_id = request.match_info["camera_id"]
        statuses = (
            list(boot.camera_registry.all())
            if getattr(boot, "camera_registry", None) else []
        )
        ha_loops = list(getattr(boot, "ha_camera_loops", []) or [])
        perc = (
            boot.action_store.perception_for(camera_id)
            if getattr(boot, "action_store", None) else []
        )
        prot = (
            boot.action_store.protective_for(camera_id)
            if getattr(boot, "action_store", None) else []
        )
        vm = build_camera_detail(
            camera_id=camera_id, registry_statuses=statuses,
            ha_loops=ha_loops, alerts=alert_log.recent(500),
            perception_entries=perc, protective_entries=prot,
            now_ts=_time.time(),
        )
        if vm is None:
            return web.HTTPNotFound(text=f"camera {camera_id!r} not found")
        body = render_camera_detail(vm)
        return _shell_response(request, "cameras", body)

    async def v2_cam_wl_new_perc(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.web_ui.cameras import render_perception_form

        camera_id = request.match_info["camera_id"]
        body = render_perception_form(camera_id)
        return _shell_response(request, "cameras", body)

    async def v2_cam_wl_new_prot(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.web_ui.cameras import render_protective_form

        camera_id = request.match_info["camera_id"]
        body = render_protective_form(camera_id)
        return _shell_response(request, "cameras", body)

    async def v2_cam_wl_save_perc(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.action_store import PerceptionEntry
        from kukiihome_ha_agent.web_ui.cameras import parse_perception_form

        if getattr(boot, "action_store", None) is None:
            return web.HTTPServiceUnavailable(text="action store unavailable")
        camera_id = request.match_info["camera_id"]
        try:
            patch = parse_perception_form(dict(await request.post()))
        except ValueError as e:
            return web.HTTPBadRequest(text=str(e))
        boot.action_store.upsert_perception(PerceptionEntry(
            camera_id=camera_id, **patch,
        ))
        raise web.HTTPSeeOther(location=f"cameras/{camera_id}")

    async def v2_cam_wl_save_prot(request: web.Request) -> web.Response:
        from kukiihome_ha_agent.action_store import ProtectiveEntry
        from kukiihome_ha_agent.web_ui.cameras import parse_protective_form

        if getattr(boot, "action_store", None) is None:
            return web.HTTPServiceUnavailable(text="action store unavailable")
        camera_id = request.match_info["camera_id"]
        try:
            patch = parse_protective_form(dict(await request.post()))
        except ValueError as e:
            return web.HTTPBadRequest(text=str(e))
        boot.action_store.upsert_protective(ProtectiveEntry(
            camera_id=camera_id, **patch,
        ))
        raise web.HTTPSeeOther(location=f"cameras/{camera_id}")

    async def v2_cam_wl_del_perc(request: web.Request) -> web.Response:
        if getattr(boot, "action_store", None) is None:
            return web.HTTPServiceUnavailable(text="action store unavailable")
        camera_id = request.match_info["camera_id"]
        form = await request.post()
        boot.action_store.delete_perception(
            camera_id,
            (form.get("target_kind") or "").strip(),
            (form.get("target") or "").strip(),
        )
        raise web.HTTPSeeOther(location=f"cameras/{camera_id}")

    async def v2_cam_wl_del_prot(request: web.Request) -> web.Response:
        if getattr(boot, "action_store", None) is None:
            return web.HTTPServiceUnavailable(text="action store unavailable")
        camera_id = request.match_info["camera_id"]
        form = await request.post()
        boot.action_store.delete_protective(
            camera_id,
            (form.get("action_class") or "").strip(),
            (form.get("service") or "").strip(),
            (form.get("target") or "").strip(),
        )
        raise web.HTTPSeeOther(location=f"cameras/{camera_id}")

    async def v2_identities(request: web.Request) -> web.Response:
        """Iter 3 / Part IX §29: Enrolled identities list. Reads
        /identity/subjects from the preprocessor and renders the tile
        grid. Falls back to an empty list if the preprocessor is down."""
        from kukiihome_ha_agent.web_ui.identities import (
            build_identity_subjects,
            render_identities_list,
        )

        subjects = []
        unresolved_count = 0
        if boot.preprocessor_client is not None:
            try:
                payload = await boot.preprocessor_client.list_identity_subjects()
                subjects = build_identity_subjects(payload)
            except Exception as e:
                logger.warning("v2.identities.subjects_fetch_failed", error=str(e))
            try:
                tracks = await boot.preprocessor_client.list_identity_tracks(
                    only_unresolved=True,
                )
                unresolved_count = len(tracks or [])
            except Exception as e:
                logger.debug("v2.identities.tracks_fetch_failed", error=str(e))
        # User-review fixup #4: when nothing is enrolled yet but tracks
        # are awaiting review, route the user to where the content is.
        # The new Enrolled tab is the right primary surface eventually,
        # but at first run "Identities" should show pending-review work,
        # not an empty pane.
        if not subjects and unresolved_count > 0:
            raise web.HTTPSeeOther(location="review")
        body = render_identities_list(
            subjects, unresolved_count=unresolved_count, tab="enrolled",
        )
        return _shell_response(request, "identities", body)

    async def v2_identity_detail(request: web.Request) -> web.Response:
        """Iter 3 / Part IX §29: per-identity detail page. Pulls the
        subject record + matching guidance entries from /memory."""
        from kukiihome_ha_agent.web_ui.identities import (
            IdentityDetailViewModel,
            build_identity_subjects,
            filter_guidance_for_subject,
            render_identity_detail,
        )
        from kukiihome_ha_agent.web_ui.memory_data import (
            build_guidance_entries,
        )

        subject_id = request.match_info["subject_id"]
        subject = None
        if boot.preprocessor_client is not None:
            try:
                payload = await boot.preprocessor_client.list_identity_subjects()
                subs = build_identity_subjects(payload)
                subject = next(
                    (s for s in subs if s.subject_id == subject_id), None,
                )
            except Exception as e:
                logger.warning("v2.identity_detail.fetch_failed", error=str(e))
        if subject is None:
            return web.HTTPNotFound(text=f"subject {subject_id!r} not found")

        # Linked guidance — pull all entries + filter by subject
        rules = (
            boot.rules_store.all_rules()
            if getattr(boot, "rules_store", None) else []
        )
        prefs = (
            boot.preferences_store.get()
            if getattr(boot, "preferences_store", None) else None
        )
        pols: list = []
        if getattr(boot, "policy_store", None):
            pols.extend(boot.policy_store.all_policies(kind="dismissal"))
            pols.extend(boot.policy_store.all_policies(kind="transient_intent"))
        areas = (
            boot.area_store.all_areas()
            if getattr(boot, "area_store", None) else []
        )
        entries = build_guidance_entries(
            rules=rules, preferences=prefs, policies=pols, areas=areas,
            provenance_store=getattr(boot, "provenance_store", None),
        )
        linked = filter_guidance_for_subject(entries, subject=subject)

        vm = IdentityDetailViewModel(subject=subject, linked_guidance=linked)
        body = render_identity_detail(vm)
        return _shell_response(request, "identities", body)

    async def v2_system(request: web.Request) -> web.Response:
        """Iter 3 / Part IX §30: /system page — storage usage + retention
        policy + privacy operations + admin audit log."""
        import time as _time

        from kukiihome_ha_agent.web_ui.camera_data import (
            build_camera_summaries,
        )
        from kukiihome_ha_agent.web_ui.system import render_system_page
        from kukiihome_ha_agent.web_ui.system_data import build_system_vm

        ret = getattr(boot, "retention_store", None)
        policy = ret.get_policy() if ret else None
        audit_log = ret.recent_audits(limit=50) if ret else []

        # Camera (id, friendly_name) pairs for the purge form dropdown.
        statuses = (
            list(boot.camera_registry.all())
            if getattr(boot, "camera_registry", None) else []
        )
        ha_loops = list(getattr(boot, "ha_camera_loops", []) or [])
        summaries = build_camera_summaries(
            registry_statuses=statuses, ha_loops=ha_loops,
            alerts=[], now_ts=_time.time(),
        )
        cameras = [(c.camera_id, c.name) for c in summaries]

        vm = build_system_vm(
            data_root="/data/kukiihome",
            policy=policy, audit_log=audit_log,
            cameras=cameras, now_ts=_time.time(),
        )
        return _shell_response(request, "system", render_system_page(vm))

    async def v2_system_retention(request: web.Request) -> web.Response:
        """POST /system/retention — update the retention policy. All fields
        optional; bad inputs fall back to the existing values."""
        ret = getattr(boot, "retention_store", None)
        if ret is None:
            return web.HTTPServiceUnavailable(text="retention store unavailable")
        form = await request.post()

        def _as_int(k):
            v = form.get(k)
            try:
                return int(v) if v else None
            except (TypeError, ValueError):
                return None

        ret.update_policy(
            events_days=_as_int("events_days"),
            events_max_gb=_as_int("events_max_gb"),
            frames_days=_as_int("frames_days"),
            audit_days=_as_int("audit_days"),
        )
        import time as _time

        from kukiihome_ha_agent.retention_store import AdminAudit
        ret.record_audit(AdminAudit(
            id=None, ts=_time.time(),
            actor=_user_id_for(request),
            operation="retention.policy.updated",
            scope="global", notes="via /system page",
        ))
        raise web.HTTPSeeOther(location="system")

    async def v2_system_erase_last_hour(request: web.Request) -> web.Response:
        """POST /system/erase-last-hour — panic button. Walks the events
        directory and removes any event-dirs with mtime in the last 60
        minutes. Records the operation in admin_audit before deletion."""
        import shutil
        import time as _time
        from pathlib import Path

        from kukiihome_ha_agent.retention_store import AdminAudit

        ret = getattr(boot, "retention_store", None)
        if ret is None:
            return web.HTTPServiceUnavailable(text="retention store unavailable")

        events_root = Path("/data/kukiihome/events")
        cutoff = _time.time() - 3600.0
        # Synchronous filesystem walk runs in a thread to keep the event
        # loop responsive — this is a rarely-pressed panic button so the
        # overhead is fine.
        bytes_removed, rows_removed = await asyncio.to_thread(
            _erase_recent_event_dirs, events_root, cutoff, shutil, logger,
        )

        ret.record_audit(AdminAudit(
            id=None, ts=_time.time(),
            actor=_user_id_for(request),
            operation="erase_last_hour",
            scope="all cameras / last 60 min",
            bytes_removed=bytes_removed, rows_removed=rows_removed,
            notes=f"{rows_removed} event dirs removed",
        ))
        raise web.HTTPSeeOther(location="system")

    async def v2_system_purge(request: web.Request) -> web.Response:
        """POST /system/purge — surgical bulk delete by camera + date.
        For v1 this records the audit row + 303s back; the actual
        date-range parse + event filtering lands when EventStore exposes
        a query-by-camera + recorded-at-between primitive."""
        import time as _time

        from kukiihome_ha_agent.retention_store import AdminAudit
        ret = getattr(boot, "retention_store", None)
        if ret is None:
            return web.HTTPServiceUnavailable(text="retention store unavailable")
        form = await request.post()
        camera_id = (form.get("camera_id") or "").strip()
        start_date = (form.get("start_date") or "").strip()
        end_date = (form.get("end_date") or "").strip()
        ret.record_audit(AdminAudit(
            id=None, ts=_time.time(),
            actor=_user_id_for(request),
            operation="purge.scheduled",
            scope=f"camera={camera_id} from {start_date} to {end_date}",
            notes="purge worker not yet implemented; audit only",
        ))
        raise web.HTTPSeeOther(location="system")

    async def v2_diagnostics(request: web.Request) -> web.Response:
        import os
        import time as _time

        from kukiihome_ha_agent import __version__
        from kukiihome_ha_agent.web_ui.diagnostics import (
            build_diagnostics_vm,
            render_diagnostics_page,
        )

        prep_ok: bool | None = None
        prep_url = os.environ.get("KUKIIHOME_PREPROCESSOR_URL") or None
        if boot.preprocessor_client is not None:
            try:
                prep_ok = await boot.preprocessor_client.healthz()
            except Exception:
                prep_ok = False
        ha_connected = bool(getattr(boot, "client", None)) and (
            getattr(boot.client, "is_connected", False)
        )
        ha_entities = 0
        try:
            snap = await boot.tools.get_snapshot() if boot.tools else []
            ha_entities = len(list(snap))
        except Exception as e:
            logger.debug("v2.diagnostics.snapshot_failed", error=str(e))

        vm = build_diagnostics_vm(
            version=__version__,
            preprocessor_ok=prep_ok, preprocessor_url=prep_url,
            ha_connected=ha_connected, ha_entities=ha_entities,
            rules_store=getattr(boot, "rules_store", None),
            action_store=getattr(boot, "action_store", None),
            area_store=getattr(boot, "area_store", None),
            policy_store=getattr(boot, "policy_store", None),
            registry_statuses=(
                list(boot.camera_registry.all())
                if getattr(boot, "camera_registry", None) else []
            ),
            ha_loops=list(getattr(boot, "ha_camera_loops", []) or []),
            alerts=alert_log.recent(500),
            now_ts=_time.time(),
        )
        return _shell_response(request, "diagnostics", render_diagnostics_page(vm))

    app = web.Application()
    app.router.add_get("/", status_page)
    app.router.add_get("/review", review_page)
    app.router.add_get("/review/thumb/{event_id}/{track_id}.jpg", review_thumb)
    app.router.add_get("/review-track", review_track)
    app.router.add_get("/review-track-clip", review_track_clip)
    # v2 product UI — see comments above. Reach via [Home] nav, link from
    # the legacy / status page TBD.
    app.router.add_get("/home", v2_home)
    app.router.add_get("/activity", v2_activity)
    app.router.add_get("/areas", v2_areas)
    # Iter 2.C: areas CRUD. Specific subpaths register BEFORE the generic
    # /areas/{id}/edit so URL routing wins on /areas/new.
    app.router.add_get("/areas/new", v2_area_new)
    app.router.add_post("/areas", v2_area_save)
    app.router.add_get("/areas/{area_id}/edit", v2_area_edit)
    app.router.add_post("/areas/{area_id}", v2_area_save)
    app.router.add_post("/areas/{area_id}/delete", v2_area_delete)
    # Iter 3 (Part IX §28): unified guidance browse. /intent and /policies
    # GET requests 301-redirect here for backward-compat. Per-rule + per-
    # policy CRUD subpaths stay live — they're the per-type detail forms
    # /memory rows link to.
    app.router.add_get("/memory", v2_memory)
    # Iter 3 / Part X §34: conversational drawer endpoints.
    app.router.add_post("/api/drawer/turn", api_drawer_turn)
    app.router.add_post("/api/drawer/confirm", api_drawer_confirm)
    # NOTE: keeping the legacy /intent listing handler reachable via
    # v2_intent for now (it's referenced by other tests / tools). The
    # public list redirect lives at the top-level route.
    app.router.add_get("/intent", v2_intent_redirect)
    # Task 9: rules CRUD via the /intent page. The matches subpath is
    # registered before the generic edit route so the URL parser doesn't
    # treat "matches" as a rule_id.
    # Iter 2.A: Preferences POST sits BEFORE the rules subpaths to keep
    # /intent/preferences from being treated as a rule_id-bearing route.
    app.router.add_post("/intent/preferences", v2_intent_preferences_save)
    app.router.add_get("/intent/rules/new", v2_intent_rule_new)
    app.router.add_post("/intent/rules", v2_intent_rule_save)
    app.router.add_get("/intent/rules/{rule_id}/edit", v2_intent_rule_edit)
    app.router.add_post("/intent/rules/{rule_id}", v2_intent_rule_save)
    app.router.add_post("/intent/rules/{rule_id}/enable", v2_intent_rule_enable)
    app.router.add_post("/intent/rules/{rule_id}/delete", v2_intent_rule_delete)
    app.router.add_get("/intent/rules/{rule_id}/matches", v2_intent_rule_matches)
    app.router.add_get("/policies", v2_policies_redirect)
    # Iter 2.D: revoke endpoint.
    app.router.add_post("/policies/{policy_id}/revoke", v2_policy_revoke)
    app.router.add_get("/cameras", v2_cameras)
    # Iter 2.B: per-camera detail + whitelist editor (Task 10 UI surface).
    # Specific subpaths registered BEFORE the generic /cameras/{id} so
    # the URL parser doesn't treat "snapshot" / "whitelist" as a camera_id.
    app.router.add_get(
        "/cameras/{camera_id}/whitelist/perception/new",
        v2_cam_wl_new_perc,
    )
    app.router.add_get(
        "/cameras/{camera_id}/whitelist/protective/new",
        v2_cam_wl_new_prot,
    )
    app.router.add_post(
        "/cameras/{camera_id}/whitelist/perception",
        v2_cam_wl_save_perc,
    )
    app.router.add_post(
        "/cameras/{camera_id}/whitelist/protective",
        v2_cam_wl_save_prot,
    )
    app.router.add_post(
        "/cameras/{camera_id}/whitelist/perception/delete",
        v2_cam_wl_del_perc,
    )
    app.router.add_post(
        "/cameras/{camera_id}/whitelist/protective/delete",
        v2_cam_wl_del_prot,
    )
    # /cameras/{camera_id}/snapshot MUST register before /cameras/{camera_id}
    # so the specific subpath wins URL matching.
    app.router.add_get("/cameras/{camera_id}/snapshot", snapshot_for_camera)
    app.router.add_get("/cameras/{camera_id}", v2_camera_detail)
    app.router.add_get("/diagnostics", v2_diagnostics)
    # Iter 3 / Part IX §30: /system storage + privacy.
    app.router.add_get("/system", v2_system)
    app.router.add_post("/system/retention", v2_system_retention)
    app.router.add_post("/system/erase-last-hour", v2_system_erase_last_hour)
    app.router.add_post("/system/purge", v2_system_purge)
    # Iter 3 / Part IX §29: /identities Enrolled list + per-subject detail.
    app.router.add_get("/identities", v2_identities)
    app.router.add_get("/identities/{subject_id}", v2_identity_detail)
    app.router.add_post("/review/label", review_label)
    app.router.add_post("/review/reject", review_reject)
    app.router.add_post("/review/merge", review_merge)
    app.router.add_get("/alerts/{alert_id}/snapshot", snapshot_for_alert)
    app.router.add_get("/alerts/{alert_id}", debug_alert)
    # Epic 10.8.1: notification tap UX — per-alert page + actions.
    app.router.add_get("/alert/{event_id}", alert_page)
    app.router.add_get("/alert/{event_id}/frame.jpg", alert_frame)
    app.router.add_get("/alert/{event_id}/annotated.jpg", alert_annotated_frame)
    # Task 1: event clip playback (stop-gap path 3 — preprocessor muxes on
    # demand). The home / activity rows surface this as a ▶ play overlay
    # on the thumbnail; the alert detail page plays it inline.
    app.router.add_get("/alert/{event_id}/clip.mp4", alert_clip)
    app.router.add_post("/alert/{event_id}/dismiss", alert_dismiss)
    app.router.add_post("/alert/{event_id}/feedback", alert_feedback)
    app.router.add_get("/logs", logs_handler)
    app.router.add_get("/debug/topology", debug_topology)
    app.router.add_get("/debug/test_snapshot", debug_test_snapshot)
    app.router.add_get("/debug/version", debug_version)
    for path in ("/healthz", "/snapshot", "/capabilities", "/recent_alerts", "/ha_cameras"):
        app.router.add_get(path, api_get)
    for path in ("/service", "/acknowledge_alert"):
        app.router.add_post(path, api_post)
    # Task 9: /api/intent/rules CRUD goes through the same dispatcher so
    # external HTTP clients (HA integration, scripts, future native app)
    # see the same view of rules the web UI does.
    async def api_delete(request: web.Request) -> web.Response:
        api._tools = boot.tools
        status, payload = await api.dispatch(
            method="DELETE", path=request.path, body={}
        )
        return web.json_response(payload, status=status)
    app.router.add_get("/api/intent/rules", api_get)
    app.router.add_get("/api/intent/rules/{rest:.*}", api_get)
    app.router.add_post("/api/intent/rules", api_post)
    app.router.add_post("/api/intent/rules/{rest:.*}", api_post)
    app.router.add_delete("/api/intent/rules/{rest:.*}", api_delete)
    # v0.3.11 zero-config discovery overrides.
    app.router.add_post("/discovery/enable", discovery_enable)
    app.router.add_post("/discovery/override", discovery_override)
    app.router.add_post("/discovery/reset", discovery_reset)
    app.router.add_post("/discovery/refresh", discovery_refresh)
    # v0.3.13 UI-driven notify service selection.
    app.router.add_post("/notify/services", notify_services_save)
    # v0.3.14 diagnostic surface.
    app.router.add_post("/notify/test", notify_test)
    app.router.add_post("/discovery/test_alert", camera_test_alert)
    # v0.3.16 motion-switch toggle + generic-motion fallback.
    app.router.add_post("/discovery/switch_toggle", discovery_switch_toggle)
    app.router.add_post("/discovery/use_generic_motion", discovery_use_generic_motion)

    # Epic 15: resilience /health + /diagnostics. The HealthService is
    # built in _run() before this app is constructed, so it's present.
    if boot.health_service is not None:
        attach_health_routes(app, boot.health_service)
    return app


async def _reconcile_discovery(boot: BootState) -> None:
    """Re-run discovery and bring HACameraLoops into line with overrides.

    Called at boot (after HA connects), from the periodic re-discover
    task, and from every POST /discovery/* handler. Safe to call
    concurrently — :class:`Reconciler` holds an internal lock.

    Failures are caught + recorded in ``boot.discovery_error`` so the
    UI shows them; never raises (we don't want a transient HA blip to
    break a user clicking Enable).
    """
    if boot.reconciler is None or boot.tools is None:
        return
    try:
        discovery = await boot.tools.discover_ha_cameras()
        overrides = load_overrides()
        decisions = build_decisions(discovery.cameras, overrides=overrides)
        # v0.3.16: annotate each enabled decision with the live state
        # of its parent HA motion-detection switches so the UI can
        # render a Turn-on banner when one is off. Done here so the
        # data refreshes on every reconcile / periodic re-discover
        # without callers having to remember to update it.
        for d in decisions:
            if d.enabled and d.spec is not None:
                try:
                    d.motion_switches = await boot.tools.find_motion_switches(d.spec.camera_entity)
                except Exception as e:
                    logger.debug(
                        "discovery.find_motion_switches_failed",
                        device_id=d.device_id,
                        error=str(e),
                    )
                    d.motion_switches = []
        boot.discovery_decisions = decisions
        target_specs = [d.spec for d in decisions if d.enabled and d.spec is not None]
        # Register device_id -> camera_entity so the
        # StreamSourceAttrProvider can look up `camera.X.stream_source`
        # when the publisher resolves credentials. Done every reconcile
        # so newly-enabled cameras are picked up immediately.
        if boot.stream_source_provider is not None:
            for spec in target_specs:
                boot.stream_source_provider.register(
                    device_id=spec.device_id, camera_entity=spec.camera_entity
                )
        await boot.reconciler.apply(target_specs)
        boot.discovery_error = None
    except Exception as e:
        boot.discovery_error = str(e)
        logger.warning("discovery.reconcile_failed", error=str(e))


async def _periodic_rediscover(boot: BootState, *, interval_seconds: float = 300.0) -> None:
    """Long-lived task that re-runs discovery every 5 minutes.

    Catches HA cameras the user added after the add-on started — no
    restart required, just an Enable click in the /ha_cameras card.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        await _reconcile_discovery(boot)


async def _bootstrap_topology_and_ha(boot: BootState, *, alert_log: AlertLog) -> None:
    """Run AFTER the HTTP server is listening. Failures land in boot.* so
    the status page shows them; the process never exits."""
    try:
        from kukiihome_shared.topology import load_topology

        topology = load_topology()
        boot.topology = topology
        boot.auto_discover = getattr(topology, "auto_discover", True)
        boot.topology_summary = {
            "profile": topology.deployment.profile,
            "household_id": topology.deployment.household_id,
            "nats": topology.bus.nats_url,
            "postgres": topology.memory.postgres_url.split("@")[-1],
            "ha_url": topology.ha_agent.ha_url,
            "vlm_backends": ", ".join(b.name for b in topology.vlm_router.backends) or "none",
            "auto_discover": "on" if boot.auto_discover else "off",
        }
        boot.ha_url = topology.ha_agent.ha_url
        boot.stage = "topology_loaded"
    except Exception:
        boot.topology_error = traceback.format_exc()
        boot.stage = "fatal"
        logger.exception("ha_agent.topology_load_failed")
        return

    # Camera loops can start even if HA connection is failing — they're
    # independent. Spawn them before the HA-connect attempt below.
    loops = build_camera_loops_from_topology(
        topology, alert_log=alert_log, registry=boot.camera_registry
    )
    boot.camera_loops = loops
    for loop in loops:
        task = asyncio.create_task(loop.run(), name=f"camera_{loop._camera_id}")
        task.add_done_callback(
            lambda t: (
                logger.warning("camera_loop.task_exception", error=str(t.exception()))
                if t.exception()
                else None
            )
        )

    try:
        settings = HAAgentSettings.from_topology(topology)
        client = HAClient(
            HAClientSettings(
                ha_url=settings.ha_url,
                ha_token=settings.ha_token,
                websocket=settings.websocket,
            )
        )
        await client.start()
        boot.client = client
        boot.tools = HATools(client)
        boot.stage = "ha_connected"
        logger.info("ha_agent.connected", ha_url=boot.ha_url)

        # Legacy adapter-driven HA camera loops (kept for back-compat).
        # New default is auto-discovery — see below.
        ha_loops = build_ha_camera_loops_from_topology(
            boot.topology,
            client=client,
            alert_log=alert_log,
            registry=boot.camera_registry,
        )
        boot.ha_camera_loops = ha_loops
        for ha_loop in ha_loops:
            ha_task = asyncio.create_task(
                ha_loop.run(),
                name=f"ha_camera_{ha_loop._camera_id}",
            )
            ha_task.add_done_callback(
                lambda t: (
                    logger.warning("ha_camera_loop.task_exception", error=str(t.exception()))
                    if t.exception()
                    else None
                )
            )

        # v0.3.13: always install the notifier so the user can toggle
        # services on/off live from the Web UI Notifications card. On
        # boot we seed its service list from
        # /data/kukiihome/notify_overrides.json (UI choices) falling
        # back to topology.notify.alert_services (YAML) if the file
        # doesn't exist. After that, the UI is the source of truth.
        yaml_services = getattr(getattr(boot.topology, "notify", None), "alert_services", [])
        initial_services = resolve_initial_services(yaml_services or [])
        # v0.3.15: fetch our HA Ingress URL prefix from Supervisor so
        # notifications open Kukii-Home on tap (not HA root) and
        # snapshot images load via HA Companion's auth session.
        ingress_base = await get_ingress_url_prefix()
        boot.ingress_url_base = ingress_base
        # Epic 10.8.6: the /app/<slug> panel route is the notification
        # tap target. Opens Kukii-Home in-app, authenticated — the only
        # form that doesn't 401 (proven against HA 2026.5).
        panel_base = await get_panel_url_base()
        boot.panel_url_base = panel_base
        notifier = AlertNotifier(
            client=client,
            notify_services=initial_services,
            kukiihome_ingress_base=ingress_base,
            panel_url_base=panel_base,
        )
        boot.notifier = notifier

        # Epic 10.6: the triage gate, not the notifier, subscribes to
        # AlertLog. Every recorded event is reasoned about (preprocessor
        # evidence when available, else HA's AI classification) and the
        # notification fires only when the decision warrants it — a
        # camera event alone never notifies. Set
        # KUKIIHOME_TRIAGE_REASONING=off to revert to legacy direct
        # notify (every event → push).
        reasoning_enabled = os.environ.get(
            "KUKIIHOME_TRIAGE_REASONING", "on"
        ).strip().lower() not in (
            "0",
            "false",
            "off",
            "no",
        )
        if reasoning_enabled:
            from kukiihome_ha_agent.reasoning import StubReasoner
            from kukiihome_ha_agent.triage import TriageGate

            gate = TriageGate(
                reasoner=StubReasoner(),
                notifier=notifier,
                event_store=boot.event_store,
                alert_log=alert_log,
                preprocessor=boot.preprocessor_client,
            )
            boot.triage_gate = gate
            alert_log.add_on_record(gate.on_alert)
            logger.info(
                "ha_agent.triage_gate_wired",
                reasoner="stub_heuristic",
                preprocessor=bool(boot.preprocessor_client),
                services=initial_services,
                panel_base=panel_base or "(none — tap URL disabled)",
            )
        else:
            alert_log.add_on_record(notifier.on_alert)
            logger.info(
                "ha_agent.triage_gate_disabled_legacy_notify",
                services=initial_services,
                from_yaml_fallback=(initial_services == list(yaml_services or [])),
                ingress_base=ingress_base or "(none — using relative URLs)",
                panel_base=panel_base or "(none — tap URL disabled)",
            )

        # v0.3.11 zero-config path: run discovery + reconciler when
        # `auto_discover` is on (the default). The reconciler manages
        # the lifecycle of HACameraLoops based on auto-picked specs +
        # per-device overrides edited from the Web UI.
        if boot.auto_discover:
            # Epic 10.1.6.3: optional NATS publisher so the
            # preprocessor's camera set tracks the Web UI Enable
            # toggle in real time. Off by default
            # (PREPROCESSOR_PUBLISH_ENABLED=1 to turn on). Failure
            # to connect doesn't block reconciler boot — locally
            # everything still works.
            if os.environ.get("PREPROCESSOR_PUBLISH_ENABLED", "").lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                stream_source = StreamSourceAttrProvider(client)
                boot.stream_source_provider = stream_source
                creds = ChainProvider(
                    [
                        stream_source,
                        JsonFileProvider(Path("/data/kukiihome/camera_rtsp_credentials.json")),
                    ]
                )
                nats_url = os.environ.get("NATS_URL", "nats://nats:4222")
                publisher = CameraConfigPublisher(nats_url=nats_url, creds=creds)
                try:
                    await publisher.connect()
                    boot.camera_publisher = publisher
                    logger.info("ha_agent.camera_publisher_wired", nats_url=nats_url)
                except Exception as e:
                    logger.warning(
                        "ha_agent.camera_publisher_connect_failed",
                        error=str(e),
                        nats_url=nats_url,
                        hint="reconciler will continue without publishing to the preprocessor",
                    )

            boot.reconciler = Reconciler(
                client=client,
                alert_log=alert_log,
                registry=boot.camera_registry,
                camera_publisher=boot.camera_publisher,
            )
            await _reconcile_discovery(boot)
            # Periodic re-discovery picks up newly-added HA cameras
            # without requiring a restart. Store the reference so the
            # task is kept alive and so we can cancel it cleanly during
            # tests or future shutdown paths.
            boot.periodic_rediscover_task = asyncio.create_task(
                _periodic_rediscover(boot),
                name="periodic_rediscover",
            )
    except Exception as e:
        boot.ha_error = str(e)
        boot.stage = "ha_failed"
        logger.warning(
            "ha_agent.no_ha_connection",
            error=str(e),
            hint="set ha_token in add-on Configuration, or check that SUPERVISOR_TOKEN reaches the container",
        )


async def _run() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    boot = BootState()
    # v0.3.12: persistent alerts. /data is the Supervisor persistent
    # volume, so alerts survive add-on restarts + updates.
    alert_log = AlertLog(persist_path="/data/kukiihome/alerts.json")

    # Task 9: rules store. Co-located under /data/kukiihome/ so it survives
    # add-on upgrades. The store is plumbed through BootState so route
    # handlers can reach it without rewiring the api object.
    from kukiihome_ha_agent.rules_store import RulesStore
    boot.rules_store = RulesStore(path="/data/kukiihome/rules.db")
    # Task 10: action whitelist + audit. Sister store to rules.db; the
    # cameras page's Authorized actions card reads + writes through it.
    from kukiihome_ha_agent.action_store import ActionStore
    boot.action_store = ActionStore(path="/data/kukiihome/actions.db")
    # Iter 2.C: areas store. Sister to rules.db / actions.db.
    from kukiihome_ha_agent.area_store import AreaStore
    boot.area_store = AreaStore(path="/data/kukiihome/areas.db")
    # Iter 2.A: preferences store (singleton row + per-actor relationships).
    from kukiihome_ha_agent.preferences_store import PreferencesStore
    boot.preferences_store = PreferencesStore(path="/data/kukiihome/preferences.db")
    # Iter 2.D: policies store (dismissals + transient intents + hits).
    from kukiihome_ha_agent.policy_store import PolicyStore
    boot.policy_store = PolicyStore(path="/data/kukiihome/policies.db")
    # Iter 3 (Part X §36): provenance store — sessions + transcripts +
    # per-guidance audit. Underlies /memory, the drawer, and the audit
    # chain extension on /alert/{id}.
    from kukiihome_ha_agent.provenance_store import ProvenanceStore
    boot.provenance_store = ProvenanceStore(path="/data/kukiihome/sessions.db")
    # Iter 3 (Part IX §30): retention policy + admin audit log.
    from kukiihome_ha_agent.retention_store import RetentionStore
    boot.retention_store = RetentionStore(path="/data/kukiihome/retention.db")
    # Iter 3 (Part X §35): LLM-backed conversational dispatcher. Reads
    # endpoint config from env (KUKIIHOME_LLM_URL / _API_KEY / _MODEL).
    # When unconfigured, dispatcher = HeuristicDispatcherProvider directly
    # so the drawer always has a placement path. When configured, wraps
    # the LLM provider in a Composite that falls back to heuristic + tracks
    # health so /memory can surface the degraded-mode banner.
    import os as _os

    from kukiihome_ha_agent.dispatcher import (
        CompositeDispatcherProvider,
        HeuristicDispatcherProvider,
        LLMDispatcherProvider,
    )
    from kukiihome_ha_agent.llm_client import LLMHealthTracker, OpenAIChatClient
    boot.llm_health = LLMHealthTracker()
    llm_url = (_os.environ.get("KUKIIHOME_LLM_URL") or "").strip()
    llm_key = (_os.environ.get("KUKIIHOME_LLM_API_KEY") or "").strip()
    llm_model = (_os.environ.get("KUKIIHOME_LLM_MODEL") or "llama-3.3-70b").strip()
    if llm_url and llm_key:
        client = OpenAIChatClient(
            base_url=llm_url, api_key=llm_key, model=llm_model,
        )
        # Iter 3 follow-up (Task 53): tool-calling enabled. The LLM can
        # search existing guidance + look up KnownActor profiles before
        # placing, so multi-turn refinement avoids duplicate rules.
        from kukiihome_ha_agent.dispatcher_tools import tools_from_boot
        tools = tools_from_boot(boot)
        boot.dispatcher = CompositeDispatcherProvider(
            llm=LLMDispatcherProvider(client, tools=tools),
            heuristic=HeuristicDispatcherProvider(),
            health=boot.llm_health,
        )
        logger.info(
            "dispatcher.llm.configured",
            base_url=llm_url, model=llm_model,
            tool_count=len(tools),
        )
    else:
        boot.dispatcher = HeuristicDispatcherProvider()
        logger.info("dispatcher.heuristic.fallback_only")

    # Epic 10.8.1: per-event persistent store. Lives next to
    # alerts.json in the Supervisor's /data volume. Subscribed to
    # AlertLog so every alert recorded also gets a durable per-event
    # directory with frame copy + structured meta + room for VLM
    # response / user feedback. Failure to write to the event store
    # is non-fatal — alerts still record into AlertLog.
    event_store = EventStore(root=Path("/data/kukiihome/events"))
    alert_log.add_on_record(event_store.record_from_alert)
    boot.event_store = event_store

    # Epic 10.9: enrich alerts with preprocessor recognition. When a
    # preprocessor URL is configured, every recorded alert triggers an
    # async pull of that camera's FrameWindow (detections + identified
    # entities + annotated frame) which is folded into the event. The
    # callback is registered AFTER event_store.record_from_alert so the
    # event directory already exists when record_enrichment runs.
    # Unconfigured (no inference box reachable) → simply skipped; alerts
    # keep their HA snapshot + rule-that-fired.
    preprocessor_url = os.environ.get("KUKIIHOME_PREPROCESSOR_URL", "").strip()
    if preprocessor_url:
        boot.preprocessor_client = PreprocessorClient(preprocessor_url)
        enricher = AlertEnricher(client=boot.preprocessor_client, event_store=event_store)
        alert_log.add_on_record(enricher.on_alert)
        boot.enricher = enricher
        logger.info("ha_agent.enricher_wired", preprocessor_url=preprocessor_url)
    else:
        logger.info(
            "ha_agent.enricher_disabled",
            hint="set KUKIIHOME_PREPROCESSOR_URL to enrich alerts with recognition",
        )

    # Epic 15: build the resilience health service before the app, so
    # _build_app can attach /health + /diagnostics. The F4 (HA down) probe
    # reads boot.client liveness dynamically — None (pre-connect) and a
    # disconnected client both report offline, which is correct.
    boot.health_service = build_health_service(
        is_connected=lambda: boot.client is not None and boot.client.is_connected,
    )

    # Bring the HTTP server up FIRST. If this fails, there's a real
    # network-level problem (port in use, no interface, etc.) and there's
    # nothing the status page can do about it.
    app = _build_app(boot=boot, alert_log=alert_log, event_store=event_store)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
    await site.start()
    logger.info("ha_agent.listening", host=LISTEN_HOST, port=LISTEN_PORT)

    # Epic 10.8.4: publish mDNS so HA's zeroconf discovery sees us.
    # Held on boot so the registration outlives this function. Best-
    # effort — if zeroconf isn't importable or registration fails the
    # manual config flow still works.
    from kukiihome_ha_agent import __version__ as _pkg_version
    from kukiihome_ha_agent.discovery_publish import publish_kukiihome

    boot.discovery_handle = publish_kukiihome(port=LISTEN_PORT, version=_pkg_version)

    # Now do the rest in the background. Any failure surfaces on the page.
    bootstrap_task = asyncio.create_task(_bootstrap_topology_and_ha(boot, alert_log=alert_log))
    bootstrap_task.add_done_callback(
        lambda t: (
            logger.warning("ha_agent.bootstrap_exception", error=str(t.exception()))
            if t.exception()
            else None
        )
    )

    # Epic 15: start the resilience watchdog poll loop. Detects HA-down
    # (F4), records transitions to the diagnostic ring, and keeps /health
    # current. Independent of the bootstrap task; cancelled on shutdown.
    watchdog_task = asyncio.create_task(boot.health_service.run(), name="health_watchdog")

    try:
        await asyncio.Event().wait()
    finally:
        watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog_task
        for loop in boot.camera_loops:
            await loop.stop()
        for ha_loop in boot.ha_camera_loops:
            await ha_loop.stop()
        await runner.cleanup()
        if boot.client is not None:
            await boot.client.stop()
        if boot.preprocessor_client is not None:
            await boot.preprocessor_client.close()


def _load_dotenv_if_present() -> None:
    """Best-effort .env loader for local development. In the HA add-on
    image, env vars come from the Supervisor; this is just so the same
    code path works when running directly from the repo. Walks up from
    cwd to find a .env file; existing env vars take precedence (caller's
    explicit setting wins over the file)."""
    import os as _os
    from pathlib import Path

    cur = Path.cwd().resolve()
    for d in [cur, *cur.parents]:
        env_path = d / ".env"
        if env_path.is_file():
            try:
                for raw in env_path.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    # Caller env wins — don't overwrite an already-set var.
                    _os.environ.setdefault(key, value)
            except OSError as e:
                logger.warning("dotenv.read_failed", path=str(env_path), error=str(e))
            return


def main() -> None:
    """Service entry point."""
    _load_dotenv_if_present()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
