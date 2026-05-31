"""Circuit breaker per backend.

Implements the classic three-state pattern:

- CLOSED: requests flow normally. On N consecutive failures → OPEN.
- OPEN:   requests rejected immediately for ``reset_seconds``. After that → HALF_OPEN.
- HALF_OPEN: one probe request allowed. Success → CLOSED. Failure → OPEN again.

Thread-safety: not safe for concurrent calls; the router serializes per-backend
calls so this is fine in practice. If you need concurrent calls per backend,
wrap in an asyncio.Lock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-backend circuit breaker."""

    failure_threshold: int = 5
    """Consecutive failures before opening."""

    reset_seconds: float = 30.0
    """How long to wait in OPEN before transitioning to HALF_OPEN."""

    _state: CircuitState = CircuitState.CLOSED
    _consecutive_failures: int = 0
    _opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        # Lazily transition OPEN → HALF_OPEN when reset window has elapsed
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.reset_seconds:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def can_attempt(self) -> bool:
        """Return True if a new request should be allowed through."""
        return self.state != CircuitState.OPEN

    def record_success(self) -> None:
        """Record a successful call. Closes the circuit if it was half-open."""
        self._consecutive_failures = 0
        if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            self._state = CircuitState.CLOSED
            self._opened_at = None

    def record_failure(self) -> None:
        """Record a failed call. Opens the circuit at threshold."""
        # If we're HALF_OPEN, any failure goes straight back to OPEN
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            self._consecutive_failures += 1
            return

        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def reset(self) -> None:
        """Force the breaker back to CLOSED. For operator override."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
