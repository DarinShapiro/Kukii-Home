"""Router-specific errors."""

from __future__ import annotations


class RouterError(Exception):
    """Base class for router errors."""


class BackendError(RouterError):
    """A specific backend failed. Routable: caller may try the next backend."""

    def __init__(self, backend_name: str, message: str) -> None:
        super().__init__(f"[{backend_name}] {message}")
        self.backend_name = backend_name


class PrivacyViolationError(RouterError):
    """Privacy tier constraint forbids this routing. Not recoverable by fallback."""


class AllBackendsFailedError(RouterError):
    """Every eligible backend failed. The fallback chain is exhausted."""

    def __init__(self, attempts: list[tuple[str, str]]) -> None:
        attempt_summary = "; ".join(f"{name}: {err}" for name, err in attempts)
        super().__init__(f"All backends failed. Attempts: {attempt_summary}")
        self.attempts = attempts
