"""kukiihome_vlm_router — multi-backend VLM router.

Routes VLM requests across local (Ollama, vLLM) and cloud backends based on
capability + privacy tier + cost + health + affinity. Enforces privacy tier
constraints, applies per-backend circuit breakers, runs fallback chains on
transient failure, emits cost/latency telemetry.

See: docs/architecture/04-model-router-and-inference.md
"""

from __future__ import annotations

__version__ = "0.1.0"

from kukiihome_vlm_router.backends import (
    Backend,
    BackendCapability,
    BackendConfig,
    BackendHealth,
    CloudBackend,
    OllamaBackend,
    VLLMBackend,
)
from kukiihome_vlm_router.breaker import CircuitBreaker, CircuitState
from kukiihome_vlm_router.errors import (
    AllBackendsFailedError,
    BackendError,
    PrivacyViolationError,
    RouterError,
)
from kukiihome_vlm_router.policy import RoutingDecision, RoutingPolicy
from kukiihome_vlm_router.router import Router, RouterConfig
from kukiihome_vlm_router.telemetry import BackendTelemetry, RequestRecord

__all__ = [
    "AllBackendsFailedError",
    "Backend",
    "BackendCapability",
    "BackendConfig",
    "BackendError",
    "BackendHealth",
    "BackendTelemetry",
    "CircuitBreaker",
    "CircuitState",
    "CloudBackend",
    "OllamaBackend",
    "PrivacyViolationError",
    "RequestRecord",
    "Router",
    "RouterConfig",
    "RouterError",
    "RoutingDecision",
    "RoutingPolicy",
    "VLLMBackend",
    "__version__",
]
