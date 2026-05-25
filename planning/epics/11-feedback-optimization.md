# Epic 11: Feedback-Driven Optimization

**Architecture refs:** §10.5
**Components:** services/core
**Priority:** P1
**Blocked by:** Epic 04, 06, 07

## Description

Autonomous variant testing and safe rollout. When user provides ground truth (miss or false alarm), system generates rule/preprocessing variants and tests them against archived clips. Safe phased rollout (silent → shadow → gradual → full) with rollback triggers.

## Definition of done

- User feedback capture (miss reports, dismissals, confirmations)
- Variant generator covers all optimization knobs (preprocessing, frame selection, compute level, etc.)
- Replay testing framework runs variants against archived clips
- Variant ranking by FP/FN/cost on failure case + historical similar events
- Cross-scenario validation (variant doesn't break other rules)
- Phased rollout: silent (7d) → shadow (7d) → gradual (10/25/50/100% over 2w) → full
- Rollback triggers (FP increases, FN regresses, dismissals spike)
- Seasonal learning over multi-month timescales

## Issues

1. **feat(core): user feedback capture mechanism** — dismiss/confirm/miss-report with reason categorization. (labels: `epic:optimization`, `component:core`, `priority:p1`)
2. **feat(core): event archival with frames + original analysis + feedback** — durable storage for replay. (labels: `epic:optimization`, `component:core`, `priority:p1`)
3. **feat(core): variant generator — preprocessing knobs** — bbox markup, edge detection, contrast, background subtraction strategies. (labels: `epic:optimization`, `component:core`, `priority:p1`)
4. **feat(core): variant generator — frame selection knobs** — resolution, interval, adaptive sampling, motion-triggered. (labels: `epic:optimization`, `component:core`, `priority:p1`)
5. **feat(core): variant generator — compute level knobs** — VLM model size, detector model, enrichment density. (labels: `epic:optimization`, `component:core`, `priority:p2`)
6. **feat(core): variant generator — mode-specific knobs** — night mode, multi-subject, crop strategies. (labels: `epic:optimization`, `component:core`, `priority:p2`)
7. **feat(core): replay engine** — execute variant against archived clip, return prediction. (labels: `epic:optimization`, `component:core`, `priority:p1`)
8. **feat(core): variant metrics computation** — TP/FP/FN/precision/recall on failure case + historical similar events. (labels: `epic:optimization`, `component:core`, `priority:p1`)
9. **feat(core): variant ranking** — primary catches miss, secondary precision on similar events, tertiary resource cost. (labels: `epic:optimization`, `component:core`, `priority:p1`)
10. **feat(core): cross-scenario validation** — ensure variant doesn't break other rules. (labels: `epic:optimization`, `component:core`, `priority:p1`)
11. **feat(core): silent rollout phase (7d)** — variant runs in parallel; no user-visible change. (labels: `epic:optimization`, `component:core`, `priority:p1`)
12. **feat(core): shadow rollout phase (7d)** — variant fires alerts; user feedback validates. (labels: `epic:optimization`, `component:core`, `priority:p1`)
13. **feat(core): gradual rollout phase (2w, 10%→25%→50%→100%)** — cohort-based ramp. (labels: `epic:optimization`, `component:core`, `priority:p1`)
14. **feat(core): rollback triggers + auto-rollback** — FP↑>20%, FN↑, dismissals spike, compute exceeds budget. (labels: `epic:optimization`, `component:core`, `priority:p1`)
15. **feat(core): seasonal learning** — multi-quarter metric comparison, sensitivity adjustment. (labels: `epic:optimization`, `component:core`, `priority:p2`)
16. **feat(core): dismissal pattern clustering** — propose suppression rules when patterns detected. (labels: `epic:optimization`, `component:core`, `priority:p2`)
17. **test: replay framework determinism tests** — same variant + same clip + same context → same output. (labels: `epic:optimization`, `component:core`, `priority:p1`)
