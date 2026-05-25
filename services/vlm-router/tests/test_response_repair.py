"""Tests for response repair (one-retry on schema-near-miss)."""

from __future__ import annotations

import pytest
from sentihome_vlm_router.errors import BackendError
from sentihome_vlm_router.response_repair import _strip_markdown, try_repair_response


def test_strip_markdown_with_json_fence() -> None:
    content = '```json\n{"a": 1}\n```'
    assert _strip_markdown(content) == '{"a": 1}'


def test_strip_markdown_without_fence() -> None:
    content = '{"a": 1}'
    assert _strip_markdown(content) == '{"a": 1}'


def test_strip_markdown_with_plain_fence() -> None:
    content = '```\n{"a": 1}\n```'
    assert _strip_markdown(content) == '{"a": 1}'


def test_repair_strips_markdown_and_validates() -> None:
    content = '```json\n{"criticality": "info", "confidence": 0.5}\n```'
    response = try_repair_response(
        content,
        request_id="r1",
        event_id="e1",
        trace_id="a" * 32,
        backend="test-backend",
        latency_ms=100,
        tokens_used=50,
    )
    assert response.request_id == "r1"
    assert response.criticality == "info"
    assert response.confidence == 0.5
    assert response.backend == "test-backend"


def test_repair_fills_missing_required_fields() -> None:
    # Empty JSON — repair should fill defaults
    response = try_repair_response(
        "{}",
        request_id="r1",
        event_id=None,
        trace_id=None,
        backend="x",
        latency_ms=1,
        tokens_used=None,
    )
    assert response.criticality == "info"
    assert response.confidence == 0.0


def test_repair_raises_on_unparseable() -> None:
    with pytest.raises(BackendError):
        try_repair_response(
            "this is not json at all",
            request_id="r1",
            event_id=None,
            trace_id=None,
            backend="x",
            latency_ms=1,
            tokens_used=None,
        )
