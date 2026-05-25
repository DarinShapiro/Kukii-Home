# Epic 05: VLM Router & Inference

**Architecture refs:** §04, §09
**Components:** services/vlm-router
**Priority:** P0
**Blocked by:** Epic 01, 02

## Description

Multi-backend VLM router. Routes calls across local (Ollama, vLLM, TGI) and cloud backends based on routing policy. Enforces privacy tier constraints, applies circuit breaker for failing backends, tracks per-backend cost and latency.

## Definition of done

- Router registry knows all configured backends + their capabilities + privacy tiers
- VLM request schema validated; structured response schema enforced (per §09)
- Routing policy considers: capability fit, privacy tier, cost, health, affinity
- Circuit breaker opens on N consecutive failures, half-opens on cooldown
- Privacy tier enforcement: local_only data NEVER routed to cloud
- Cost + latency telemetry per backend
- Fallback chains work end-to-end

## Issues

1. **feat(vlm-router): backend registry + config schema** — supports Ollama, vLLM, TGI, OpenAI-compatible cloud APIs. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p0`)
2. **feat(vlm-router): Ollama backend driver** — async client, vision model support. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p0`)
3. **feat(vlm-router): vLLM backend driver** — alternative local inference. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p1`)
4. **feat(vlm-router): cloud backend driver (OpenAI-compatible)** — Anthropic, OpenAI, etc. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p1`)
5. **feat(vlm-router): routing policy engine** — capability + privacy + cost + health + affinity scoring. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p0`)
6. **feat(vlm-router): privacy tier enforcement** — hard reject local_only data going to cloud backends. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p0`)
7. **feat(vlm-router): circuit breaker per backend** — open on N consecutive failures, half-open cooldown. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p1`)
8. **feat(vlm-router): fallback chain execution** — try next backend on failure within policy budget. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p1`)
9. **feat(vlm-router): per-backend cost + latency telemetry** — surfaces to observability. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p1`)
10. **feat(shared): VLM request schema** — frames, context, persona, output schema constraints (per §09). (labels: `epic:vlm-router`, `component:shared`, `priority:p0`)
11. **feat(shared): VLM response schema** — structured decision JSON with criticality, confidence, rules_fired, limiting_factors. (labels: `epic:vlm-router`, `component:shared`, `priority:p0`)
12. **feat(vlm-router): response validation + repair** — if VLM returns malformed JSON, attempt one repair before failing. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p1`)
13. **test: router integration tests** — mock backends, routing decisions, circuit breaker, privacy enforcement. (labels: `epic:vlm-router`, `component:vlm-router`, `priority:p0`)
