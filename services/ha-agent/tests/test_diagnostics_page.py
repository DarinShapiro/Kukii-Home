"""Diagnostics (Part VIII) — view-model builder + page renderer."""

from __future__ import annotations

from kukiihome_ha_agent.action_store import (
    ActionStore,
    PerceptionEntry,
    ProtectiveEntry,
    ProtectiveLogRow,
)
from kukiihome_ha_agent.area_store import Area, AreaStore
from kukiihome_ha_agent.policy_store import Policy, PolicyStore
from kukiihome_ha_agent.rules_store import Rule, RulesStore
from kukiihome_ha_agent.web_ui.diagnostics import (
    ActionRuntimeStats,
    CameraHealthRow,
    DiagnosticsViewModel,
    ReasonerStats,
    StoresSnapshot,
    build_diagnostics_vm,
    render_diagnostics_page,
)

NOW = 1_700_000_000.0


class _FakeStatus:
    def __init__(self, cid, *, state="running", frames=0, motions=0, err=""):
        self.camera_id = cid
        self.state = state
        self.frames_read = frames
        self.motion_events = motions
        self.last_error = err


class _FakeHALoop:
    def __init__(self, cid, friendly):
        self.camera_id = cid
        self.friendly_name = friendly


def _alert(camera_id="pool", status="alerted", ts_offset=600):
    return {
        "camera_id": camera_id,
        "trigger_ts": NOW - ts_offset,
        "triage_status": status,
    }


# ─── view-model builder ────────────────────────────────────────────


def test_build_vm_stores_counts_rules_areas_policies():
    rules = RulesStore(path=None)
    areas = AreaStore(path=None)
    actions = ActionStore(path=None)
    pols = PolicyStore(path=None)
    rules.create(Rule(id="", name="active", mode="nl", intent_text=""))
    rules.create(Rule(id="", name="disabled", mode="nl",
                       intent_text="", enabled=False))
    areas.create(Area(id="", name="Pool"))
    areas.create(Area(id="", name="Yard"))
    actions.upsert_perception(PerceptionEntry(
        camera_id="pool", target_kind="ha_service", target="x",
    ))
    actions.upsert_protective(ProtectiveEntry(
        camera_id="pool", action_class="lock", service="lock.lock",
        target="lock.x",
    ))
    pols.create(Policy(id="", kind="dismissal", name="d"))
    pols.create(Policy(id="", kind="transient_intent", name="t"))

    vm = build_diagnostics_vm(
        version="0.16.0", preprocessor_ok=True, preprocessor_url="http://prep",
        ha_connected=True, ha_entities=10,
        rules_store=rules, action_store=actions, area_store=areas,
        policy_store=pols,
        registry_statuses=[_FakeStatus("pool")], ha_loops=[],
        alerts=[], now_ts=NOW,
    )
    s = vm.stores
    assert s.rules_active == 1
    assert s.rules_total == 2
    assert s.areas == 2
    assert s.perception_entries == 1
    assert s.protective_entries == 1
    assert s.policies_dismissals == 1
    assert s.policies_transient_intents == 1

    rules.close()
    areas.close()
    actions.close()
    pols.close()


def test_build_vm_cameras_pulls_health_from_registry():
    vm = build_diagnostics_vm(
        version="0.x", preprocessor_ok=True, preprocessor_url="http://x",
        ha_connected=True, ha_entities=0,
        rules_store=None, action_store=None, area_store=None,
        policy_store=None,
        registry_statuses=[
            _FakeStatus("pool", state="running", frames=1234, motions=8),
        ],
        ha_loops=[_FakeHALoop("pool", "Pool Camera Fluent")],
        alerts=[], now_ts=NOW,
    )
    assert len(vm.cameras) == 1
    c = vm.cameras[0]
    assert c.frames_read == 1234
    assert c.motion_events == 8
    assert c.name == "Pool Camera"   # suffix-stripped via shell helper


def test_build_vm_reasoner_counts_24h_alerts_and_dismissals():
    alerts = [
        _alert(status="alerted", ts_offset=600),       # 24h, alerted
        _alert(status="dismissed", ts_offset=3600),    # 24h, dismissed
        _alert(status="alerted", ts_offset=30 * 3600), # outside 24h
    ]
    vm = build_diagnostics_vm(
        version="0.x", preprocessor_ok=True, preprocessor_url=None,
        ha_connected=True, ha_entities=0,
        rules_store=None, action_store=None, area_store=None,
        policy_store=None,
        registry_statuses=[_FakeStatus("pool")], ha_loops=[],
        alerts=alerts, now_ts=NOW,
    )
    assert vm.reasoner.decisions_24h == 2
    assert vm.reasoner.alerts_24h == 1
    assert vm.reasoner.dismissed_24h == 1


