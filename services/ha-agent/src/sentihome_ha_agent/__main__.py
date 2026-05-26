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
import json
import logging
import os
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from aiohttp import web

from sentihome_ha_agent.camera_loop import (
    CameraLoop,
    CameraLoopRegistry,
    HACameraLoop,
    build_camera_loops_from_topology,
    build_ha_camera_loops_from_topology,
)
from sentihome_ha_agent.client import HAClient, HAClientSettings
from sentihome_ha_agent.config import HAAgentSettings
from sentihome_ha_agent.http_api import AlertLog, HAAgentAPI
from sentihome_ha_agent.mcp_tools import HATools

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


_STATUS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SentiHome</title>
<meta http-equiv="refresh" content="10">
<!-- base href="./" makes all relative URLs resolve against the current
     document's directory. Belt-and-suspenders for HA Ingress, which
     may or may not preserve the trailing slash on the page URL. -->
<base href="./">
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
<h1>SentiHome</h1>
<p class="muted">v__VERSION__ &middot; stage: <code>__STAGE__</code> &middot; auto-refresh every 10 s</p>

__TOPOLOGY_CARD__
__HA_CAMERAS_CARD__
__CAMERAS_CARD__
__HA_CARD__
__ALERTS_CARD__
__CAPABILITIES_CARD__
__LOGS_CARD__

<div class="footer">
<p>Next step: install the
<a href="https://github.com/DarinShapiro/SentiHome/blob/main/docs/install.md">SentiHome custom integration</a>
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


async def _render_status(boot: BootState, alert_log: AlertLog) -> str:
    from sentihome_ha_agent import __version__

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
            tier = a.get("tier", "")
            status = "ack" if a.get("acknowledged") else "open"

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

            row_strs.append(
                f"<tr><td>{thumb}</td>"
                f"<td>{when}</td>"
                f"<td>{headline}</td>"
                f"<td>{tier}</td>"
                f"<td>{status}</td></tr>"
            )
        alerts_card = (
            '<div class="card"><h3>Recent alerts</h3>'
            "<table><tr><th>Snapshot</th><th>Time</th><th>Headline</th>"
            "<th>Tier</th><th>Status</th></tr>" + "".join(row_strs) + "</table></div>"
        )
    else:
        alerts_card = (
            '<div class="card"><h3>Recent alerts</h3><p class="muted">No alerts yet.</p></div>'
        )

    # ─── capabilities card ─────────────────────────────────────────
    caps_html = boot.topology_summary.pop("__caps_html", "—")
    caps_card = (
        '<div class="card"><h3>Capabilities</h3>'
        '<p class="muted">Domains SentiHome can act on in your HA:</p>'
        f"<p>{caps_html}</p></div>"
    )

    # ─── HA cameras detected card (read-only discovery) ───────────
    # Shows every camera HA already knows about, with heuristically-matched
    # motion sensors. User pastes the camera_entity + motion_candidates
    # into the add-on Configuration to wire SentiHome to it.
    ha_cameras_card = '<div class="card"><h3>HA cameras detected</h3>'
    if boot.tools is None:
        ha_cameras_card += '<p class="muted">Connect to HA first (see card below).</p></div>'
    else:
        try:
            discovery = await boot.tools.discover_ha_cameras()
            ha_cams = discovery.cameras
            unmatched = discovery.unmatched_motion_sensors
        except Exception as e:
            ha_cams = []
            unmatched = []
            ha_cameras_card += f'<p class="bad">Discovery failed: {e}</p>'
        if not ha_cams and not unmatched:
            ha_cameras_card += (
                '<p class="muted">HA has no camera.* entities. Add a camera '
                "via an HA integration (Generic Camera, ONVIF, Reolink, etc.) "
                "and refresh.</p></div>"
            )
        else:
            rows = []
            for c in ha_cams:
                name = c.friendly_name or c.camera_entity
                motion_html = (
                    ", ".join(f"<code>{m}</code>" for m in c.motion_candidates)
                    if c.motion_candidates
                    else '<span class="muted">none auto-matched</span>'
                )
                state_class = "bad" if c.state in ("unavailable", "unknown") else "muted"
                rows.append(
                    f"<tr><td><code>{c.camera_entity}</code><br/>"
                    f'<span class="muted">{name}</span></td>'
                    f'<td><span class="{state_class}">{c.state}</span></td>'
                    f"<td>{motion_html}</td></tr>"
                )
            ha_cameras_card += (
                "<table><tr><th>Camera entity</th><th>State</th>"
                "<th>Motion / AI sensors</th></tr>" + "".join(rows) + "</table>"
            )

            if unmatched:
                ha_cameras_card += (
                    '<h4 style="margin-top:1rem;">Unmatched motion sensors</h4>'
                    '<p class="muted">Motion-like binary sensors I couldn\'t '
                    "auto-pair with a camera (likely because their entity names "
                    "don't share tokens with any camera). Manually wire the "
                    "right one into <code>motion_entities</code> below.</p>"
                    "<ul>" + "".join(f"<li><code>{m}</code></li>" for m in unmatched) + "</ul>"
                )

            ha_cameras_card += (
                '<p class="muted">To wire one of these into SentiHome, paste '
                "into the add-on Configuration:</p>"
                "<pre>adapters:\n"
                "  - name: my-cam\n"
                "    kind: ha-camera\n"
                "    camera_entity: camera.YOUR_CAMERA\n"
                "    motion_entities:\n"
                "      - binary_sensor.YOUR_MOTION_SENSOR</pre></div>"
            )

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
            '<div class="card"><h3>Cameras configured for SentiHome</h3>'
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
        .replace("__LOGS_CARD__", logs_card)
    )


