"""OpenAIChatClient + LLMHealthTracker + degraded-mode banner (Part X §35)."""

from __future__ import annotations

import httpx
import pytest
from kukiihome_ha_agent.dispatcher import (
    CompositeDispatcherProvider,
    DispatcherContext,
)
from kukiihome_ha_agent.llm_client import (
    LLMHealth,
    LLMHealthTracker,
    OpenAIChatClient,
)
from kukiihome_ha_agent.web_ui.memory import render_memory_page

NOW = 1_700_000_000.0


# ─── OpenAIChatClient — happy path + URL normalization ───────────


def _wrap_chat_response(content: str) -> dict:
    """Build the OpenAI chat-completion response envelope around content."""
    return {
        "id": "cmpl_x",
        "object": "chat.completion",
        "choices": [
            {"index": 0,
             "message": {"role": "assistant", "content": content},
             "finish_reason": "stop"},
        ],
        "model": "fake-model",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.mark.asyncio
async def test_openai_chat_client_returns_assistant_content():
    captured: dict = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        import json
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200, json=_wrap_chat_response('{"storage_class": "rule"}'),
        )

    transport = httpx.MockTransport(transport_handler)
    # Patch AsyncClient to use the mock transport for this test
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    import kukiihome_ha_agent.llm_client as mod
    mod.httpx.AsyncClient = _factory
    try:
        client = OpenAIChatClient(
            base_url="https://api.cerebras.ai/v1",
            api_key="sk-test",
            model="llama-3.3-70b",
        )
        text = await client.complete(system="sys prompt", user="hello")
    finally:
        mod.httpx.AsyncClient = real_async_client

    assert text == '{"storage_class": "rule"}'
    assert captured["url"] == "https://api.cerebras.ai/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    assert captured["body"]["model"] == "llama-3.3-70b"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][0]["content"] == "sys prompt"
    assert captured["body"]["messages"][1]["content"] == "hello"
    # JSON-object response format requested
    assert captured["body"]["response_format"]["type"] == "json_object"
    # Low temperature for deterministic structured output
    assert captured["body"]["temperature"] <= 0.2


def test_openai_chat_client_normalizes_base_url_without_v1():
    """Caller may pass the host root; we add /v1 so end users don't
    have to remember the convention."""
    client = OpenAIChatClient(
        base_url="https://api.cerebras.ai", api_key="x", model="y",
    )
    assert client.base_url == "https://api.cerebras.ai/v1"


def test_openai_chat_client_keeps_existing_v1():
    client = OpenAIChatClient(
        base_url="https://api.cerebras.ai/v1/", api_key="x", model="y",
    )
    assert client.base_url == "https://api.cerebras.ai/v1"


@pytest.mark.asyncio
async def test_openai_chat_client_raises_on_http_error():
    def transport_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    transport = httpx.MockTransport(transport_handler)
    real = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    import kukiihome_ha_agent.llm_client as mod
    mod.httpx.AsyncClient = _factory
    try:
        client = OpenAIChatClient(base_url="https://x", api_key="k", model="m")
        with pytest.raises(httpx.HTTPStatusError):
            await client.complete(system="s", user="u")
    finally:
        mod.httpx.AsyncClient = real


@pytest.mark.asyncio
async def test_openai_chat_client_raises_on_unexpected_shape():
    def transport_handler(_request: httpx.Request) -> httpx.Response:
        # Missing the choices array
        return httpx.Response(200, json={"id": "x"})

    transport = httpx.MockTransport(transport_handler)
    real = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    import kukiihome_ha_agent.llm_client as mod
    mod.httpx.AsyncClient = _factory
    try:
        client = OpenAIChatClient(base_url="https://x", api_key="k", model="m")
        with pytest.raises(RuntimeError, match="unexpected"):
            await client.complete(system="s", user="u")
    finally:
        mod.httpx.AsyncClient = real


# ─── LLMHealthTracker ─────────────────────────────────────────────


def test_health_tracker_starts_ok_false_before_any_call():
    t = LLMHealthTracker()
    assert t.status.ok is False
    assert t.status.total_calls == 0


def test_health_tracker_ok_after_success():
    t = LLMHealthTracker()
    t.record_success(now_ts=NOW)
    assert t.status.ok is True
    assert t.status.last_success_at == NOW
    assert t.status.total_calls == 1


def test_health_tracker_not_ok_after_failure():
    t = LLMHealthTracker()
    t.record_success(now_ts=NOW)
    t.record_failure("timeout", now_ts=NOW + 60)
    assert t.status.ok is False
    assert t.status.last_failure_reason == "timeout"
    assert t.status.total_failures == 1


