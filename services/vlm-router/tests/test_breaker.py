"""Tests for the per-backend circuit breaker."""

from __future__ import annotations

import time

from sentihome_vlm_router.breaker import CircuitBreaker, CircuitState


def test_starts_closed() -> None:
    cb = CircuitBreaker()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_attempt() is True


def test_opens_after_threshold_failures() -> None:
    cb = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.can_attempt() is False


def test_failures_below_threshold_stay_closed() -> None:
    cb = CircuitBreaker(failure_threshold=5)
    for _ in range(4):
        cb.record_failure()
    assert cb.state == CircuitState.CLOSED


def test_success_resets_failure_counter() -> None:
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # still under threshold


def test_open_transitions_to_half_open_after_reset() -> None:
    cb = CircuitBreaker(failure_threshold=1, reset_seconds=0.05)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.1)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.can_attempt() is True


def test_half_open_success_returns_to_closed() -> None:
    cb = CircuitBreaker(failure_threshold=1, reset_seconds=0.05)
    cb.record_failure()
    time.sleep(0.1)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_failure_returns_to_open() -> None:
    cb = CircuitBreaker(failure_threshold=1, reset_seconds=0.05)
    cb.record_failure()
    time.sleep(0.1)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_reset_clears_state() -> None:
    cb = CircuitBreaker(failure_threshold=1)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_attempt() is True
