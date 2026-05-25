"""Tests for the routing policy engine.

We use lightweight fake backends instead of real Ollama/Cloud to keep tests
fast and deterministic.
"""

from __future__ import annotations

import pytest
from sentihome_shared.generated.events.vlm_request import VLMRequest
from sentihome_vlm_router.backends import Backend, BackendCapability, BackendConfig
from sentihome_vlm_router.breaker import CircuitBreaker
from sentihome_vlm_router.errors import PrivacyViolationError
from sentihome_vlm_router.policy import RoutingPolicy


class _FakeBackend(Backend):
    """In-memory backend used only for policy tests."""

    def __init__(
        self,
        *,
        name: str,
        location: str = "local",
        latency: int = 1000,
        cost: float = 0.0,
        supports_vision: bool = True,
        max_frames: int = 8,
    ) -> None:
        super().__init__(
            BackendConfig(
                name=name,
                location=location,
                model_name=f"{name}-model",
                base_url="http://fake/",
                cost_per_1k_tokens_usd=cost,
                typical_latency_ms=latency,
            )
        )
        self._supports_vision = supports_vision
        self._max_frames = max_frames

    def capability(self) -> BackendCapability:
        return BackendCapability(
            name=self.name,
            location=self.location,
            model_name=f"{self.name}-model",
            supports_vision=self._supports_vision,
            max_frames_per_call=self._max_frames,
            typical_latency_ms=self._config.typical_latency_ms,
            cost_per_1k_tokens_usd=self._config.cost_per_1k_tokens_usd,
        )

    async def invoke(self, request):  # type: ignore[no-untyped-def]
        raise NotImplementedError  # tests don't actually invoke


def _request(
    *,
    privacy_tier: str = "cloud_eligible",
    frames: int = 2,
    preferred: str | None = None,
    max_latency_ms: int | None = None,
) -> VLMRequest:
    return VLMRequest(
        request_id="req_1",
        event_id="evt_1",
        frames=tuple(f"file://frame_{i}" for i in range(frames)),
        prompt="describe this",
        privacy_tier=privacy_tier,  # type: ignore[arg-type]
        preferred_backend=preferred,
        max_latency_ms=max_latency_ms,
    )


# ─────────────────────────────────────────────────────────────────────
# Privacy tier enforcement
# ─────────────────────────────────────────────────────────────────────


def test_local_only_excludes_cloud_backends() -> None:
    policy = RoutingPolicy()
    backends = [
        _FakeBackend(name="local-1", location="local"),
        _FakeBackend(name="cloud-1", location="cloud", cost=0.01),
    ]
    breakers = {b.name: CircuitBreaker() for b in backends}

    decision = policy.decide(
        request=_request(privacy_tier="local_only"),
        backends=backends,
        breakers=breakers,
    )
    assert "cloud-1" not in decision.chain
    assert "local-1" in decision.chain


def test_local_only_with_no_local_backend_raises_privacy_error() -> None:
    policy = RoutingPolicy()
    backends = [_FakeBackend(name="cloud-1", location="cloud")]
    breakers = {b.name: CircuitBreaker() for b in backends}

    with pytest.raises(PrivacyViolationError):
        policy.decide(
            request=_request(privacy_tier="local_only"),
            backends=backends,
            breakers=breakers,
        )


def test_cloud_any_allows_both() -> None:
    policy = RoutingPolicy()
    backends = [
        _FakeBackend(name="local-1", location="local"),
        _FakeBackend(name="cloud-1", location="cloud"),
    ]
    breakers = {b.name: CircuitBreaker() for b in backends}

    decision = policy.decide(
        request=_request(privacy_tier="cloud_any"),
        backends=backends,
        breakers=breakers,
    )
    assert "local-1" in decision.chain
    assert "cloud-1" in decision.chain


# ─────────────────────────────────────────────────────────────────────
# Affinity (preferred backend)
# ─────────────────────────────────────────────────────────────────────


def test_preferred_backend_ranks_first() -> None:
    policy = RoutingPolicy()
    backends = [
        _FakeBackend(name="fast-local", location="local", latency=500),
        _FakeBackend(name="slow-cloud", location="cloud", latency=3000, cost=0.01),
    ]
    breakers = {b.name: CircuitBreaker() for b in backends}

    decision = policy.decide(
        request=_request(preferred="slow-cloud", privacy_tier="cloud_any"),
        backends=backends,
        breakers=breakers,
    )
    assert decision.chain[0] == "slow-cloud"


# ─────────────────────────────────────────────────────────────────────
# Cost + latency scoring
# ─────────────────────────────────────────────────────────────────────


def test_local_before_cloud_when_latency_comparable() -> None:
    """With similar latencies, local should win by the location bonus + free cost."""
    policy = RoutingPolicy()
    backends = [
        _FakeBackend(name="cloud-1", location="cloud", latency=1000, cost=0.01),
        _FakeBackend(name="local-1", location="local", latency=1000),
    ]
    breakers = {b.name: CircuitBreaker() for b in backends}

    decision = policy.decide(
        request=_request(privacy_tier="cloud_any"),
        backends=backends,
        breakers=breakers,
    )
    assert decision.chain[0] == "local-1"


def test_fast_cloud_can_beat_slow_local() -> None:
    """When cloud is much faster, latency penalty overcomes the location bonus."""
    policy = RoutingPolicy()
    backends = [
        _FakeBackend(name="fast-cloud", location="cloud", latency=200, cost=0.0),
        _FakeBackend(name="slow-local", location="local", latency=5000),
    ]
    breakers = {b.name: CircuitBreaker() for b in backends}

    decision = policy.decide(
        request=_request(privacy_tier="cloud_any"),
        backends=backends,
        breakers=breakers,
    )
    # 5000/100 - 10 = 40 vs 200/100 + 0 = 2 → fast-cloud wins
    assert decision.chain[0] == "fast-cloud"


# ─────────────────────────────────────────────────────────────────────
# Circuit breaker
# ─────────────────────────────────────────────────────────────────────


def test_open_breaker_excludes_backend() -> None:
    policy = RoutingPolicy()
    backends = [
        _FakeBackend(name="open-1", location="local"),
        _FakeBackend(name="ok-1", location="local"),
    ]
    breakers = {b.name: CircuitBreaker(failure_threshold=1) for b in backends}
    # Open the breaker on the first backend
    breakers["open-1"].record_failure()

    decision = policy.decide(
        request=_request(),
        backends=backends,
        breakers=breakers,
    )
    assert "open-1" not in decision.chain
    assert "ok-1" in decision.chain


# ─────────────────────────────────────────────────────────────────────
# Capability filtering
# ─────────────────────────────────────────────────────────────────────


def test_too_many_frames_excludes_backend() -> None:
    policy = RoutingPolicy()
    backends = [
        _FakeBackend(name="small", max_frames=2),
        _FakeBackend(name="big", max_frames=16),
    ]
    breakers = {b.name: CircuitBreaker() for b in backends}

    decision = policy.decide(
        request=_request(frames=8),
        backends=backends,
        breakers=breakers,
    )
    assert "small" not in decision.chain
    assert "big" in decision.chain


def test_no_vision_support_excludes_backend() -> None:
    policy = RoutingPolicy()
    backends = [
        _FakeBackend(name="text-only", supports_vision=False),
        _FakeBackend(name="vision-ok"),
    ]
    breakers = {b.name: CircuitBreaker() for b in backends}

    decision = policy.decide(
        request=_request(frames=1),
        backends=backends,
        breakers=breakers,
    )
    assert "text-only" not in decision.chain
