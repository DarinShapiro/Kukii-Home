"""Tests for the per-backend telemetry rolling window."""

from __future__ import annotations

from kukiihome_vlm_router.telemetry import BackendTelemetry


def test_empty_telemetry_defaults_clean() -> None:
    t = BackendTelemetry(backend_name="x")
    assert t.request_count == 0
    assert t.success_rate == 1.0
    assert t.p50_latency_ms == 0
    assert t.total_cost_usd == 0.0


def test_records_successes_and_failures() -> None:
    t = BackendTelemetry(backend_name="x")
    t.record(success=True, latency_ms=100)
    t.record(success=True, latency_ms=200)
    t.record(success=False, latency_ms=500)
    assert t.request_count == 3
    assert t.success_count == 2
    assert t.success_rate == 2 / 3


def test_latency_percentiles() -> None:
    t = BackendTelemetry(backend_name="x")
    for ms in [50, 100, 150, 200, 1000]:
        t.record(success=True, latency_ms=ms)
    # With 5 data points, p50 ≈ index 2 (150ms), p95 ≈ index 4 (1000ms)
    assert t.p50_latency_ms == 150
    assert t.p95_latency_ms == 1000


def test_rolling_window_caps_records() -> None:
    t = BackendTelemetry(backend_name="x", window_size=5)
    for _ in range(20):
        t.record(success=True, latency_ms=10)
    assert t.request_count == 5  # window cap


def test_total_cost_and_tokens_aggregated() -> None:
    t = BackendTelemetry(backend_name="x")
    t.record(success=True, latency_ms=1, tokens_used=100, cost_usd=0.01)
    t.record(success=True, latency_ms=1, tokens_used=200, cost_usd=0.02)
    assert t.total_tokens == 300
    assert abs(t.total_cost_usd - 0.03) < 1e-9


def test_snapshot_has_all_fields() -> None:
    t = BackendTelemetry(backend_name="x")
    t.record(success=True, latency_ms=100, tokens_used=50, cost_usd=0.005)
    snap = t.snapshot()
    assert snap["backend_name"] == "x"
    assert snap["request_count"] == 1
    assert "success_rate" in snap
    assert "p50_latency_ms" in snap
    assert "p95_latency_ms" in snap
