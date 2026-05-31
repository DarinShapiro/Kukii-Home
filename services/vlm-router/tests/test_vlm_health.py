"""Tests for the F7 (VLM down) health probe."""

from __future__ import annotations

import pytest
from kukiihome_shared.health import FailureMode
from kukiihome_vlm_router.breaker import CircuitBreaker
from kukiihome_vlm_router.vlm_health import make_vlm_health_check, probe_vlm_health


def _open() -> CircuitBreaker:
    b = CircuitBreaker(failure_threshold=1)
    b.record_failure()  # -> OPEN
    return b


def _closed() -> CircuitBreaker:
    return CircuitBreaker()


def test_no_backends_is_offline():
    h = probe_vlm_health({}, now=0.0)
    assert h.status == "offline"
    assert h.component == "vlm_router"


def test_all_closed_is_ok():
    h = probe_vlm_health({"ollama": _closed(), "cloud": _closed()}, now=0.0)
    assert h.status == "ok"


def test_all_open_is_offline():
    h = probe_vlm_health({"ollama": _open(), "cloud": _open()}, now=0.0)
    assert h.status == "offline"
    assert "detector-only" in h.detail


def test_some_open_is_degraded():
    h = probe_vlm_health({"ollama": _open(), "cloud": _closed()}, now=0.0)
    assert h.status == "degraded"
    assert "ollama" in h.detail


@pytest.mark.asyncio
async def test_make_check_declares_f7_and_reads_provider_object():
    class _Router:
        def __init__(self) -> None:
            self.breakers = {"ollama": _open()}

    check = make_vlm_health_check(_Router(), clock=lambda: 0.0)
    assert check.failure_mode == FailureMode.F7_VLM_DOWN
    assert check.name == "vlm_router"
    health = await check.probe()
    assert health.status == "offline"


@pytest.mark.asyncio
async def test_make_check_accepts_bare_mapping():
    check = make_vlm_health_check({"a": _closed()}, clock=lambda: 0.0)
    health = await check.probe()
    assert health.status == "ok"
