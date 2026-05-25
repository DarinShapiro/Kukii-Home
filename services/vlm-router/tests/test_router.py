"""Tests for the Router end-to-end with fake backends.

Verifies fallback chain execution, breaker integration, telemetry, and
privacy-violation handling.
"""

from __future__ import annotations

import pytest
from sentihome_shared.generated.events.vlm_request import VLMRequest
from sentihome_shared.generated.events.vlm_response import VLMResponse
from sentihome_vlm_router.backends import Backend, BackendCapability, BackendConfig
from sentihome_vlm_router.errors import AllBackendsFailedError, BackendError, PrivacyViolationError
from sentihome_vlm_router.router import Router, RouterConfig

pytestmark = pytest.mark.asyncio


class _ScriptedBackend(Backend):
    """Backend that returns / fails according to a scripted sequence."""

    def __init__(
        self,
        *,
        name: str,
        location: str = "local",
        results: list[BaseException | VLMResponse],
    ) -> None:
        super().__init__(
            BackendConfig(
                name=name,
                location=location,
                model_name="scripted",
                base_url="http://scripted/",
            )
        )
        self._results = list(results)
        self.invoke_count = 0

    def capability(self) -> BackendCapability:
        return BackendCapability(
            name=self.name,
            location=self.location,
            model_name="scripted",
            supports_vision=True,
            max_frames_per_call=16,
            typical_latency_ms=100,
            cost_per_1k_tokens_usd=0.0,
        )

    async def invoke(self, request: VLMRequest) -> VLMResponse:
        self.invoke_count += 1
        if not self._results:
            raise BackendError(self.name, "no scripted results remaining")
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _ok_response(*, backend: str = "scripted") -> VLMResponse:
    return VLMResponse(
        request_id="req_1",
        event_id="evt_1",
        criticality="info",
        confidence=0.85,
        backend=backend,
        latency_ms=42,
        tokens_used=120,
    )


def _request(privacy_tier: str = "cloud_eligible", frames: int = 1) -> VLMRequest:
    return VLMRequest(
        request_id="req_1",
        event_id="evt_1",
        frames=tuple(f"file://f{i}" for i in range(frames)),
        prompt="x",
        privacy_tier=privacy_tier,  # type: ignore[arg-type]
    )


# ─────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────


async def test_router_returns_first_backend_response() -> None:
    b1 = _ScriptedBackend(name="b1", results=[_ok_response(backend="b1")])
    b2 = _ScriptedBackend(name="b2", results=[_ok_response(backend="b2")])
    router = Router([b1, b2])
    response = await router.invoke(_request())
    assert response.backend == "b1"
    assert b1.invoke_count == 1
    assert b2.invoke_count == 0


# ─────────────────────────────────────────────────────────────────────
# Fallback chain
# ─────────────────────────────────────────────────────────────────────


async def test_router_falls_back_on_backend_error() -> None:
    b1 = _ScriptedBackend(name="b1", results=[BackendError("b1", "boom")])
    b2 = _ScriptedBackend(name="b2", results=[_ok_response(backend="b2")])
    router = Router([b1, b2])
    response = await router.invoke(_request())
    assert response.backend == "b2"
    assert b1.invoke_count == 1
    assert b2.invoke_count == 1


async def test_router_raises_when_all_backends_fail() -> None:
    b1 = _ScriptedBackend(name="b1", results=[BackendError("b1", "boom")])
    b2 = _ScriptedBackend(name="b2", results=[BackendError("b2", "splat")])
    router = Router([b1, b2])
    with pytest.raises(AllBackendsFailedError) as exc_info:
        await router.invoke(_request())
    assert len(exc_info.value.attempts) == 2


async def test_router_respects_max_fallback_attempts() -> None:
    b1 = _ScriptedBackend(name="b1", results=[BackendError("b1", "x")])
    b2 = _ScriptedBackend(name="b2", results=[BackendError("b2", "x")])
    b3 = _ScriptedBackend(name="b3", results=[_ok_response(backend="b3")])
    # Limit to 2 attempts; b3 won't be tried
    router = Router([b1, b2, b3], config=RouterConfig(max_fallback_attempts=2))
    with pytest.raises(AllBackendsFailedError):
        await router.invoke(_request())
    assert b3.invoke_count == 0


# ─────────────────────────────────────────────────────────────────────
# Circuit breaker integration
# ─────────────────────────────────────────────────────────────────────


async def test_breaker_opens_after_repeated_failures() -> None:
    b1 = _ScriptedBackend(name="b1", results=[BackendError("b1", "x"), BackendError("b1", "x")])
    b2 = _ScriptedBackend(
        name="b2",
        results=[
            _ok_response(backend="b2"),
            _ok_response(backend="b2"),
            _ok_response(backend="b2"),
        ],
    )
    router = Router(
        [b1, b2],
        config=RouterConfig(failure_threshold=2, breaker_reset_seconds=60),
    )

    # First call: b1 fails, b2 succeeds
    await router.invoke(_request())
    # Second call: b1 fails again (threshold=2 reached → OPEN), b2 succeeds
    await router.invoke(_request())
    # Third call: b1 breaker is OPEN, policy skips it → goes straight to b2
    await router.invoke(_request())

    assert b1.invoke_count == 2  # not called the 3rd time
    assert b2.invoke_count == 3


# ─────────────────────────────────────────────────────────────────────
# Privacy enforcement
# ─────────────────────────────────────────────────────────────────────


async def test_local_only_with_only_cloud_backend_raises_privacy() -> None:
    cloud = _ScriptedBackend(name="cloud-1", location="cloud", results=[_ok_response()])
    router = Router([cloud])
    with pytest.raises(PrivacyViolationError):
        await router.invoke(_request(privacy_tier="local_only"))
    assert cloud.invoke_count == 0


async def test_local_only_routes_to_local_only() -> None:
    local = _ScriptedBackend(
        name="local-1", location="local", results=[_ok_response(backend="local-1")]
    )
    cloud = _ScriptedBackend(
        name="cloud-1", location="cloud", results=[_ok_response(backend="cloud-1")]
    )
    router = Router([local, cloud])
    response = await router.invoke(_request(privacy_tier="local_only"))
    assert response.backend == "local-1"
    assert cloud.invoke_count == 0


# ─────────────────────────────────────────────────────────────────────
# Telemetry
# ─────────────────────────────────────────────────────────────────────


async def test_telemetry_records_success() -> None:
    b1 = _ScriptedBackend(name="b1", results=[_ok_response(backend="b1")])
    router = Router([b1])
    await router.invoke(_request())
    snap = router.telemetry("b1").snapshot()  # type: ignore[union-attr]
    assert snap["request_count"] == 1
    assert snap["success_rate"] == 1.0


async def test_telemetry_records_failures() -> None:
    b1 = _ScriptedBackend(name="b1", results=[BackendError("b1", "x")])
    b2 = _ScriptedBackend(name="b2", results=[_ok_response(backend="b2")])
    router = Router([b1, b2])
    await router.invoke(_request())
    snap1 = router.telemetry("b1").snapshot()  # type: ignore[union-attr]
    snap2 = router.telemetry("b2").snapshot()  # type: ignore[union-attr]
    assert snap1["request_count"] == 1
    assert snap1["success_rate"] == 0.0
    assert snap2["success_rate"] == 1.0
