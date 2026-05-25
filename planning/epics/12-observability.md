# Epic 12: Observability & Diagnostics

**Architecture refs:** §17, §19
**Components:** services/core, frontend/operator-dashboard, frontend/ha-cards
**Priority:** P1
**Blocked by:** Epic 02 (need event bus telemetry)

## Description

Comprehensive instrumentation across intelligence + operations. Metrics taxonomy, three-level dashboard (overview, detailed, AI chat synthesis), feedback loops at multiple timescales, audience-specific views (homeowner / operator / developer).

## Definition of done

- Metric collection pipeline (per-rule, per-camera, per-component)
- Time-series storage (InfluxDB or equivalent)
- Per-event tracing (distributed trace IDs)
- Audit log immutable storage
- Overview dashboard (in HA cards)
- Detailed metrics dashboard (operator)
- AI synthesis layer (LLM-based root cause + recommendation)
- Replay tooling for re-running events against new rules
- Alerts for operational thresholds

## Issues

1. **feat(core): metric collection across all services** — Prometheus-style metrics, exposed via `/metrics`. (labels: `epic:observability`, `component:core`, `priority:p1`)
2. **chore(infra): time-series storage in docker-compose** — InfluxDB or Victoria Metrics. (labels: `epic:observability`, `component:infrastructure`, `priority:p1`)
3. **feat(shared): structured logging + trace ID propagation** — every event has a trace ID; logs are JSON; trace IDs propagate across services. (labels: `epic:observability`, `component:shared`, `priority:p1`)
4. **feat(memory): audit log append-only storage** — every cloud egress, every rule fire/dismiss, every identity decision. (labels: `epic:observability`, `component:memory`, `priority:p1`)
5. **feat(core): intelligence metrics** — rule performance, identity confidence, detection quality, VLM performance, user feedback patterns, optimization feedback. (labels: `epic:observability`, `component:core`, `priority:p1`)
6. **feat(core): operational metrics** — pipeline health, queue depths, resource utilization, component health, data freshness, cost tracking. (labels: `epic:observability`, `component:core`, `priority:p1`)
7. **feat(ha-cards): overview dashboard card** — status, alerts, top recommendations. (labels: `epic:observability`, `component:frontend`, `priority:p1`)
8. **feat(operator-dashboard): detailed metrics views** — per-rule histograms, drift charts, correlation. (labels: `epic:observability`, `component:frontend`, `priority:p2`)
9. **feat(core): AI synthesis layer** — LLM-based root cause analysis, impact estimation, ranked recommendations with explanations. (labels: `epic:observability`, `component:core`, `priority:p2`)
10. **feat(operator-dashboard): AI chat interface** — operator queries → synthesis layer → actionable recommendations. (labels: `epic:observability`, `component:frontend`, `priority:p2`)
11. **feat(core): replay tooling** — re-run archived event against new rules/prompts, compare to original. (labels: `epic:observability`, `component:core`, `priority:p2`)
12. **feat(core): alert thresholds for operational metrics** — FP rate > 0.50 for 7d → suggest optimization, GPU > 0.85 sustained → warning, etc. (labels: `epic:observability`, `component:core`, `priority:p1`)
13. **feat(core): per-event distributed trace** — ingress → triage → detector → VLM → reasoner → action, with timing breakdown. (labels: `epic:observability`, `component:core`, `priority:p1`)
14. **feat(core): cloud egress audit + UI** — every byte logged, queryable from user app. (labels: `epic:observability`, `component:core`, `priority:p1`)
