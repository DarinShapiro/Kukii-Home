"""ActionStore — whitelist CRUD + gate decision + audit log."""

from __future__ import annotations

from datetime import datetime

import pytest
from kukiihome_ha_agent.action_store import (
    ActionStore,
    PerceptionEntry,
    ProtectiveEntry,
    ProtectiveLogRow,
    _severity_meets,
    gate_recommendation,
)


@pytest.fixture
def store():
    s = ActionStore(path=None)
    yield s
    s.close()


# ─── severity gate primitive ──────────────────────────────────────────


def test_severity_meets_canonical_rankings():
    assert _severity_meets("critical", "normal") is True
    assert _severity_meets("normal", "critical") is False
    assert _severity_meets("critical", "critical") is True
    assert _severity_meets("low", "any") is True


def test_severity_meets_unknown_actual_fails_closed():
    assert _severity_meets(None, "any") is False
    assert _severity_meets("nonsense", "critical") is False


# ─── perception whitelist CRUD ────────────────────────────────────────


def test_perception_upsert_then_read(store):
    e = PerceptionEntry(
        camera_id="front",
        target_kind="ha_service",
        target="light.turn_on:light.porch",
        max_duration_s=120,
    )
    store.upsert_perception(e)
    out = store.perception_for("front")
    assert len(out) == 1
    assert out[0].target == "light.turn_on:light.porch"
    assert out[0].max_duration_s == 120


def test_perception_upsert_overwrites(store):
    e = PerceptionEntry(
        camera_id="front",
        target_kind="camera_api",
        target="ptz_zoom",
        max_duration_s=30,
    )
    store.upsert_perception(e)
    # Same primary key (camera, kind, target) → update existing row
    store.upsert_perception(
        PerceptionEntry(
            camera_id="front", target_kind="camera_api", target="ptz_zoom", max_duration_s=60
        )
    )
    out = store.perception_for("front")
    assert len(out) == 1
    assert out[0].max_duration_s == 60


def test_perception_disabled_excluded_from_read(store):
    store.upsert_perception(
        PerceptionEntry(
            camera_id="front",
            target_kind="camera_api",
            target="ptz_zoom",
            enabled=False,
        )
    )
    assert store.perception_for("front") == []


def test_perception_delete(store):
    store.upsert_perception(
        PerceptionEntry(
            camera_id="front",
            target_kind="ha_service",
            target="x",
        )
    )
    store.delete_perception("front", "ha_service", "x")
    assert store.perception_for("front") == []


# ─── protective whitelist CRUD ────────────────────────────────────────


def _protective(**kw):
    base: dict = dict(  # noqa: C408
        camera_id="backyard",
        action_class="lock",
        service="lock.lock",
        target="lock.back_door",
        min_severity="critical",
        min_confidence=0.8,
    )
    base.update(kw)
    return ProtectiveEntry(**base)


def test_protective_upsert_then_read(store):
    store.upsert_protective(_protective())
    out = store.protective_for("backyard")
    assert len(out) == 1
    assert out[0].action_class == "lock"
    assert out[0].min_severity == "critical"


def test_protective_blackout_roundtrip_as_json(store):
    e = _protective(
        blackout_windows=[
            {"days": ["mon"], "start": "08:00", "end": "20:00"},
        ]
    )
    store.upsert_protective(e)
    out = store.protective_for("backyard")[0]
    assert out.blackout_windows == [
        {"days": ["mon"], "start": "08:00", "end": "20:00"},
    ]


def test_protective_find_exact_match(store):
    store.upsert_protective(_protective())
    hit = store.find_protective(
        camera_id="backyard",
        service="lock.lock",
        target="lock.back_door",
        action_class="lock",
    )
    assert hit is not None
    miss = store.find_protective(
        camera_id="backyard",
        service="lock.lock",
        target="lock.front_door",
        action_class="lock",
    )
    assert miss is None


def test_protective_delete(store):
    store.upsert_protective(_protective())
    store.delete_protective("backyard", "lock", "lock.lock", "lock.back_door")
    assert store.protective_for("backyard") == []


# ─── gate_recommendation policy ───────────────────────────────────────


def test_gate_rejects_unscoped(store):
    decision = gate_recommendation(
        store=store,
        camera_id=None,
        action_class="lock",
        service="lock.lock",
        target="lock.x",
        severity="critical",
        confidence=0.99,
    )
    assert decision.execute is False
    assert decision.reason == "no_camera_scope"


