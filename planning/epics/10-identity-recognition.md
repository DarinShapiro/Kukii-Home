# Epic 10: Identity & Recognition

**Architecture refs:** §12, §12.5
**Components:** services/memory, services/detector, services/core
**Priority:** P1
**Blocked by:** Epic 04, 06

## Description

Face recognition pipeline, body re-ID, multi-camera identity fusion, behavioral biometrics, temporal evidence accumulation, retroactive re-evaluation. The system that builds confident identity over time across multiple cameras and signal types.

## Definition of done

- Face recognition pipeline with multi-frame aggregation
- Per-tier confidence thresholds (high confidence, tentative, low)
- Body re-ID for in-session correlation
- Cross-day composite identity (face + plate + behavioral + height + gait)
- IdentityResolution records with multiple candidates + evidence
- Multi-camera fusion with strategic overlapping placement
- Calibrated stereo verification (where available)
- Behavioral biometric profiles per person, per camera
- Retroactive re-evaluation on label
- Identity gallery management UX

## Issues

1. **feat(core): face recognition orchestrator** — detection → quality gates → embedding → gallery match → multi-frame aggregation. (labels: `epic:identity`, `component:core`, `priority:p1`)
2. **feat(core): per-tier confidence thresholds** — high (>=0.60), tentative (>=0.55), low (no claim). (labels: `epic:identity`, `component:core`, `priority:p1`)
3. **feat(core): body re-ID in-session correlation** — temporal continuity, exclusion checking. (labels: `epic:identity`, `component:core`, `priority:p1`)
4. **feat(memory): IdentityResolution record schema** — multiple candidates with confidence + evidence sources. (labels: `epic:identity`, `component:memory`, `priority:p1`)
5. **feat(core): multi-modal identity matching** — face + plate + behavioral + height + clothing + gait. (labels: `epic:identity`, `component:core`, `priority:p2`)
6. **feat(core): multi-camera identity fusion** — combine signals across overlapping cameras. (labels: `epic:identity`, `component:core`, `priority:p1`)
7. **feat(core): same-person verification** — temporal plausibility + geometric consistency. (labels: `epic:identity`, `component:core`, `priority:p1`)
8. **feat(core): stereo face verification (when calibrated)** — 3D triangulation, face geometric consistency. (labels: `epic:identity`, `component:core`, `priority:p2`)
9. **feat(core): temporal evidence accumulation** — confidence compounds over multiple observations. (labels: `epic:identity`, `component:core`, `priority:p1`)
10. **feat(core): cross-camera behavioral profiles** — greeting, gait, clothing patterns per person per camera. (labels: `epic:identity`, `component:core`, `priority:p2`)
11. **feat(core): retroactive re-evaluation on label** — when user labels someone, re-analyze past detections. (labels: `epic:identity`, `component:core`, `priority:p2`)
12. **feat(core): conflict detection** — surface uncertainty when signals disagree (e.g., face match but gait mismatch). (labels: `epic:identity`, `component:core`, `priority:p2`)
13. **feat(memory): identity gallery management** — add/edit/delete known actors, promote candidates. (labels: `epic:identity`, `component:memory`, `priority:p1`)
14. **feat(memory): auto-enrollment for frequent unknowns** — propose adding after N detections. (labels: `epic:identity`, `component:memory`, `priority:p2`)
15. **feat(memory): drift detection** — compare oldest vs newest embeddings, prompt re-enrollment. (labels: `epic:identity`, `component:memory`, `priority:p2`)
16. **feat(memory): pet recognition gallery** — separate from human gallery, face + coat pattern. (labels: `epic:identity`, `component:memory`, `priority:p2`)
17. **test: identity recognition test suite** — confusion matrices, multi-camera fusion accuracy. (labels: `epic:identity`, `component:core`, `priority:p1`)
