"""Response repair: one-retry on schema-validation failure.

When a VLM returns near-valid JSON but missing/extra fields, attempt a single
deterministic repair before failing the whole request. Production code paths
wrap ``Backend.invoke`` with :func:`with_response_repair` if enabled.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog
from pydantic import ValidationError

from kukiihome_vlm_router.errors import BackendError

if TYPE_CHECKING:
    from kukiihome_shared.generated.events.vlm_response import VLMResponse

logger = structlog.get_logger(__name__)


def try_repair_response(
    raw_content: str,
    *,
    request_id: str,
    event_id: str | None,
    trace_id: str | None,
    backend: str,
    latency_ms: int,
    tokens_used: int | None,
) -> VLMResponse:
    """Attempt to repair a near-valid VLM response.

    Repair heuristics:
    1. If the model wrapped JSON in markdown ```json blocks, strip them.
    2. If required fields are missing, fill sensible defaults
       (criticality="info", confidence=0.0).
    3. Trim known-bad characters before parsing.

    If repair still fails, raise BackendError.
    """
    from kukiihome_shared.generated.events.vlm_response import VLMResponse as _VR

    cleaned = _strip_markdown(raw_content).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise BackendError(
            backend, f"Response repair failed to parse JSON: {e}: {cleaned[:200]}"
        ) from e

    data.setdefault("request_id", request_id)
    if event_id is not None:
        data.setdefault("event_id", event_id)
    if trace_id is not None:
        data.setdefault("trace_id", trace_id)
    data.setdefault("backend", backend)
    data.setdefault("criticality", "info")
    data.setdefault("confidence", 0.0)
    data["latency_ms"] = latency_ms
    if tokens_used is not None:
        data["tokens_used"] = tokens_used

    try:
        return _VR(**data)
    except ValidationError as e:
        raise BackendError(backend, f"Response repair could not satisfy schema: {e}") from e


def _strip_markdown(content: str) -> str:
    """Remove ```json ... ``` markdown fences if present."""
    if "```" not in content:
        return content
    # Find first ``` and last ```
    start = content.find("```")
    end = content.rfind("```")
    if start == end:
        return content
    inner = content[start + 3 : end]
    # Drop a leading "json" language tag
    if inner.startswith("json"):
        inner = inner[4:]
    return inner.strip()
