# 04 — Model Router & Inference Hosts

**Purpose:** How heterogeneous inference backends (multiple local Ollama hosts, cloud providers) are selected per event, with privacy, cost, and call-pattern capability as first-class inputs.
**Status:** drafting

---

## Capabilities, not models

The queue and pipeline never target a specific model. They request a **capability + SLO**:

```
{
  task: "visual_reasoning | description | chat | embed | rule_normalize | journey_summary | scrub",
  supports_visual_reasoning: true,   ← drives single-call vs two-step path (see §06, §09)
  max_latency_ms: 8000,
  privacy_tier: "local_only | cloud_trusted | cloud_any",
  preferred_quality: "best | standard | fast"
}
```

The router selects the backend that satisfies the request. Callers don't know or care which model runs.

---

## `supports_visual_reasoning` — the key capability flag

This flag determines which prompt path the pipeline uses (see §06 and §09):

| Flag value | Call pattern                                                     | When to use                                                                                     |
| ---------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `true`     | Single VLM call: frames + full context + persona → decision JSON | Capable reasoning VLMs (Qwen2.5-VL 72B, InternVL2 72B, GPT-4o, Claude)                          |
| `false`    | Two-step: VLM describes → text LLM reasons                       | Smaller/weaker local vision models that reliably describe but don't reason over complex context |

The flag is set per backend in the registry and tested on registration. It is not inferred at runtime.

---

## Backend registry schema

```
BackendRecord:
  backend_id
  kind: vlm | chat | embed | text_reasoner
  location: local_host_A | local_host_B | cloud_anthropic | cloud_openai | ...
  model_id: "qwen2.5-vl:72b" | "claude-opus-4-7" | ...

  capabilities: {
    supports_visual_reasoning: bool,
    max_images_per_call: int,
    max_context_tokens: int,
    supported_tasks: [visual_reasoning, description, chat, embed, ...]
    structured_output: bool,
    structured_output_mode: "json_schema | json_mode | tool_call"
  }

  slo: {
    p50_latency_ms, p95_latency_ms,
    max_concurrency, cost_per_call
  }

  privacy_tier: "local | cloud_trusted | cloud_any"
  data_policy: "no_logging | retained_30d | ..."

  health: {
    status: ok | degraded | down,
    last_check, last_error,
    circuit_breaker: { open: bool, retry_at }
  }

  current_inflight: int
```

---

## Hosting environments

### Local Ollama hosts (LAN)

- One or more GPU hosts on the local network
- Serve VLM, chat/reasoner, and embedder models
- Keep-alive tuned to avoid model reload latency between calls
- Single-model-at-a-time constraint: if VLM and chat model both needed, either (a) one large GPU with sequential loading, (b) split hosts, or (c) vLLM/TGI for the VLM with Ollama for lighter models

### vLLM / TGI (local, heavier serving)

- Better throughput and micro-batching for the primary VLM under load
- Worth considering when event rate saturates Ollama's single-call model
- Tradeoff: more ops complexity vs. Ollama's simplicity

### Cloud providers (fallback + cloud-preferred tasks)

- Fallback when local queue depth exceeds threshold and event is cloud-eligible
- Cloud-preferred for: journey-close summaries, rule normalization, `deeper_assessment` escalation, ambiguous high-stakes decisions
- Session affinity: once a session has been reasoned about by a cloud backend, prefer the same backend for continuity and prompt-cache reuse

---

## Routing policy (evaluated in order)

### 1. Privacy gate (hard block)

Events tagged `local_only` never leave the LAN — period. Derived from camera location, time, recognized resident faces, interior cam flag. Router rejects rather than spills.

If no local backend can satisfy the request: conservative action + human notification. Never silently downgrade privacy.

### 2. Capability match

`supports_visual_reasoning` must match the pipeline's call-pattern requirement. A backend that can't return structured output is not eligible for hot-path VLM calls.

### 3. Local-first when healthy and not saturated

Latency, cost, and offline resilience all favor local. Route local unless:

- Local queue depth > threshold, OR
- Local backends are degraded/down, OR
- Event is explicitly `cloud-preferred`

### 4. Cloud spillover

When local is saturated and event is cloud-eligible: spill to cloud. Prioritize backends with matching `supports_visual_reasoning` and best p50 latency for the task.

### 5. Cloud-preferred tasks

Some tasks route to cloud by default regardless of local capacity:

- Journey-close summaries (quality gap significant)
- Rule normalization from natural language
- `deeper_assessment` escalation on high-stakes ambiguous events
- Deliberative background tasks (report generation, pattern mining)

### 6. Session affinity / prompt-cache stickiness

Once a session or multi-step task starts on a backend, route subsequent calls for the same session to the same backend. Maintains prompt-cache warmth (especially valuable on cloud — significant cost reduction) and reasoning consistency within a session.

---

## Health, circuit breakers, backoff

- **Health probe:** lightweight heartbeat per backend every 30s; immediate mark-down on timeout/error during real calls
- **Circuit breaker:** exponential backoff on error; open circuit after N consecutive failures; half-open probe after backoff window
- **Degraded state:** backend stays in rotation at reduced weight; router prefers healthy alternatives but doesn't fully exclude it
- **Recovery:** automatic on successful probe; no manual intervention required for transient failures

---

## Cost & rate-limit accounting

- Daily/monthly spend cap per cloud provider. Soft warn at 70%, hard cutoff at 100% — route everything local until reset.
- Per-provider RPM/TPM tracking. Router throttles before the provider rate-limits.
- Cost tracked per event_id for observability and tuning.
- Local backends: cost = 0 for accounting; GPU duty-cycle tracked separately (see §18).

---

## Prompt-cache strategy

Structured to maximize cloud cache hit rate (see §09 for full layout):

- System prompt (persona + home context + area description) = stable prefix = primary cache target
- Active situational contexts = secondary cache target (stable within a time window)
- Per-event content (rules, identity, frames) = variable tail = never cached

On cloud providers with explicit cache control (Anthropic `cache_control`): mark the stable prefix explicitly. On others (OpenAI): keep the prefix byte-identical across calls to trigger implicit caching.

Session affinity reinforces cache: same backend = same cache = cheaper cloud calls.

---

## Graceful degrade when internet is out

```
Cloud backend unreachable
        │
        ▼
Route to local backend with matching capability
        │
   local capable? ──yes──► proceed (single-call if visual_reasoning supported)
        │
       no (e.g. local VLM too small for visual reasoning)
        │
        ▼
Two-step fallback: local VLM describes, local text LLM reasons
        │
   still failing?
        │
        ▼
Detector-only verdict: enrichment JSON → rule match without LLM
(structured rules with explicit conditions only — no VLM judgment)
        │
        ▼
Conservative action + log + human notification of degraded mode
```

**Trust-critical actions** (unlock door, disarm alarm) are never taken by the detector-only fallback path. These require LLM reasoning or explicit human confirmation.

---

## Audit log

Every routing decision is logged:

```
RoutingAuditEntry:
  event_id, ts
  task, capability_requested
  backends_considered: [{ backend_id, reason_excluded }]
  backend_selected
  privacy_tier_of_event
  call_pattern_used: single_call | two_step | detector_only
  latency_ms, cost_usd
  outcome: success | timeout | error
  cloud_egress: bool   ← flagged for privacy audit
```

Logs are queryable for: "why did this event go to cloud?", "what was the cost of last week's storm events?", "how often did we fall back to two-step?"