def test_gate_rejects_missing_whitelist(store):
    decision = gate_recommendation(
        store=store,
        camera_id="any",
        action_class="lock",
        service="lock.lock",
        target="lock.x",
        severity="critical",
        confidence=0.99,
    )
    assert decision.execute is False
    assert decision.reason == "no_authorization"


def test_gate_passes_when_thresholds_met(store):
    store.upsert_protective(_protective())
    decision = gate_recommendation(
        store=store,
        camera_id="backyard",
        action_class="lock",
        service="lock.lock",
        target="lock.back_door",
        severity="critical",
        confidence=0.9,
    )
    assert decision.execute is True
    assert decision.matched_entry is not None


def test_gate_blocks_low_severity(store):
    store.upsert_protective(_protective(min_severity="critical"))
    decision = gate_recommendation(
        store=store,
        camera_id="backyard",
        action_class="lock",
        service="lock.lock",
        target="lock.back_door",
        severity="normal",
        confidence=0.95,
    )
    assert decision.execute is False
    assert decision.reason == "severity_below_threshold"


def test_gate_blocks_low_confidence(store):
    store.upsert_protective(_protective(min_confidence=0.9))
    decision = gate_recommendation(
        store=store,
        camera_id="backyard",
        action_class="lock",
        service="lock.lock",
        target="lock.back_door",
        severity="critical",
        confidence=0.5,
    )
    assert decision.execute is False
    assert decision.reason == "confidence_below_threshold"


def test_gate_blocks_in_blackout_window(store):
    # 2026-06-01 Monday 12:00 in the blackout window 09:00-17:00
    when = datetime(2026, 6, 1, 12, 0, 0).timestamp()
    store.upsert_protective(
        _protective(
            blackout_windows=[
                {"days": ["mon"], "start": "09:00", "end": "17:00"},
            ]
        )
    )
    decision = gate_recommendation(
        store=store,
        camera_id="backyard",
        action_class="lock",
        service="lock.lock",
        target="lock.back_door",
        severity="critical",
        confidence=0.95,
        now_ts=when,
    )
    assert decision.execute is False
    assert decision.reason == "blackout_window"


def test_gate_passes_outside_blackout(store):
    # 2026-06-01 Monday 19:00 outside the window
    when = datetime(2026, 6, 1, 19, 0, 0).timestamp()
    store.upsert_protective(
        _protective(
            blackout_windows=[
                {"days": ["mon"], "start": "09:00", "end": "17:00"},
            ]
        )
    )
    decision = gate_recommendation(
        store=store,
        camera_id="backyard",
        action_class="lock",
        service="lock.lock",
        target="lock.back_door",
        severity="critical",
        confidence=0.95,
        now_ts=when,
    )
    assert decision.execute is True


# ─── audit log ────────────────────────────────────────────────────────


def test_log_protective_assigns_id(store):
    row = ProtectiveLogRow(
        incident_id="i1",
        camera_id="backyard",
        ts=100.0,
        action_class="lock",
        service="lock.lock",
        target="lock.x",
        data_json=None,
        status="ok",
    )
    log_id = store.log_protective(row)
    assert log_id > 0


def test_log_for_incident_returns_chrono_order(store):
    for i, ts in enumerate([200, 100, 300]):
        store.log_protective(
            ProtectiveLogRow(
                incident_id="inc99",
                camera_id="c",
                ts=ts,
                action_class="lock",
                service="lock.lock",
                target=f"lock.t{i}",
                data_json=None,
                status="ok",
            )
        )
    rows = store.log_for_incident("inc99")
    # ORDER BY ts ASC
    assert [r.ts for r in rows] == [100, 200, 300]


def test_recent_log_newest_first(store):
    for ts in [100.0, 200.0, 300.0]:
        store.log_protective(
            ProtectiveLogRow(
                incident_id="i",
                camera_id="c",
                ts=ts,
                action_class="lock",
                service="lock.lock",
                target="x",
                data_json=None,
                status="ok",
            )
        )
    rows = store.recent_log(limit=2)
    assert [r.ts for r in rows] == [300.0, 200.0]


def test_persist_to_disk_survives_reopen(tmp_path):
    db = tmp_path / "actions.db"
    s1 = ActionStore(path=str(db))
    s1.upsert_perception(
        PerceptionEntry(
            camera_id="front",
            target_kind="ha_service",
            target="x",
        )
    )
    s1.upsert_protective(_protective())
    s1.close()

    s2 = ActionStore(path=str(db))
    assert s2.perception_for("front") != []
    assert s2.protective_for("backyard") != []
    s2.close()
