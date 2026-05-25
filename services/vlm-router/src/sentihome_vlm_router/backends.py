"""Backend driver abstractions + concrete implementations.

Three drivers ship in v1:

- :class:`OllamaBackend` — local, default for service-mode deployments
- :class:`VLLMBackend` — alternative local, higher throughput
- :class:`CloudBackend` — OpenAI-compatible HTTP API (Anthropic, OpenAI, etc.)

Each implements the :class:`Backend` Protocol so the router can swap them
transparently. Drivers are async; HTTP clients are constructed lazily.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from sentihome_vlm_router.errors import BackendError

if TYPE_CHECKING:
    from sentihome_shared.generated.events.vlm_request import VLMRequest
    from sentihome_shared.generated.events.vlm_response import VLMResponse

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Configuration + capability advertisement
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackendCapability:
    """What a backend can do — used by the routing policy."""

    name: str
    """Stable identifier, e.g. "local-ollama" or "anthropic-cloud"."""

    location: str
    """"local" or "cloud" — controls privacy tier enforcement."""

    model_name: str
    """The specific model (e.g. "qwen2.5-vl:7b")."""

    supports_vision: bool = True
    """Does the model accept images?"""

    max_frames_per_call: int = 8
    """Hard cap on frames per request."""

    typical_latency_ms: int = 2000
    """Expected p50 latency for routing scoring."""

    cost_per_1k_tokens_usd: float = 0.0
    """0.0 for local; nonzero for cloud."""


@dataclass
class BackendConfig:
    """Per-backend connection settings."""

    name: str
    location: str  # "local" | "cloud"
    model_name: str
    base_url: str
    api_key: str | None = None
    """Required for cloud backends."""
    timeout_seconds: float = 30.0
    extra_headers: dict[str, str] = field(default_factory=dict)
    cost_per_1k_tokens_usd: float = 0.0
    typical_latency_ms: int = 2000


@dataclass
class BackendHealth:
    """Reported backend health snapshot."""

    name: str
    reachable: bool
    last_check_ts: float
    note: str | None = None


# ─────────────────────────────────────────────────────────────────────
# Abstract Backend
# ─────────────────────────────────────────────────────────────────────


class Backend(ABC):
    """Common interface every VLM backend implements."""

    def __init__(self, config: BackendConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def location(self) -> str:
        return self._config.location

    @property
    def config(self) -> BackendConfig:
        return self._config

    @abstractmethod
    def capability(self) -> BackendCapability:
        """Advertise what this backend can do."""

    @abstractmethod
    async def invoke(self, request: VLMRequest) -> VLMResponse:
        """Execute a VLM call, returning a validated response."""

    async def health(self) -> BackendHealth:
        """Cheap health probe. Default: HTTP HEAD/GET on base_url."""
        try:
            await self._ensure_client()
            assert self._client is not None
            r = await self._client.get("/", timeout=5.0)
            ok = r.status_code < 500
            return BackendHealth(
                name=self.name,
                reachable=ok,
                last_check_ts=time.monotonic(),
                note=None if ok else f"status={r.status_code}",
            )
        except Exception as e:
            return BackendHealth(
                name=self.name,
                reachable=False,
                last_check_ts=time.monotonic(),
                note=str(e),
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.timeout_seconds,
                headers=self._config.extra_headers,
            )


# ─────────────────────────────────────────────────────────────────────
# Concrete drivers
# ─────────────────────────────────────────────────────────────────────


class OllamaBackend(Backend):
    """Local Ollama VLM backend.

    POSTs to ``/api/chat`` with a vision-enabled model (e.g. qwen2.5-vl).
    """

    def capability(self) -> BackendCapability:
        return BackendCapability(
            name=self._config.name,
            location="local",
            model_name=self._config.model_name,
            supports_vision=True,
            max_frames_per_call=8,
            typical_latency_ms=self._config.typical_latency_ms,
            cost_per_1k_tokens_usd=0.0,
        )

    async def invoke(self, request: VLMRequest) -> VLMResponse:
        # Lazy import to avoid hard-circular at module load

        await self._ensure_client()
        assert self._client is not None

        start = time.monotonic()
        try:
            payload = {
                "model": self._config.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": request.prompt,
                        "images": list(request.frames),
                    }
                ],
                "stream": False,
                "format": "json",
            }
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendError(self.name, f"Ollama HTTP error: {e}") from e

        elapsed_ms = int((time.monotonic() - start) * 1000)
        raw = response.json()
        content = (raw.get("message") or {}).get("content", "")
        return _parse_vlm_response(
            content,
            request_id=request.request_id,
            event_id=request.event_id,
            trace_id=request.trace_id,
            backend=self.name,
            latency_ms=elapsed_ms,
            tokens_used=raw.get("eval_count"),
            backend_label=self.name,
        )


class VLLMBackend(Backend):
    """Local vLLM backend (OpenAI-compatible).

    POSTs to ``/v1/chat/completions`` — vLLM exposes the OpenAI API shape.
    """

    def capability(self) -> BackendCapability:
        return BackendCapability(
            name=self._config.name,
            location="local",
            model_name=self._config.model_name,
            supports_vision=True,
            max_frames_per_call=8,
            typical_latency_ms=self._config.typical_latency_ms,
            cost_per_1k_tokens_usd=0.0,
        )

    async def invoke(self, request: VLMRequest) -> VLMResponse:
        return await _openai_compatible_invoke(
            backend=self,
            request=request,
            endpoint="/v1/chat/completions",
        )


class CloudBackend(Backend):
    """OpenAI-compatible cloud VLM (OpenAI, Anthropic via OpenAI shim, etc.).

    Auth via ``api_key`` in config (sent as ``Authorization: Bearer ...``).
    """

    def capability(self) -> BackendCapability:
        return BackendCapability(
            name=self._config.name,
            location="cloud",
            model_name=self._config.model_name,
            supports_vision=True,
            max_frames_per_call=16,
            typical_latency_ms=self._config.typical_latency_ms,
            cost_per_1k_tokens_usd=self._config.cost_per_1k_tokens_usd,
        )

    async def _ensure_client(self) -> None:
        if self._client is None:
            headers = dict(self._config.extra_headers)
            if self._config.api_key:
                headers.setdefault("Authorization", f"Bearer {self._config.api_key}")
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.timeout_seconds,
                headers=headers,
            )

    async def invoke(self, request: VLMRequest) -> VLMResponse:
        return await _openai_compatible_invoke(
            backend=self,
            request=request,
            endpoint="/v1/chat/completions",
        )


# ─────────────────────────────────────────────────────────────────────
# Shared OpenAI-compatible invocation
# ─────────────────────────────────────────────────────────────────────


async def _openai_compatible_invoke(
    *,
    backend: Backend,
    request: VLMRequest,
    endpoint: str,
) -> VLMResponse:
    from sentihome_shared.generated.events.vlm_response import VLMResponse as _VR  # noqa: F401

    await backend._ensure_client()
    assert backend._client is not None

    start = time.monotonic()
    try:
        payload: dict[str, Any] = {
            "model": backend.config.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": request.prompt},
                        *[
                            {"type": "image_url", "image_url": {"url": uri}}
                            for uri in request.frames
                        ],
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
        }
        response = await backend._client.post(endpoint, json=payload)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise BackendError(backend.name, f"HTTP error: {e}") from e

    elapsed_ms = int((time.monotonic() - start) * 1000)
    raw = response.json()
    content = (raw.get("choices") or [{}])[0].get("message", {}).get("content", "")
    usage = raw.get("usage") or {}
    return _parse_vlm_response(
        content,
        request_id=request.request_id,
        event_id=request.event_id,
        trace_id=request.trace_id,
        backend=backend.name,
        latency_ms=elapsed_ms,
        tokens_used=usage.get("total_tokens"),
        backend_label=backend.name,
    )


def _parse_vlm_response(
    content: str,
    *,
    request_id: str,
    event_id: str | None,
    trace_id: str | None,
    backend: str,
    latency_ms: int,
    tokens_used: int | None,
    backend_label: str,
) -> VLMResponse:
    """Parse the raw VLM string into a validated VLMResponse.

    Lives here for reuse; the response_repair module wraps this with one
    retry on schema-validation failure (Epic 5 #80).
    """
    import json

    from pydantic import ValidationError
    from sentihome_shared.generated.events.vlm_response import VLMResponse as _VR

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise BackendError(backend, f"VLM returned non-JSON: {e}: {content[:200]}") from e

    # Inject the framing fields the schema requires; the model isn't asked
    # to emit them (we know them).
    data.setdefault("request_id", request_id)
    if event_id is not None:
        data.setdefault("event_id", event_id)
    if trace_id is not None:
        data.setdefault("trace_id", trace_id)
    data.setdefault("backend", backend_label)
    data["latency_ms"] = latency_ms
    if tokens_used is not None:
        data["tokens_used"] = tokens_used

    try:
        return _VR(**data)
    except ValidationError as e:
        raise BackendError(backend, f"VLM response failed schema validation: {e}") from e