def test_build_vm_action_runtime_counts_protective_status():
    actions = ActionStore(path=None)
    base = ProtectiveLogRow(
        incident_id="i", camera_id="pool", ts=NOW - 600,
        action_class="lock", service="lock.lock", target="lock.x",
        data_json=None, status="ok",
    )
    actions.log_protective(base)
    actions.log_protective(ProtectiveLogRow(
        **{**base.__dict__, "id": None, "ts": NOW - 700, "status": "gated"},
    ))
    actions.log_protective(ProtectiveLogRow(
        **{**base.__dict__, "id": None, "ts": NOW - 200_000,  # outside 24h
           "status": "ok"},
    ))
    vm = build_diagnostics_vm(
        version="0.x", preprocessor_ok=True, preprocessor_url=None,
        ha_connected=True, ha_entities=0,
        rules_store=None, action_store=actions, area_store=None,
        policy_store=None,
        registry_statuses=[_FakeStatus("pool")], ha_loops=[],
        alerts=[], now_ts=NOW,
    )
    assert vm.action_runtime.protective_recent_ok == 1
    assert vm.action_runtime.protective_recent_gated == 1
    actions.close()


def test_build_vm_tolerates_all_stores_missing():
    # All stores None → no exceptions, defaults
    vm = build_diagnostics_vm(
        version="0.x", preprocessor_ok=None, preprocessor_url=None,
        ha_connected=False, ha_entities=0,
        rules_store=None, action_store=None, area_store=None,
        policy_store=None,
        registry_statuses=[], ha_loops=[], alerts=[], now_ts=NOW,
    )
    assert vm.stores.rules_active == 0
    assert vm.cameras == []


# ─── page rendering ───────────────────────────────────────────────


def _vm(**kw):
    base: dict = dict(  # noqa: C408
        version="0.16.0", preprocessor_ok=True,
        preprocessor_url="http://prep:8080",
        ha_connected=True, ha_entities=42,
        stores=StoresSnapshot(rules_active=3, rules_total=5, areas=4),
        cameras=[
            CameraHealthRow(camera_id="pool", name="Pool", state="running",
                            frames_read=1234, motion_events=5),
        ],
        action_runtime=ActionRuntimeStats(
            perception_pending=2, protective_recent_ok=4,
            protective_recent_gated=1, protective_recent_failed=0,
        ),
        reasoner=ReasonerStats(decisions_24h=12, alerts_24h=3,
                                dismissed_24h=9, last_decision_ts=NOW - 600),
        now_ts=NOW,
    )
    base.update(kw)
    return DiagnosticsViewModel(**base)


def test_render_diagnostics_page_includes_all_sections():
    html = render_diagnostics_page(_vm())
    for h in ("System", "Stores", "Cameras", "Action runtime",
              "Reasoner", "Legacy status"):
        assert f"<h2>{h}</h2>" in html


def test_render_diagnostics_page_shows_version_and_reachability():
    html = render_diagnostics_page(_vm(version="9.9.9"))
    assert "9.9.9" in html
    assert "reachable" in html   # prep_ok=True → 'reachable'
    assert "connected" in html   # ha_connected=True
    assert "42 entities" in html


def test_render_diagnostics_page_preprocessor_unreachable_shows_bad_state():
    html = render_diagnostics_page(_vm(preprocessor_ok=False))
    assert "unreachable" in html


def test_render_diagnostics_page_preprocessor_unknown_shows_muted():
    html = render_diagnostics_page(_vm(preprocessor_ok=None))
    assert "unknown" in html


def test_render_diagnostics_page_stores_table_shows_counts():
    html = render_diagnostics_page(_vm(stores=StoresSnapshot(
        rules_active=3, rules_total=5, areas=4,
        perception_entries=2, protective_entries=1,
        policies_dismissals=7, policies_transient_intents=2,
    )))
    assert "3</b> active" in html
    assert "5 total" in html
    assert "<b>4</b>" in html  # areas
    assert "2</b> perception" in html
    assert "1</b> protective" in html
    assert "7</b> dismissals" in html


def test_render_diagnostics_page_cameras_table_with_state_chips():
    html = render_diagnostics_page(_vm())
    assert "Pool" in html
    assert "1234" in html
    assert "5</td>" in html


def test_render_diagnostics_page_empty_cameras_shows_empty_copy():
    html = render_diagnostics_page(_vm(cameras=[]))
    assert "No cameras configured" in html


def test_render_diagnostics_page_action_runtime_stats_visible():
    html = render_diagnostics_page(_vm())
    assert "2</b> perception reverts" in html
    assert "4</b> protective ok" in html
    assert "1</b> gated" in html
    assert "0</b> failed" in html


def test_render_diagnostics_page_reasoner_shows_24h_counts():
    html = render_diagnostics_page(_vm())
    assert "12</b> VLM decisions" in html
    assert "3</b> alerted" in html
    assert "9</b> dismissed" in html


def test_render_diagnostics_page_legacy_link_present():
    html = render_diagnostics_page(_vm(legacy_status_url="/legacy"))
    assert "Open legacy status" in html
    assert "href='/legacy'" in html
