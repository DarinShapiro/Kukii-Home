# Epic 08: Action Dispatch & Alerting

**Architecture refs:** §15, §06
**Components:** services/core, services/notify, services/ha-agent
**Priority:** P0
**Blocked by:** Epic 02, 07, 09

## Description

How decisions become user-visible actions. Confidence tiers (0–4) route alerts to silent log, in-app, push, wake call, or automated emergency. Device actions dispatched to HA services. Conversational `ask` for ambiguous events. Last-responder bias mitigation. Quiet hours and occupancy-aware routing.

## Definition of done

- Five confidence tiers implemented with escalation rules
- Occupancy-aware routing (who's home, who to notify, when to escalate)
- Quiet hours respected per resident
- Conversational `ask` flow with pipeline suspend/resume
- Autonomous action policy: auto-allowed, policy-gated, hard-blocked
- Pre-approval rules for device actions
- Action dispatcher calls HA services for execution
- Alert acknowledgment + feedback loop

## Issues

1. **feat(core): action dispatcher** — reads VLM decision, evaluates rules, emits action plan. (labels: `epic:action-dispatch`, `component:core`, `priority:p0`)
2. **feat(core): confidence tier router (0–4)** — silent / in-app / push / wake / emergency. (labels: `epic:action-dispatch`, `component:core`, `priority:p0`)
3. **feat(core): tier escalation engine** — timeouts, follow-up detections, unanswered escalations. (labels: `epic:action-dispatch`, `component:core`, `priority:p0`)
4. **feat(core): quiet hours + occupancy-aware routing** — per-resident preferences, who's home detection via HA. (labels: `epic:action-dispatch`, `component:core`, `priority:p1`)
5. **feat(core): last-responder bias mitigation** — delegation + confirmation tracking when multiple residents alerted. (labels: `epic:action-dispatch`, `component:core`, `priority:p2`)
6. **feat(core): per-resident DND + preferences** — vacation mode, emergency-only, preferred contact channel. (labels: `epic:action-dispatch`, `component:core`, `priority:p1`)
7. **feat(notify): push notification dispatcher** — via HA companion app. (labels: `epic:action-dispatch`, `component:notify`, `priority:p0`)
8. **feat(notify): TTS speaker delivery** — via HA media_player / TTS integration. (labels: `epic:action-dispatch`, `component:notify`, `priority:p1`)
9. **feat(notify): conversational ask flow** — pose question, register callback, suspend pipeline, resume on response or timeout. (labels: `epic:action-dispatch`, `component:notify`, `priority:p1`)
10. **feat(core): autonomous action policy enforcement** — auto-allowed, policy-gated (require pre-approval or ask), hard-blocked. (labels: `epic:action-dispatch`, `component:core`, `priority:p0`)
11. **feat(core): pre-approval rule mechanism** — user can grant "unlock door for Sarah" pre-approval. (labels: `epic:action-dispatch`, `component:core`, `priority:p2`)
12. **feat(core): action explanation generator** — every alert cites rules fired, has direct edit path. (labels: `epic:action-dispatch`, `component:core`, `priority:p1`)
13. **feat(core): alert acknowledgment + feedback loop** — dismiss/confirm/forward, feeds into §10.5 optimization. (labels: `epic:action-dispatch`, `component:core`, `priority:p1`)
14. **feat(core): remediation registry** — maps `confidence_limiting_factors` + `area_resources` to environmental actions (per §06). (labels: `epic:action-dispatch`, `component:core`, `priority:p1`)
15. **feat(core): deeper-assessment loop** — second VLM call after environmental remediation (lights on, PTZ slew). (labels: `epic:action-dispatch`, `component:core`, `priority:p1`)
16. **test: action dispatch integration tests** — tier escalation, ask flow, policy enforcement. (labels: `epic:action-dispatch`, `component:core`, `priority:p1`)