def test_health_tracker_ok_again_after_recovery():
    t = LLMHealthTracker()
    t.record_failure("timeout", now_ts=NOW)
    t.record_success(now_ts=NOW + 60)
    assert t.status.ok is True


def test_health_tracker_counts_total_calls():
    t = LLMHealthTracker()
    for _ in range(5):
        t.record_success(now_ts=NOW)
    for _ in range(2):
        t.record_failure("x", now_ts=NOW)
    s = t.status
    assert s.total_calls == 7
    assert s.total_failures == 2


# ─── Composite reports to tracker ────────────────────────────────


class _FakeLLM:
    def __init__(self, raise_with: Exception | None = None) -> None:
        self.raise_with = raise_with
        self.calls = 0

    async def propose_async(self, utterance, *, ctx):
        self.calls += 1
        if self.raise_with:
            raise self.raise_with
        from kukiihome_ha_agent.provenance_store import PlacementProposal
        return PlacementProposal(
            storage_class="rule", name="x", scope={},
            lifecycle="persistent", fire_affordance="alert",
            intent_text="x", reasoning="r", confidence=0.9,
        )


@pytest.mark.asyncio
async def test_composite_records_success_to_tracker():
    tracker = LLMHealthTracker()
    composite = CompositeDispatcherProvider(
        llm=_FakeLLM(), health=tracker,
    )
    await composite.propose_async(
        "alert me", ctx=DispatcherContext([], [], []),
    )
    assert tracker.status.ok is True
    assert tracker.status.total_failures == 0


@pytest.mark.asyncio
async def test_composite_records_failure_to_tracker_on_llm_error():
    tracker = LLMHealthTracker()
    composite = CompositeDispatcherProvider(
        llm=_FakeLLM(raise_with=RuntimeError("network down")),
        health=tracker,
    )
    await composite.propose_async(
        "alert me when Bob arrives", ctx=DispatcherContext([], [], []),
    )
    s = tracker.status
    assert s.ok is False
    assert "network down" in s.last_failure_reason


@pytest.mark.asyncio
async def test_composite_with_no_tracker_still_works():
    """The tracker is optional. None-safe path stays functional."""
    composite = CompositeDispatcherProvider(llm=_FakeLLM(), health=None)
    p = await composite.propose_async(
        "alert me", ctx=DispatcherContext([], [], []),
    )
    assert p.storage_class == "rule"


# ─── /memory degraded-mode banner ────────────────────────────────


def test_memory_page_renders_llm_down_banner_when_unhealthy():
    health = LLMHealth(
        ok=False, last_failure_reason="connection refused",
        last_failure_at=NOW,
    )
    html = render_memory_page([], llm_health=health, now_ts=NOW)
    assert "llm-down-banner" in html
    assert "LLM unavailable" in html
    assert "connection refused" in html


def test_memory_page_no_banner_when_healthy():
    health = LLMHealth(ok=True, last_success_at=NOW)
    html = render_memory_page([], llm_health=health, now_ts=NOW)
    assert "llm-down-banner" not in html


def test_memory_page_no_banner_on_fresh_restart_with_no_calls():
    """Tracker starts with ok=False as a defensive default. After a
    clean restart with no utterances tried yet, the banner should be
    silent — showing 'LLM unavailable' here would alarm users between
    restarts when nothing has actually failed."""
    health = LLMHealth(ok=False)  # last_failure_at=None by default
    html = render_memory_page([], llm_health=health, now_ts=NOW)
    assert "llm-down-banner" not in html


def test_memory_page_no_banner_when_health_none():
    """When LLM isn't configured, the tracker is None and we render
    nothing — heuristic-only operation is the design baseline, not
    something the user needs to know about."""
    html = render_memory_page([], llm_health=None, now_ts=NOW)
    assert "llm-down-banner" not in html


def test_memory_page_llm_banner_escapes_failure_reason():
    health = LLMHealth(
        ok=False, last_failure_reason="<script>bad</script>",
        last_failure_at=NOW,
    )
    html = render_memory_page([], llm_health=health, now_ts=NOW)
    assert "<script>bad" not in html
    assert "&lt;script&gt;bad" in html


def test_memory_page_llm_banner_appears_above_drift_banner():
    """LLM failure is more urgent than drift — surface it first."""
    from kukiihome_ha_agent.drift_detector import DriftSuggestion
    health = LLMHealth(
        ok=False, last_failure_reason="x", last_failure_at=NOW,
    )
    drift = [DriftSuggestion(
        guidance_id="r1", kind="rule", name="Stale",
        summary="y", recommended_action="convert_to_preference",
    )]
    html = render_memory_page(
        [], llm_health=health, drift_suggestions=drift, now_ts=NOW,
    )
    assert html.index("llm-down-banner") < html.index("drift-banner")
