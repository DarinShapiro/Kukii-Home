"""/diagnostics — system + observability surface (Part VIII).

The home page shows the *reasoned* incident stream; this page shows the
*observed* stream + dev loop. Top-to-bottom:

  - **System** — version, preprocessor reachability, HA connection,
    /data store health.
  - **Cameras roll-up** — per-camera frames_read, motion_events,
    last_error in a compact table.
  - **Stores** — counts across rules / actions / areas / policies /
    preferences so the user can see what's accumulated.
  - **Action runtime** — protective_actions_log recent hits + pending
    perception reverts.
  - **Reasoner** — recent VLM-decision counts by criticality, recent
    matched rules.
  - **Legacy** — link out to ``/`` (the existing topology+capability +
    logs status page) while the *audit edge browser* + *dev loop
    dashboard* (full Part VIII) lands across future iterations.

Pure renderer — fed a structured view model from the route handler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from kukiihome_ha_agent.web_ui.shell import _e, friendly_time_html

_LOG = structlog.get_logger(__name__)


@dataclass
class StoresSnapshot:
    rules_active: int = 0
    rules_total: int = 0
    areas: int = 0
    perception_entries: int = 0
    protective_entries: int = 0
    policies_dismissals: int = 0
    policies_transient_intents: int = 0


@dataclass
class CameraHealthRow:
    camera_id: str
    name: str
    state: str
    frames_read: int = 0
    motion_events: int = 0
    last_error: str = ""


@dataclass
class ActionRuntimeStats:
    perception_pending: int = 0
    protective_recent_ok: int = 0
    protective_recent_gated: int = 0
    protective_recent_failed: int = 0


@dataclass
class ReasonerStats:
    decisions_24h: int = 0
    alerts_24h: int = 0
    dismissed_24h: int = 0
    last_decision_ts: float | None = None


@dataclass
class GraphSubstrateSnapshot:
    """Epic 10.2: memory-graph substrate health + node counts.

    ``backend`` is ``"neo4j"`` (durable sidecar / bolt URL) or
    ``"in_memory"`` (Phase 1 shadow / Neo4j-unavailable fallback).
    Counts let the operator watch the graph fill from real traffic
    and confirm the dual-write seam is live."""

    backend: str = "in_memory"
    events: int = 0
    policies: int = 0
    actors: int = 0
    vlm_decisions: int = 0
    available: bool = True
    """False only if even the count queries errored — surfaces a broken
    substrate rather than silently showing zeros."""


@dataclass
class DiagnosticsViewModel:
    version: str
    preprocessor_ok: bool | None
    preprocessor_url: str | None
    ha_connected: bool
    ha_entities: int
    stores: StoresSnapshot = field(default_factory=StoresSnapshot)
    cameras: list[CameraHealthRow] = field(default_factory=list)
    action_runtime: ActionRuntimeStats = field(default_factory=ActionRuntimeStats)
    reasoner: ReasonerStats = field(default_factory=ReasonerStats)
    graph: GraphSubstrateSnapshot = field(default_factory=GraphSubstrateSnapshot)
    now_ts: float | None = None
    legacy_status_url: str = "/"


# ─── Section renderers ─────────────────────────────────────────────


def _ok_chip(ok: bool | None, *, when_ok: str, when_bad: str) -> str:
    if ok is None:
        return "<span class='chip cam-state muted'>unknown</span>"
    css = "ok" if ok else "bad"
    label = when_ok if ok else when_bad
    return f"<span class='chip cam-state {css}'>{_e(label)}</span>"


def _system_section(vm: DiagnosticsViewModel) -> str:
    prep_chip = _ok_chip(
        vm.preprocessor_ok, when_ok="reachable", when_bad="unreachable",
    )
    ha_chip = _ok_chip(
        vm.ha_connected, when_ok="connected", when_bad="disconnected",
    )
    prep_url = (
        f"<span class='muted'>at {_e(vm.preprocessor_url)}</span>"
        if vm.preprocessor_url else "<span class='muted'>no URL configured</span>"
    )
    return (
        "<section class='card'>"
        "<h2>System</h2>"
        "<div class='cam-row'>"
        f"<b>Version:</b> {_e(vm.version)} · "
        f"<b>Preprocessor:</b> {prep_chip} {prep_url} · "
        f"<b>HA:</b> {ha_chip} <span class='muted'>{vm.ha_entities} "
        "entities</span>"
        "</div>"
        "</section>"
    )


def _stores_section(stores: StoresSnapshot) -> str:
    return (
        "<section class='card'>"
        "<h2>Stores</h2>"
        "<table class='matrix-table'><tbody>"
        f"<tr><td>Rules</td><td>"
        f"<b>{stores.rules_active}</b> active · "
        f"{stores.rules_total} total</td></tr>"
        f"<tr><td>Areas</td><td><b>{stores.areas}</b></td></tr>"
        f"<tr><td>Action whitelist</td><td>"
        f"<b>{stores.perception_entries}</b> perception · "
        f"<b>{stores.protective_entries}</b> protective</td></tr>"
        f"<tr><td>Policies</td><td>"
        f"<b>{stores.policies_dismissals}</b> dismissals · "
        f"<b>{stores.policies_transient_intents}</b> transient intents"
        "</td></tr>"
        "</tbody></table>"
        "</section>"
    )


def _graph_section(g: GraphSubstrateSnapshot) -> str:
    """Memory-graph substrate panel (Epic 10.2). Shows which backend is
    live + the node counts so the dual-write seam is observable."""
    if g.backend == "neo4j":
        backend_chip = "<span class='chip cam-state ok'>Neo4j</span>"
        backend_note = "<span class='muted'>durable + vector index</span>"
    else:
        backend_chip = "<span class='chip cam-state muted'>in-memory</span>"
        backend_note = (
            "<span class='muted'>shadow (non-persistent) — set "
            "<code>KUKIIHOME_NEO4J_URL</code> for the durable sidecar"
            "</span>"
        )
    if not g.available:
        backend_chip = "<span class='chip cam-state bad'>error</span>"
    return (
        "<section class='card'>"
        "<h2>Memory graph</h2>"
        f"<div class='cam-row'><b>Backend:</b> {backend_chip} {backend_note}</div>"
        "<table class='matrix-table'><tbody>"
        f"<tr><td>Events</td><td><b>{g.events}</b></td></tr>"
        f"<tr><td>Policies</td><td><b>{g.policies}</b></td></tr>"
        f"<tr><td>Known actors</td><td><b>{g.actors}</b></td></tr>"
        f"<tr><td>VLM decisions</td><td><b>{g.vlm_decisions}</b></td></tr>"
        "</tbody></table>"
        "</section>"
    )


def _cameras_section(rows: list[CameraHealthRow]) -> str:
    if not rows:
        body = "<div class='empty'>No cameras configured.</div>"
    else:
        body = (
            "<table class='matrix-table'>"
            "<thead><tr><th>Camera</th><th>State</th>"
            "<th>Frames</th><th>Motion events</th><th>Last error</th></tr></thead>"
            "<tbody>"
            + "".join(
                "<tr>"
                f"<td><b>{_e(c.name)}</b><br><span class='muted'>"
                f"{_e(c.camera_id)}</span></td>"
                f"<td>{_ok_chip(c.state == 'running', when_ok=c.state, when_bad=c.state)}</td>"
                f"<td>{c.frames_read}</td>"
                f"<td>{c.motion_events}</td>"
                f"<td class='muted'>{_e(c.last_error or '—')}</td>"
                "</tr>"
                for c in rows
            )
            + "</tbody></table>"
        )
    return (
        "<section class='card'>"
        "<h2>Cameras</h2>"
        f"{body}"
        "</section>"
    )


def _action_runtime_section(stats: ActionRuntimeStats) -> str:
    return (
        "<section class='card'>"
        "<h2>Action runtime</h2>"
        "<div class='cam-row'>"
        f"<b>{stats.perception_pending}</b> perception reverts pending · "
        f"<b>{stats.protective_recent_ok}</b> protective ok (24h) · "
        f"<b>{stats.protective_recent_gated}</b> gated · "
        f"<b>{stats.protective_recent_failed}</b> failed"
        "</div>"
        "<div class='sub'>"
        "Perception actions (class 2) auto-revert; protective actions "
        "(class 3) are persistent and policy-gated per camera. The "
        "/cameras detail page edits each camera's whitelist."
        "</div>"
        "</section>"
    )


def _reasoner_section(stats: ReasonerStats, *, now_ts: float | None) -> str:
    last_html = (
        friendly_time_html(stats.last_decision_ts, now=now_ts)
        if stats.last_decision_ts and now_ts
        else "<span class='muted'>none yet</span>"
    )
    return (
        "<section class='card'>"
        "<h2>Reasoner</h2>"
        "<div class='cam-row'>"
        f"<b>{stats.decisions_24h}</b> VLM decisions (24h) · "
        f"<b>{stats.alerts_24h}</b> alerted · "
        f"<b>{stats.dismissed_24h}</b> dismissed · "
        f"last decision: {last_html}"
        "</div>"
        "<div class='sub'>"
        "Full per-VLM-call trace + audit-edge browser (CITED / INFLUENCED "
        "/ YIELDED) + dev-loop dashboard lands in subsequent iterations. "
        "Today this is a roll-up of triage gate outcomes."
        "</div>"
        "</section>"
    )


def _legacy_section(legacy_url: str) -> str:
    return (
        "<section class='card'>"
        "<h2>Legacy status</h2>"
        "<div class='sub'>The pre-v2 status page (topology, capabilities, "
        "logs, raw camera registrations) is still available while the "
        "Diagnostics surface is being built out.</div>"
        f"<div class='form-actions' style='justify-content:flex-start;margin-top:12px'>"
        f"<a class='btn' href='{_e(legacy_url)}'>Open legacy status</a>"
        f"</div>"
        "</section>"
    )


# ─── Top-level ────────────────────────────────────────────────────


def render_diagnostics_page(vm: DiagnosticsViewModel) -> str:
    return (
        "<h1>Diagnostics</h1>"
        "<div class='sub'>System + observability. The home page shows the "
        "<i>reasoned</i> incident stream; this is the <i>observed</i> "
        "stream + roll-ups across stores + the dev loop dashboard.</div>"
        + _system_section(vm)
        + _stores_section(vm.stores)
        + _graph_section(vm.graph)
        + _cameras_section(vm.cameras)
        + _action_runtime_section(vm.action_runtime)
        + _reasoner_section(vm.reasoner, now_ts=vm.now_ts)
        + _legacy_section(vm.legacy_status_url)
    )


# ─── View model builder ──────────────────────────────────────────


def build_diagnostics_vm(
    *,
    version: str,
    preprocessor_ok: bool | None,
    preprocessor_url: str | None,
    ha_connected: bool,
    ha_entities: int,
    rules_store: Any | None,
    action_store: Any | None,
    area_store: Any | None,
    policy_store: Any | None,
    registry_statuses: list[Any],
    ha_loops: list[Any],
    alerts: list[dict],
    now_ts: float,
    graph_client: Any | None = None,
    graph_backend: str = "in_memory",
) -> DiagnosticsViewModel:
    """Wires the live store contents into the view model. Each store is
    optional so older boot paths and tests can pass None."""
    from kukiihome_ha_agent.web_ui.camera_data import (
        build_camera_summaries,
    )

    # Stores summary
    stores = StoresSnapshot()
    if rules_store is not None:
        try:
            active = rules_store.active_rules()
            total = rules_store.all_rules()
            stores.rules_active = len(active)
            stores.rules_total = len(total)
        except Exception as e:
            # Each store read is best-effort — a broken store shouldn't
            # blank the whole diagnostics page.
            _LOG.debug("diagnostics.store_read_failed", error=str(e))
    if area_store is not None:
        try:
            stores.areas = len(area_store.all_areas())
        except Exception as e:
            # Each store read is best-effort — a broken store shouldn't
            # blank the whole diagnostics page.
            _LOG.debug("diagnostics.store_read_failed", error=str(e))
    # Hoist build_camera_summaries — earlier code called it 3x
    # (once each for perception/protective sums + once for cam health
    # rows below). Compute the list once + reuse.
    cam_summaries_for_counts = build_camera_summaries(
        registry_statuses=registry_statuses, ha_loops=ha_loops,
        alerts=[], now_ts=now_ts,
    )
    if action_store is not None:
        try:
            stores.perception_entries = sum(
                len(action_store.perception_for(c.camera_id))
                for c in cam_summaries_for_counts
            )
            stores.protective_entries = sum(
                len(action_store.protective_for(c.camera_id))
                for c in cam_summaries_for_counts
            )
        except Exception as e:
            # Each store read is best-effort — a broken store shouldn't
            # blank the whole diagnostics page.
            _LOG.debug("diagnostics.store_read_failed", error=str(e))
    if policy_store is not None:
        try:
            stores.policies_dismissals = len(
                policy_store.all_policies(kind="dismissal", now_ts=now_ts)
            )
            stores.policies_transient_intents = len(
                policy_store.all_policies(kind="transient_intent", now_ts=now_ts)
            )
        except Exception as e:
            # Each store read is best-effort — a broken store shouldn't
            # blank the whole diagnostics page.
            _LOG.debug("diagnostics.store_read_failed", error=str(e))

    # Graph substrate snapshot (Epic 10.2). All counts best-effort —
    # a graph hiccup must not blank the diagnostics page.
    graph = GraphSubstrateSnapshot(backend=graph_backend)
    if graph_client is not None:
        try:
            graph.events = graph_client.count_events()
            graph.policies = graph_client.count_policies()
            graph.vlm_decisions = graph_client.count_vlm_decisions()
            graph.actors = len(graph_client.list_all_known_actors())
        except Exception as e:
            graph.available = False
            _LOG.debug("diagnostics.graph_count_failed", error=str(e))

    # Camera health rows
    cam_summaries = build_camera_summaries(
        registry_statuses=registry_statuses, ha_loops=ha_loops,
        alerts=alerts, now_ts=now_ts,
    )
    # Map registry frames/motion_events back onto rows.
    by_id = {
        getattr(s, "camera_id", ""): s for s in (registry_statuses or [])
    }
    camera_rows = [
        CameraHealthRow(
            camera_id=c.camera_id, name=c.name, state=c.state,
            last_error=c.last_error,
            frames_read=getattr(by_id.get(c.camera_id), "frames_read", 0),
            motion_events=getattr(by_id.get(c.camera_id), "motion_events", 0),
        )
        for c in cam_summaries
    ]

    # Action runtime stats from log table
    action_stats = ActionRuntimeStats()
    if action_store is not None:
        try:
            since = now_ts - 86400.0
            recent = action_store.recent_log(limit=200)
            action_stats.protective_recent_ok = sum(
                1 for r in recent if r.ts >= since and r.status == "ok"
            )
            action_stats.protective_recent_gated = sum(
                1 for r in recent if r.ts >= since and r.status == "gated"
            )
            action_stats.protective_recent_failed = sum(
                1 for r in recent if r.ts >= since and r.status == "failed"
            )
        except Exception as e:
            # Each store read is best-effort — a broken store shouldn't
            # blank the whole diagnostics page.
            _LOG.debug("diagnostics.store_read_failed", error=str(e))

    # Reasoner stats from alert log triage_status
    since = now_ts - 86400.0
    decisions_24h = 0
    alerts_24h = 0
    dismissed_24h = 0
    last_decision_ts: float | None = None
    for a in alerts:
        ts = float(a.get("trigger_ts") or 0.0)
        if ts < since:
            continue
        status = (a.get("triage_status") or "").lower()
        if status in ("alerted", "dismissed"):
            decisions_24h += 1
            if status == "alerted":
                alerts_24h += 1
            else:
                dismissed_24h += 1
            if last_decision_ts is None or ts > last_decision_ts:
                last_decision_ts = ts

    return DiagnosticsViewModel(
        version=version,
        preprocessor_ok=preprocessor_ok,
        preprocessor_url=preprocessor_url,
        ha_connected=ha_connected,
        ha_entities=ha_entities,
        stores=stores,
        cameras=camera_rows,
        action_runtime=action_stats,
        reasoner=ReasonerStats(
            decisions_24h=decisions_24h, alerts_24h=alerts_24h,
            dismissed_24h=dismissed_24h, last_decision_ts=last_decision_ts,
        ),
        graph=graph,
        now_ts=now_ts,
    )
