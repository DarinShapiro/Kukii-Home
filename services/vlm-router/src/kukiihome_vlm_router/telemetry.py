"""Per-backend cost + latency telemetry.

Lightweight in-memory rolling counters per backend. The observability service
(Epic 12) scrapes these via the metrics endpoint; for now we expose
``snapshot()`` for tests and the router's internal scoring.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequestRecord:
    """One completed request, recorded for telemetry."""

    backend_name: str
    success: bool
    latency_ms: int
    tokens_used: int | None
    cost_usd: float | None
    timestamp: float


@dataclass
class BackendTelemetry:
    """Rolling metrics for a single backend."""

    backend_name: str
    window_size: int = 1000
    _records: deque[RequestRecord] = field(default_factory=deque)

    def record(
        self,
        *,
        success: bool,
        latency_ms: int,
        tokens_used: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        rec = RequestRecord(
            backend_name=self.backend_name,
            success=success,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            timestamp=time.monotonic(),
        )
        self._records.append(rec)
        while len(self._records) > self.window_size:
            self._records.popleft()

    @property
    def request_count(self) -> int:
        return len(self._records)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self._records if r.success)

    @property
    def success_rate(self) -> float:
        if not self._records:
            return 1.0
        return self.success_count / len(self._records)

    @property
    def p50_latency_ms(self) -> int:
        return self._percentile_latency(50)

    @property
    def p95_latency_ms(self) -> int:
        return self._percentile_latency(95)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd or 0.0 for r in self._records)

    @property
    def total_tokens(self) -> int:
        return sum(r.tokens_used or 0 for r in self._records)

    def _percentile_latency(self, pct: int) -> int:
        if not self._records:
            return 0
        latencies = sorted(r.latency_ms for r in self._records)
        idx = max(0, min(len(latencies) - 1, int(len(latencies) * pct / 100)))
        return latencies[idx]

    def snapshot(self) -> dict[str, float | int]:
        return {
            "backend_name": self.backend_name,
            "request_count": self.request_count,
            "success_rate": self.success_rate,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "total_cost_usd": self.total_cost_usd,
            "total_tokens": self.total_tokens,
        }
