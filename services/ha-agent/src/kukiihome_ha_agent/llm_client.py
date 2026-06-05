"""LLM client implementations + health tracker (Part X §35 plumbing).

Two pieces wire the dispatcher to a real text LLM:

  - **OpenAIChatClient** — concrete ``LLMClient`` implementation against
    the OpenAI-compatible ``/v1/chat/completions`` shape. Works for
    Cerebras (target endpoint), Ollama, LM Studio, vLLM, Together, Groq,
    and OpenAI itself — the JSON shape is identical across all of them.
    Built on httpx (already a dep). Caller provides base_url + api_key
    + model.

  - **LLMHealthTracker** — small in-memory state that records each
    success/failure on the LLM path. The CompositeDispatcherProvider
    reports to it; the ``/memory`` page reads it to render a degraded-
    mode banner per §39 backstop principle (no silent fallback).

The ``LLMClient`` Protocol itself lives in ``dispatcher.py`` so this
module can stay decoupled from dispatch logic — a future Anthropic
Messages client or HA-conversation-agent client just implements the
same Protocol.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


# ─── OpenAI-compatible chat completions client ────────────────────


class OpenAIChatClient:
    """``LLMClient`` implementation against the OpenAI-compatible
    ``POST {base_url}/chat/completions`` shape. Single ``complete()``
    method returns the assistant message text; HTTP errors raise so
    the dispatcher retry / composite fallback can react.

    Built for Cerebras initially, but the request + response shape is
    portable across every provider that follows the OpenAI Chat API
    (Cerebras, Ollama, LM Studio, vLLM, Together, Groq, OpenAI itself).
    """

    def __init__(
        self, *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        # Normalize: caller may pass with or without trailing /v1.
        url = base_url.rstrip("/")
        if not url.endswith("/v1"):
            # Cerebras + most providers use /v1; default it if missing so
            # the user doesn't have to remember.
            url = url + "/v1"
        self.base_url = url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def complete_chat(
        self, *, messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1500,
    ) -> dict:
        """Full chat-completion call. Returns the raw assistant
        ``message`` dict — caller decides whether to read ``content``
        (final answer) or ``tool_calls`` (mid-reasoning tool requests).

        Multi-turn dispatching needs full message control: the caller
        owns the message history across tool-call rounds, this method
        is a thin transport. ``complete()`` is a single-shot
        convenience built on top for backwards compatibility.
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        if tools:
            payload["tools"] = tools
        else:
            # When no tools, hint json_object so the structured-output
            # path returns clean JSON in content. With tools, omit the
            # hint — some providers won't emit tool_calls under
            # response_format=json_object.
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"unexpected chat-completion response shape: {data!r}",
            ) from e

    async def complete(
        self, *, system: str, user: str, max_tokens: int = 1500,
    ) -> str:
        # 1500 leaves headroom for reasoning-class models (gpt-oss /
        # zai-glm / o1-style) where internal chain-of-thought tokens
        # count against max_tokens. The placement proposal itself only
        # needs ~300-500 tokens; the rest is reasoning budget.
        """Send one chat completion. Returns the assistant content
        text verbatim — the dispatcher handles parsing / schema retry."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            # Low temperature → deterministic structured-output behavior.
            # The dispatcher needs a stable JSON shape; creativity is not
            # what we want here.
            "temperature": 0.1,
            # Hint the model to return JSON. Cerebras + OpenAI both honor
            # this; providers that don't will just ignore it (the
            # dispatcher's _strip_code_fence + retry handle the difference).
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"unexpected chat-completion response shape: {data!r}",
            ) from e


# ─── LLM health tracker (banner backing state) ────────────────────


@dataclass
class LLMHealth:
    """Snapshot of LLM availability for the /memory banner."""

    ok: bool                              # last call succeeded
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_failure_reason: str = ""
    total_calls: int = 0
    total_failures: int = 0


class LLMHealthTracker:
    """Tiny in-memory counter. Shared between the CompositeDispatcher
    (writer) and the /memory route (reader). No persistence — the
    banner shows current-session state, which is what the user cares
    about. Reset on add-on restart is fine."""

    def __init__(self) -> None:
        self._last_success_at: float | None = None
        self._last_failure_at: float | None = None
        self._last_failure_reason: str = ""
        self._total_calls: int = 0
        self._total_failures: int = 0

    def record_success(self, *, now_ts: float | None = None) -> None:
        self._last_success_at = now_ts or time.time()
        self._total_calls += 1

    def record_failure(
        self, reason: str, *, now_ts: float | None = None,
    ) -> None:
        self._last_failure_at = now_ts or time.time()
        self._last_failure_reason = reason or "unknown"
        self._total_calls += 1
        self._total_failures += 1
        logger.warning("llm_health.failure_recorded", reason=reason)

    @property
    def status(self) -> LLMHealth:
        # ok = the most recent call succeeded. We deliberately don't use
        # a time-based window — if the last call failed it's still
        # failing as far as the user knows, regardless of how long ago.
        ok = (
            self._last_success_at is not None
            and (
                self._last_failure_at is None
                or self._last_success_at >= self._last_failure_at
            )
        )
        return LLMHealth(
            ok=ok,
            last_success_at=self._last_success_at,
            last_failure_at=self._last_failure_at,
            last_failure_reason=self._last_failure_reason,
            total_calls=self._total_calls,
            total_failures=self._total_failures,
        )
