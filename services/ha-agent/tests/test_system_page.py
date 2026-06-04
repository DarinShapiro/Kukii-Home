"""/system storage + privacy (Part IX §30) — RetentionStore + page renderer
+ disk-scanner view-model builder."""

from __future__ import annotations

import sqlite3

import pytest
from kukiihome_ha_agent.retention_store import (
    DEFAULT_AUDIT_DAYS,
    DEFAULT_EVENTS_DAYS,
    DEFAULT_FRAMES_DAYS,
    AdminAudit,
    RetentionPolicy,
    RetentionStore,
)
from kukiihome_ha_agent.web_ui.system import (
    StorageClassRow,
    SystemViewModel,
    render_system_page,
)
from kukiihome_ha_agent.web_ui.system_data import build_system_vm

NOW = 1_700_000_000.0


# ─── RetentionStore ───────────────────────────────────────────────


@pytest.fixture
def store():
    s = RetentionStore(path=None)
    yield s
    s.close()


def test_get_policy_returns_defaults_on_fresh_store(store):
    p = store.get_policy()
    assert p.events_days == DEFAULT_EVENTS_DAYS
    assert p.frames_days == DEFAULT_FRAMES_DAYS
    assert p.audit_days == DEFAULT_AUDIT_DAYS


def test_update_policy_persists_partial_updates(store):
    store.update_policy(events_days=30, frames_days=7)
    p = store.get_policy()
    assert p.events_days == 30
    assert p.frames_days == 7
    # Unchanged fields preserved
    assert p.audit_days == DEFAULT_AUDIT_DAYS


def test_update_policy_clamps_below_one(store):
    store.update_policy(events_days=0, frames_days=-5)
    p = store.get_policy()
    assert p.events_days >= 1
    assert p.frames_days >= 1


def test_record_audit_returns_rowid(store):
    rid = store.record_audit(AdminAudit(
        id=None, ts=NOW, actor="alice",
        operation="erase_last_hour", scope="all", rows_removed=3,
        bytes_removed=1024,
    ))
    assert rid > 0


def test_recent_audits_returns_newest_first(store):
    for i in range(3):
        store.record_audit(AdminAudit(
            id=None, ts=NOW - i * 60, actor="alice",
            operation=f"op{i}", scope="x",
        ))
    audits = store.recent_audits(limit=10)
    assert [a.operation for a in audits] == ["op0", "op1", "op2"]


def test_recent_audits_limit_enforced(store):
    for i in range(10):
        store.record_audit(AdminAudit(
            id=None, ts=NOW + i, actor="x", operation=f"op{i}", scope="x",
        ))
    assert len(store.recent_audits(limit=5)) == 5


def test_singleton_constraint_blocks_second_policy_row(store):
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO retention_policy "
            "(id, events_days, events_max_gb, frames_days, audit_days, updated_at) "
            "VALUES (2, 10, 1, 1, 1, 0)"
        )


def test_persist_to_disk_survives_reopen(tmp_path):
    db = tmp_path / "ret.db"
    s1 = RetentionStore(path=str(db))
    s1.update_policy(events_days=42)
    s1.record_audit(AdminAudit(
        id=None, ts=NOW, actor="alice", operation="x", scope="x",
    ))
    s1.close()
    s2 = RetentionStore(path=str(db))
    assert s2.get_policy().events_days == 42
    assert len(s2.recent_audits()) == 1
    s2.close()


# ─── build_system_vm ──────────────────────────────────────────────


def test_build_vm_with_missing_data_root_returns_zero_rows(tmp_path):
    vm = build_system_vm(data_root=str(tmp_path / "absent"))
    # 4 rows always (stores / events / frames / clips); all empty
    assert len(vm.storage_rows) == 4
    assert all(r.bytes_used == 0 for r in vm.storage_rows)
    assert vm.total_bytes == 0


def test_build_vm_counts_event_json_files(tmp_path):
    root = tmp_path / "kukii"
    events_dir = root / "events" / "ev_001"
    events_dir.mkdir(parents=True)
    (events_dir / "event.json").write_text('{"x": 1}')
    (events_dir / "frame.jpg").write_bytes(b"\x00" * 10_000)

    vm = build_system_vm(data_root=str(root))
    by_label = {r.label: r for r in vm.storage_rows}
    assert by_label["Episodic events"].count == 1
    assert by_label["Frame snapshots"].count == 1
    assert by_label["Frame snapshots"].bytes_used == 10_000


