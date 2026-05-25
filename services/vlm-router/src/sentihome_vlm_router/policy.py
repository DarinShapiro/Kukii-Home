"""Routing policy engine.

Decides which backend(s) to try, in what order, for a given request.
Considerations (per §04):

1. **Privacy tier** (hard constraint) — local_only data must not route to cloud
2. **Capability** — backend must support vision + accept request size
3. **Health** — circuit breakers must allow attempts
4. **Affinity** — caller's ``preferred_backend`` hint (soft)
5. **Cost** — local before cloud unless local is saturated/unhealthy
6. **Latency** — lower-latency backends ranked higher under tight deadlines
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from sentihome_vlm_router.errors import PrivacyViolationError

if TYPE_CHECKING:
    from sentihome_shared.generated.events.vlm_request import VLMRequest

    from sentihome_vlm_router.backends import Backend, BackendCapability
    from sentihome_vlm_router.breaker import CircuitBreaker

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RoutingDecision:
    """Output of the policy engine: an ordered fallback chain."""

    chain: tuple[str, ...]
    """Ordered backend names to try."""

    reasons: tuple[str, ...]
    """Parallel to ``chain``: one human-readable reason per entry."""


class RoutingPolicy:
    """Builds a fallback chain for a given request.

    Stateless: feed it backends + breakers + the request, get a chain.
    """

    def decide(
        self,
        *,
        request: VLMRequest,
        backends: list[Backend],
        breakers: dict[str, CircuitBreaker],
    ) -> RoutingDecision:
        """Compute the ordered fallback chain. Raises PrivacyViolationError if
        no backend can legally handle the request (e.g., local_only + no local
        backend healthy).
        """
        eligible: list[tuple[Backend, BackendCapability, float, str]] = []
        rejected: list[tuple[str, str]] = []

        for backend in backends:
            cap = backend.capability()
            breaker = breakers.get(backend.name)

            # Privacy tier check (hard)
            tier = (
                request.privacy_tier.value
                if hasattr(request.privacy_tier, "value")
                else request.privacy_tier
            )
            if tier == "local_only" and cap.location != "local":
                rejected.append((backend.name, "privacy_tier=local_only requires local backend"))
                continue

            # Capability check
            if not cap.supports_vision and len(request.frames) > 0:
                rejected.append((backend.name, "backend does not support vision"))
                continue
            if len(request.frames) > cap.max_frames_per_call:
                rejected.append(
                    (backend.name, f"{len(request.frames)} frames > max {cap.max_frames_per_call}")
                )
                continue

            # Circuit breaker check
            if breaker is not None and not breaker.can_attempt():
                rejected.append((backend.name, "circuit breaker open"))
                continue

            # Latency budget check
            if (
                request.max_latency_ms is not None
                and cap.typical_latency_ms > request.max_latency_ms
            ):
                # Don't outright reject — penalize in scoring instead.
                penalty = 10.0
            else:
                penalty = 0.0

            score = _score(backend, cap, request, penalty=penalty)
            reason = _explain(backend, cap, request)
            eligible.append((backend, cap, score, reason))

        if not eligible:
            # If all rejections were privacy-driven, it's a privacy violation
            if all("privacy" in r for _, r in rejected):
                raise PrivacyViolationError(
                    f"No backend can handle privacy_tier={request.privacy_tier}: "
                    + "; ".join(f"{n}: {r}" for n, r in rejected)
                )
            # Otherwise, the chain is just empty and the router will raise
            # AllBackendsFailedError downstream
            return RoutingDecision(chain=(), reasons=tuple(f"{n}: {r}" for n, r in rejected))

        # Sort by score (lower = better)
        eligible.sort(key=lambda e: e[2])

        chain = tuple(b.name for b, _, _, _ in eligible)
        reasons = tuple(r for _, _, _, r in eligible)

        logger.debug(
            "policy.decided",
            chain=chain,
            preferred=request.preferred_backend,
            tier=request.privacy_tier,
        )
        return RoutingDecision(chain=chain, reasons=reasons)


def _score(
    backend: Backend,
    cap: BackendCapability,
    request: VLMRequest,
    *,
    penalty: float = 0.0,
) -> float:
    """Lower is better.

    Scoring weights:
    - Affinity match → -100 (strong preference)
    - Local backends → small bonus (-10) — prefer keeping data local
    - Latency → backend's typical_latency_ms / 100 (faster = lower)
    - Cost → ``cost_per_1k_tokens_usd * 10`` (cheaper = lower)
    """
    score = 0.0
    if request.preferred_backend and backend.name == request.preferred_backend:
        score -= 100.0
    if cap.location == "local":
        score -= 10.0
    score += cap.typical_latency_ms / 100.0
    score += cap.cost_per_1k_tokens_usd * 10.0
    score += penalty
    return score


def _explain(
    backend: Backend,
    cap: BackendCapability,
    request: VLMRequest,
) -> str:
    parts = [f"loc={cap.location}", f"~{cap.typical_latency_ms}ms"]
    if request.preferred_backend == backend.name:
        parts.append("preferred")
    if cap.cost_per_1k_tokens_usd > 0:
        parts.append(f"${cap.cost_per_1k_tokens_usd}/1K-tok")
    return f"{backend.name} [{', '.join(parts)}]"