def _build_app(*, boot: BootState, alert_log: AlertLog) -> web.Application:
    api = HAAgentAPI(tools=None, alert_log=alert_log)  # tools rebound below

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

        from sentihome_ha_agent import __version__ as pkg_version

        addon_version = "unknown"
        version_file = Path("/app/.sentihome_addon_version")
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

    app = web.Application()
    app.router.add_get("/", status_page)
    app.router.add_get("/cameras/{camera_id}/snapshot", snapshot_for_camera)
    app.router.add_get("/alerts/{alert_id}/snapshot", snapshot_for_alert)
    app.router.add_get("/alerts/{alert_id}", debug_alert)
    app.router.add_get("/logs", logs_handler)
    app.router.add_get("/debug/topology", debug_topology)
    app.router.add_get("/debug/test_snapshot", debug_test_snapshot)
    app.router.add_get("/debug/version", debug_version)
    for path in ("/healthz", "/snapshot", "/capabilities", "/recent_alerts", "/ha_cameras"):
        app.router.add_get(path, api_get)
    for path in ("/service", "/acknowledge_alert"):
        app.router.add_post(path, api_post)
    return app


async def _bootstrap_topology_and_ha(boot: BootState, *, alert_log: AlertLog) -> None:
    """Run AFTER the HTTP server is listening. Failures land in boot.* so
    the status page shows them; the process never exits."""
    try:
        from sentihome_shared.topology import load_topology

        topology = load_topology()
        boot.topology = topology
        boot.topology_summary = {
            "profile": topology.deployment.profile,
            "household_id": topology.deployment.household_id,
            "nats": topology.bus.nats_url,
            "postgres": topology.memory.postgres_url.split("@")[-1],
            "ha_url": topology.ha_agent.ha_url,
            "vlm_backends": ", ".join(b.name for b in topology.vlm_router.backends) or "none",
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

        # ha-camera loops need the live HAClient — spawn them now.
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
    alert_log = AlertLog()

    # Bring the HTTP server up FIRST. If this fails, there's a real
    # network-level problem (port in use, no interface, etc.) and there's
    # nothing the status page can do about it.
    app = _build_app(boot=boot, alert_log=alert_log)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
    await site.start()
    logger.info("ha_agent.listening", host=LISTEN_HOST, port=LISTEN_PORT)

    # Now do the rest in the background. Any failure surfaces on the page.
    bootstrap_task = asyncio.create_task(_bootstrap_topology_and_ha(boot, alert_log=alert_log))
    bootstrap_task.add_done_callback(
        lambda t: (
            logger.warning("ha_agent.bootstrap_exception", error=str(t.exception()))
            if t.exception()
            else None
        )
    )

    try:
        await asyncio.Event().wait()
    finally:
        for loop in boot.camera_loops:
            await loop.stop()
        for ha_loop in boot.ha_camera_loops:
            await ha_loop.stop()
        await runner.cleanup()
        if boot.client is not None:
            await boot.client.stop()


def main() -> None:
    """Service entry point."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
