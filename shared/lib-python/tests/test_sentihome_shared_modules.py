"""Smoke tests for the sentihome_shared library modules."""

from __future__ import annotations

from pydantic import BaseModel


def test_logging_configure_idempotent() -> None:
    from sentihome_shared.logging import configure_logging, get_logger

    configure_logging(service="test", level="INFO")
    configure_logging(service="test", level="INFO")  # second call is safe
    log = get_logger("test")
    assert log is not None


def test_tracing_new_ids() -> None:
    from sentihome_shared.tracing import new_span_id, new_trace_id

    trace = new_trace_id()
    span = new_span_id()
    assert len(trace) == 32
    assert len(span) == 16
    assert trace != new_trace_id()


def test_trace_context_binds_and_unbinds() -> None:
    import structlog
    from sentihome_shared.tracing import new_trace_id, trace_context

    trace_id = new_trace_id()
    assert "trace_id" not in structlog.contextvars.get_contextvars()

    with trace_context(trace_id=trace_id, event_id="evt_xyz"):
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["trace_id"] == trace_id
        assert ctx["event_id"] == "evt_xyz"

    assert "trace_id" not in structlog.contextvars.get_contextvars()
    assert "event_id" not in structlog.contextvars.get_contextvars()


def test_config_load_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from sentihome_shared.config import load_config

    class CoreConfig(BaseModel):
        nats_url: str
        log_level: str = "INFO"

    monkeypatch.setenv("SENTIHOME_TEST_NATS_URL", "nats://example:4222")
    monkeypatch.setenv("SENTIHOME_TEST_LOG_LEVEL", "DEBUG")

    cfg = load_config(CoreConfig, prefix="SENTIHOME_TEST_")
    assert cfg.nats_url == "nats://example:4222"
    assert cfg.log_level == "DEBUG"


def test_mcp_policy_gate_error_payload() -> None:
    from sentihome_shared.mcp import PolicyGateError

    err = PolicyGateError(action="unlock", reason="needs confirmation")
    payload = err.to_dict()
    assert payload["error"] == "policy_gate"
    assert payload["suggest"] == "ask"
    assert err.action == "unlock"


def test_bus_module_imports() -> None:
    """Bus module imports cleanly even though full impl lands in Epic 02."""
    from sentihome_shared import bus

    assert hasattr(bus, "connect")
    assert hasattr(bus, "publish_json")