def test_build_vm_counts_clip_files(tmp_path):
    root = tmp_path / "kukii"
    ev = root / "events" / "ev_001"
    ev.mkdir(parents=True)
    (ev / "clip.mp4").write_bytes(b"\x00" * 2_000)
    (ev / "clip.gif").write_bytes(b"\x00" * 1_000)
    vm = build_system_vm(data_root=str(root))
    by_label = {r.label: r for r in vm.storage_rows}
    assert by_label["Clip files"].count == 2
    assert by_label["Clip files"].bytes_used == 3_000


def test_build_vm_counts_known_store_dbs(tmp_path):
    root = tmp_path / "kukii"
    root.mkdir()
    (root / "rules.db").write_bytes(b"\x00" * 500)
    (root / "areas.db").write_bytes(b"\x00" * 200)
    vm = build_system_vm(data_root=str(root))
    by_label = {r.label: r for r in vm.storage_rows}
    assert by_label["Stores (SQLite)"].count == 2
    assert by_label["Stores (SQLite)"].bytes_used == 700


def test_build_vm_total_bytes_sums_rows(tmp_path):
    root = tmp_path / "kukii"
    root.mkdir()
    (root / "rules.db").write_bytes(b"\x00" * 100)
    vm = build_system_vm(data_root=str(root))
    assert vm.total_bytes == sum(r.bytes_used for r in vm.storage_rows)
    assert vm.total_bytes >= 100


# ─── render_system_page ───────────────────────────────────────────


def _vm(**kw):
    base: dict = dict(  # noqa: C408
        storage_rows=[
            StorageClassRow(label="Episodic events", count=12, bytes_used=10_000),
            StorageClassRow(label="Frame snapshots", count=120, bytes_used=10_000_000),
        ],
        total_bytes=10_010_000,
        policy=RetentionPolicy(),
        audit_log=[],
        cameras=[("pool", "Pool Camera"), ("front", "Front Camera")],
        now_ts=NOW,
    )
    base.update(kw)
    return SystemViewModel(**base)


def test_render_page_includes_all_sections():
    html = render_system_page(_vm())
    for heading in ("Storage usage", "Retention policy",
                    "Operations", "Admin audit log"):
        assert f"<h2>{heading}</h2>" in html


def test_render_page_storage_table_shows_rows_and_total():
    html = render_system_page(_vm())
    assert "Episodic events" in html
    assert "Frame snapshots" in html
    assert "9.5 MB" in html or "10 MB" in html  # bytes formatting


def test_render_page_no_storage_rows_shows_empty_marker():
    html = render_system_page(_vm(storage_rows=[]))
    assert "No usage data" in html


def test_render_page_retention_form_shows_current_values():
    p = RetentionPolicy(events_days=30, frames_days=7,
                         events_max_gb=5, audit_days=180)
    html = render_system_page(_vm(policy=p))
    assert "value='30'" in html
    assert "value='7'" in html
    assert "value='5'" in html
    assert "value='180'" in html
    assert "action='system/retention'" in html


def test_render_page_retention_unavailable_when_no_policy():
    html = render_system_page(_vm(policy=None))
    assert "Retention store not wired" in html


def test_render_page_operations_camera_dropdown():
    html = render_system_page(_vm())
    assert "Pool Camera" in html
    assert "value='pool'" in html
    assert "system/erase-last-hour" in html
    assert "system/purge" in html


def test_render_page_operations_camera_dropdown_empty_when_no_cameras():
    html = render_system_page(_vm(cameras=[]))
    assert "no cameras configured" in html


def test_render_page_audit_log_empty_state():
    html = render_system_page(_vm(audit_log=[]))
    assert "No admin operations recorded" in html


def test_render_page_audit_log_lists_recent_operations():
    audits = [
        AdminAudit(id=1, ts=NOW - 600, actor="alice",
                    operation="erase_last_hour", scope="all",
                    rows_removed=5, bytes_removed=10_000),
        AdminAudit(id=2, ts=NOW - 1200, actor="bob",
                    operation="retention.policy.updated",
                    scope="global"),
    ]
    html = render_system_page(_vm(audit_log=audits))
    assert "erase_last_hour" in html
    assert "retention.policy.updated" in html
    assert "alice" in html
    assert "bob" in html
    assert "5 rows" in html


def test_render_page_html_escapes_audit_fields():
    a = AdminAudit(id=1, ts=NOW, actor="<bad>", operation="<op>",
                    scope="<scope>")
    html = render_system_page(_vm(audit_log=[a]))
    assert "<bad>" not in html
    assert "&lt;bad&gt;" in html


def test_render_page_byte_formatting_progresses_through_units():
    from kukiihome_ha_agent.web_ui.system import _format_bytes
    assert _format_bytes(500) == "500 B"
    assert "KB" in _format_bytes(2 * 1024)
    assert "MB" in _format_bytes(5 * 1024 ** 2)
    assert "GB" in _format_bytes(3 * 1024 ** 3)
