"""Accelerated test clock — drives the harness through simulated months.

In production the memory subsystem reads wall-clock time. The test
harness needs to fast-forward through 30-90 days of activity in
seconds of real time. This module provides a TimeProvider that the
harness controls; tests can wire it in place of ``time.time()``.

Usage:
    tp = TimeProvider(seed=42, start_ts=1735689600.0)  # 2025-01-01 UTC
    tp.now()                # returns 1735689600.0
    tp.advance(86400)       # fast-forward one simulated day
    tp.now()                # returns 1735776000.0
    tp.advance_to(target)   # jump to a specific timestamp
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

_DEFAULT_START_TS = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


@dataclass
class TimeProvider:
    """Test clock with explicit advance semantics.

    The harness drives this clock forward as it processes scenario
    events. Code under test should accept a TimeProvider and call
    ``provider.now()`` instead of ``time.time()`` or
    ``datetime.now()``. In production a real-clock provider returns
    actual wall time; in tests this synthetic provider returns
    whatever the test has advanced to.
    """

    start_ts: float = _DEFAULT_START_TS
    """Simulated wall-clock origin (Unix seconds)."""

    _elapsed: float = field(default=0.0, init=False)
    """Cumulative simulated time advanced since start_ts."""

    def now(self) -> float:
        """Return the current simulated Unix timestamp."""
        return self.start_ts + self._elapsed

    def now_dt(self) -> datetime:
        """Return the current simulated time as an aware datetime (UTC)."""
        return datetime.fromtimestamp(self.now(), tz=UTC)

    def advance(self, seconds: float) -> None:
        """Move the simulated clock forward by ``seconds``."""
        if seconds < 0:
            raise ValueError("TimeProvider can only advance forward")
        self._elapsed += seconds

    def advance_to(self, target_ts: float) -> None:
        """Jump simulated time to a specific Unix timestamp.

        Raises if ``target_ts`` is before the current simulated time.
        """
        delta = target_ts - self.now()
        if delta < 0:
            raise ValueError(
                f"TimeProvider can't move backward (current={self.now()}, target={target_ts})"
            )
        self._elapsed += delta

    def reset(self) -> None:
        """Reset elapsed time to zero (back to start_ts)."""
        self._elapsed = 0.0

    @property
    def elapsed_seconds(self) -> float:
        """Total simulated seconds advanced since start."""
        return self._elapsed

    @property
    def elapsed_days(self) -> float:
        """Total simulated days advanced since start."""
        return self._elapsed / 86_400.0
