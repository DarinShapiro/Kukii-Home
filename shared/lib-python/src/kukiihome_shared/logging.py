"""Structured logging with trace ID propagation.

All Kukii-Home services emit JSON logs to stdout, one event per line. Each log
record carries a ``trace_id`` and ``service`` field so logs across services
can be joined by trace ID.

Usage::

    from kukiihome_shared.logging import configure_logging, get_logger

    configure_logging(service="core", level="INFO")
    log = get_logger(__name__)
    log.info("triage decision", event_id="...", rule_id="...")
"""

from __future__ import annotations

import logging
import os
from typing import Any

import structlog

_configured = False


def configure_logging(
    *,
    service: str,
    level: str | None = None,
    json_output: bool | None = None,
) -> None:
    """Configure structlog + stdlib logging for a service.

    Idempotent: subsequent calls re-bind context but don't reinstall processors.

    Args:
        service: Service name (e.g. "core", "preprocessor"). Added to every log.
        level: Log level string (DEBUG, INFO, ...). Defaults to ``LOG_LEVEL`` env.
        json_output: Force JSON output regardless of TTY. Default: JSON if not a TTY,
            else colored console output for dev ergonomics.
    """
    global _configured

    resolved_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    use_json = json_output if json_output is not None else not _is_tty()

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, resolved_level, logging.INFO),
    )

    if _configured:
        # Rebind service context only
        structlog.contextvars.bind_contextvars(service=service)
        return

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if use_json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, resolved_level, logging.INFO),
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.bind_contextvars(service=service)
    _configured = True


def get_logger(name: str | None = None) -> Any:
    """Return a structlog logger bound to ``name`` (typically ``__name__``)."""
    return structlog.get_logger(name)


def bind(**kwargs: Any) -> None:
    """Bind context variables (e.g. trace_id) onto the current async context.

    Cleared automatically at task boundaries; safe across concurrent tasks.
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def unbind(*keys: str) -> None:
    """Remove keys from the current async context."""
    structlog.contextvars.unbind_contextvars(*keys)


def _is_tty() -> bool:
    import sys

    return sys.stdout.isatty() and sys.stderr.isatty()
