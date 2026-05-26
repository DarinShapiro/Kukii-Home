"""Router — the public entry point.

Holds the backend registry, circuit breakers, telemetry, and routing policy.
The ``invoke()`` method runs the full pipeline: policy → fallback chain →
backend call → telemetry → response.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from sentihome_vlm_router.backends import (
    Backend,
    BackendConfig,
    CloudBackend,
    OllamaBackend,
    VLLMBackend,
)
from sentihome_vlm_router.breaker import CircuitBreaker
from sentihome_vlm_router.errors import (
    AllBackendsFailedError,
    BackendError,
)
from sentihome_vlm_router.policy import RoutingPolicy
from sentihome_vlm_router.telemetry import BackendTelemetry

if TYPE_CHECKING:
    from sentihome_shared.generated.events.vlm_request import VLMRequest
    from sentihome_shared.generated.events.vlm_response import VLMResponse

logger = structlog.get_logger(__name__)


@dataclass
class RouterConfig:
    """Router-wide configuration."""

    failure_threshold: int = 5
    """Consecutive failures before a backend's breaker opens."""

    breaker_reset_seconds: float = 30.0
    """How long an OPEN breaker stays open before HALF_OPEN probe."""

    max_fallback_attempts: int = 3
    """How many backends the router will try per request before giving up."""


class Router:
    """Multi-backend VLM router.

    Construct with a list of ``Backend`` instances. The router owns the
    per-backend circuit breakers + telemetry. Call ``invoke(request)`` to
    execute a VLM request through the policy + fallback chain.
    """

    def __init__(
        self,
        backends: list[Backend],
        *,
        config: RouterConfig | None = None,
        policy: RoutingPolicy | None = None,
    ) -> None:
        self._config = config or RouterConfig()
        self._policy = policy or RoutingPolicy()
        self._backends: dict[str, Backend] = {b.name: b for b in backends}
        self._breakers: dict[str, CircuitBreaker] = {
            b.name: CircuitBreaker(
                failure_threshold=self._config.failure_threshold,
                reset_seconds=self._config.breaker_reset_seconds,
            )
            for b in backends
        }
        self._telemetry: dict[str, BackendTelemetry] = {
            b.name: BackendTelemetry(backend_name=b.name) for b in backends
        }

    @property
    def backends(self) -> list[Backend]:
        return list(self._backends.values())

    @property
    def breakers(self) -> dict[str, CircuitBreaker]:
        return dict(self._breakers)

    def telemetry(
        self, backend_name: str | None = None
    ) -> dict[str, BackendTelemetry] | BackendTelemetry:
        if backend_name is not None:
            return self._telemetry[backend_name]
        return dict(self._telemetry)

    async def invoke(self, request: VLMRequest) -> VLMResponse:
        """Execute one VLM request, falling back through eligible backends."""
        decision = self._policy.decide(
            request=request,
            backends=list(self._backends.values()),
            breakers=self._breakers,
        )

        if not decision.chain:
            raise AllBackendsFailedError(
                attempts=[(name, "ineligible") for name in decision.reasons]
            )

        attempts: list[tuple[str, str]] = []
        for backend_name in decision.chain[: self._config.max_fallback_attempts]:
            backend = self._backends[backend_name]
            breaker = self._breakers[backend_name]
            telemetry = self._telemetry[backend_name]

            start = time.monotonic()
            try:
                response = await backend.invoke(request)
            except BackendError as e:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                breaker.record_failure()
                telemetry.record(success=False, latency_ms=elapsed_ms)
                attempts.append((backend_name, str(e)))
                logger.warning(
                    "router.backend_failed",
                    backend=backend_name,
                    error=str(e),
                    breaker_state=breaker.state.value,
                )
                continue
            except Exception as e:
                # Defensive: any other exception is treated as a failure too
                elapsed_ms = int((time.monotonic() - start) * 1000)
                breaker.record_failure()
                telemetry.record(success=False, latency_ms=elapsed_ms)
                attempts.append((backend_name, repr(e)))
                logger.exception(
                    "router.backend_unexpected_error",
                    backend=backend_name,
                )
                continue

            elapsed_ms = int((time.monotonic() - start) * 1000)
            breaker.record_success()
            cap = backend.capability()
            cost = (
                (response.tokens_used or 0) / 1000.0 * cap.cost_per_1k_tokens_usd
                if response.tokens_used is not None
                else None
            )
            telemetry.record(
                success=True,
                latency_ms=response.latency_ms or elapsed_ms,
                tokens_used=response.tokens_used,
                cost_usd=cost,
            )
            logger.info(
                "router.success",
                backend=backend_name,
                latency_ms=response.latency_ms,
                tokens_used=response.tokens_used,
            )
            return response

        raise AllBackendsFailedError(attempts=attempts)

    async def close(self) -> None:
        """Close all backend HTTP clients."""
        for backend in self._backends.values():
            await backend.close()


# ─────────────────────────────────────────────────────────────────────
# Construction helpers
# ─────────────────────────────────────────────────────────────────────


def build_backend(config: BackendConfig) -> Backend:
    """Construct a backend instance from config.

    When ``config.kind_hint`` is set (topology path) it picks the driver
    directly. Otherwise falls back to URL-shape sniffing for legacy callers.
    """
    if config.kind_hint == "ollama":
        return OllamaBackend(config)
    if config.kind_hint == "vllm":
        return VLLMBackend(config)
    if config.kind_hint == "openai_compatible":
        return CloudBackend(config)

    location = config.location.lower()
    if location == "cloud":
        return CloudBackend(config)
    if "ollama" in config.base_url.lower() or "11434" in config.base_url:
        return OllamaBackend(config)
    if "vllm" in config.base_url.lower():
        return VLLMBackend(config)
    # Default to Ollama for local; this matches the v1 default deployment
    return OllamaBackend(config)


def build_backends_from_topology(topology: Any) -> list[Backend]:
    """Construct the configured backend list from a :class:`Topology`."""
    return [build_backend(BackendConfig.from_topology(b)) for b in topology.vlm_router.backends]
