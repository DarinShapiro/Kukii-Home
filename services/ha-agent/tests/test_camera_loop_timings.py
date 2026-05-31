"""Tests for the per-alert latency-waypoint computation (v0.3.19)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kukiihome_ha_agent.camera_loop import _compute_timings


def test_computes_all_legs_when_ha_timestamp_present():
    ha_t = datetime(2026, 5, 27, 14, 23, 0, tzinfo=UTC)
    received = ha_t + timedelta(milliseconds=80)
    snap_start = received + timedelta(milliseconds=2)
    snap_done = snap_start + timedelta(milliseconds=500)

    t = _compute_timings(
        ha_last_changed=ha_t.isoformat(),
        received_at=received,
        snapshot_started_at=snap_start,
        snapshot_completed_at=snap_done,
    )

    # HA → us: 80ms (WebSocket / process lag)
    assert t["ha_to_received_ms"] == 80.0
    # Handler overhead: 2ms (debounce + plumbing)
    assert t["handler_to_snapshot_start_ms"] == 2.0
    # Snapshot fetch: 500ms (camera HTTP round-trip)
    assert t["snapshot_duration_ms"] == 500.0
    # Total HA → snapshot in hand: 582ms
    assert t["ha_to_snapshot_complete_ms"] == 582.0


def test_omits_ha_legs_when_no_last_changed():
    """HA event without last_changed (rare) → we can still compute
    the parts we can measure ourselves, just not the HA-relative legs."""
    now = datetime(2026, 5, 27, 14, 23, 0, tzinfo=UTC)
    t = _compute_timings(
        ha_last_changed=None,
        received_at=now,
        snapshot_started_at=now + timedelta(milliseconds=1),
        snapshot_completed_at=now + timedelta(milliseconds=300),
    )
    assert t["ha_to_received_ms"] is None
    assert t["ha_to_snapshot_complete_ms"] is None
    assert t["handler_to_snapshot_start_ms"] == 1.0
    assert t["snapshot_duration_ms"] == 299.0


def test_handles_malformed_ha_timestamp():
    """Defensive: HA returning a weird string shouldn't crash the
    alert recording path."""
    now = datetime(2026, 5, 27, 14, 23, 0, tzinfo=UTC)
    t = _compute_timings(
        ha_last_changed="not-a-real-timestamp",
        received_at=now,
        snapshot_started_at=now + timedelta(milliseconds=1),
        snapshot_completed_at=now + timedelta(milliseconds=300),
    )
    # HA-relative legs stay None, the rest are still computed.
    assert t["ha_to_received_ms"] is None
    assert t["ha_to_snapshot_complete_ms"] is None
    assert t["handler_to_snapshot_start_ms"] == 1.0


def test_high_latency_scenario():
    """The stale-snapshot case: HA integration delay of 3 seconds +
    slow snapshot fetch of 800ms = 3.8s total. UI should flag this red."""
    ha_t = datetime(2026, 5, 27, 14, 23, 0, tzinfo=UTC)
    received = ha_t + timedelta(seconds=3)  # huge HA → us lag
    snap_done = received + timedelta(milliseconds=800)
    t = _compute_timings(
        ha_last_changed=ha_t.isoformat(),
        received_at=received,
        snapshot_started_at=received,
        snapshot_completed_at=snap_done,
    )
    assert t["ha_to_received_ms"] == 3000.0
    assert t["snapshot_duration_ms"] == 800.0
    assert t["ha_to_snapshot_complete_ms"] == 3800.0
