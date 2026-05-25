# Epic 15: Failure Modes & Resilience

**Architecture refs:** §19
**Components:** services/core, all services
**Priority:** P1
**Blocked by:** Epic 02 (need event bus to coordinate)

## Description

Implement the 10 failure modes from §19. Watchdogs, circuit breakers, safe defaults, recovery playbooks. Principle: degrade, don't fail. Every failure must be detected, isolated, logged, alerted, degraded gracefully, and recovered when possible.

## Definition of done

- All 10 documented failure modes have detection + degradation + recovery
- Safe defaults matrix enforced (which actions are still safe in each degraded mode)
- Watchdog process monitors critical components
- Circuit breakers on every external call
- User-visible health dashboard
- Recovery is automatic where possible; manual where not

## Issues

1. **feat(core): watchdog process** — monitors all services, restarts on crash, alerts on persistent failure. (labels: `epic:resilience`, `component:core`, `priority:p1`)
2. **feat(core): F1 — camera offline detection + recovery** — reconnect logic, in-flight session handling. (labels: `epic:resilience`, `component:core`, `priority:p1`)
3. **feat(core): F2 — RTSP stutter / packet loss tolerance** — frame-budget downshift, stream quality flag. (labels: `epic:resilience`, `component:core`, `priority:p1`)
4. **feat(core): F3 — NVR / DVR down handling** — proceed with live frames only, skip clip references. (labels: `epic:resilience`, `component:core`, `priority:p1`)
5. **feat(core): F4 — HA down handling** — cache snapshot, fail device actions cleanly, raise confidence thresholds. (labels: `epic:resilience`, `component:core`, `priority:p1`)
6. **feat(core): F5 — event bus down handling** — backpressure on ingress, watchdog restart attempt. (labels: `epic:resilience`, `component:core`, `priority:p1`)
7. **feat(core): F6 — GPU saturation load shedding** — frame budget reduction, background task pause, tier preemption. (labels: `epic:resilience`, `component:core`, `priority:p1`)
8. **feat(vlm-router): F7 — local VLM down circuit breaker** — try cloud fallback, then detector-only fallback. (labels: `epic:resilience`, `component:vlm-router`, `priority:p1`)
9. **feat(core): F8 — internet down handling** — cloud backends offline, queue uploads for later. (labels: `epic:resilience`, `component:core`, `priority:p2`)
10. **feat(memory): F9 — memory pressure handling** — auto-truncate vector DB, auto-archive sessions. (labels: `epic:resilience`, `component:memory`, `priority:p2`)
11. **feat(core): F10 — power loss / restart recovery** — hot state restoration on boot. (labels: `epic:resilience`, `component:core`, `priority:p2`)
12. **feat(core): safe defaults matrix enforcement** — per failure mode, which actions are still allowed. (labels: `epic:resilience`, `component:core`, `priority:p1`)
13. **feat(ha-cards): health dashboard card** — component status, last issues. (labels: `epic:resilience`, `component:frontend`, `priority:p1`)
14. **feat(core): diagnostic log + UI** — last 100 entries queryable. (labels: `epic:resilience`, `component:core`, `priority:p1`)
15. **test: chaos testing harness** — kill services, simulate network loss, verify degraded modes. (labels: `epic:resilience`, `component:core`, `priority:p2`)
