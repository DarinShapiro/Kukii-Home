"""Lightweight tracing primitives.

A trace ID is generated at event ingress (NVR adapter or HA poller) and
propagated through every downstream tool call, log line, and bus message.

Usage::

    from sentihome_shared.tracing import new_trace_id, trace_context

    trace_id = new_trace_id()
    with trace_context(trace_id=trace_id, event_id="..."):
        ...  # all log lines here include trace_id

For OpenTelemetry-grade tracing (spans, exporters, etc.), use the
``opentelemetry`` packages directly — this module is the minimal
trace-id-propagation layer that's always on.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog


def new_trace_id() -> str:
    """Generate a fresh 16-byte hex trace ID (32 chars)."""
    return secrets.token_hex(16)


def new_span_id() -> str:
    """Generate a fresh 8-byte hex span ID (16 chars)."""
    return secrets.token_hex(8)


@contextmanager
def trace_context(**kwargs: Any) -> Iterator[None]:
    """Context manager that binds trace fields onto structlog contextvars.

    On exit, removes the keys that were added (does not affect pre-existing keys).

    Example::

        with trace_context(trace_id=tid, event_id=eid):
            log.info("processing")  # log line carries trace_id + event_id
    """
    # Snapshot existing keys so we know what we added vs. what was already there
    existing = structlog.contextvars.get_contextvars()
    added_keys = [k for k in kwargs if k not in existing]

    structlog.contextvars.bind_contextvars(**kwargs)
    try:
        yield
    finally:
        if added_keys:
            structlog.contextvars.unbind_contextvars(*added_keys)
