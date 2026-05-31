# Epic 16: Documentation & Onboarding

**Architecture refs:** all
**Components:** docs, scripts
**Priority:** P1

## Description

User-facing documentation, installation guides, getting started tutorials. The architecture docs already exist; this epic is about translating them into user-friendly guides for installation, configuration, and day-to-day use.

## Definition of done

- Install guide: zero-to-running in <30 min for a typical HA user
- Configuration guide: how to set up cameras, NVR adapters, rules, identity
- Day-to-day usage guide: dashboard, alerts, optimization workflow
- Troubleshooting guide: common issues + diagnostics
- Video walkthrough (optional but very useful for HA community)
- HACS-ready packaging for ha-integration

## Issues

1. **docs: installation guide** — HA add-on path + standalone docker-compose path. (labels: `epic:docs`, `component:docs`, `priority:p1`)
2. **docs: NVR adapter setup guide** — per-platform configuration with screenshots. (labels: `epic:docs`, `component:docs`, `priority:p1`)
3. **docs: first-rule walkthrough** — "Let me know when the mailman arrives" end-to-end. (labels: `epic:docs`, `component:docs`, `priority:p1`)
4. **docs: identity enrollment guide** — adding residents, labeling visitors. (labels: `epic:docs`, `component:docs`, `priority:p2`)
5. **docs: dashboard reference** — what every metric means, how to interpret. (labels: `epic:docs`, `component:docs`, `priority:p2`)
6. **docs: troubleshooting guide** — common issues + how to diagnose. (labels: `epic:docs`, `component:docs`, `priority:p1`)
7. **docs: HA blueprint library** — example automations on top of Kukii-Home events. (labels: `epic:docs`, `component:docs`, `priority:p2`)
8. **chore(packaging): HACS-ready manifest for ha-integration** — for community installation. (labels: `epic:docs`, `component:ha-integration`, `priority:p2`)
9. **chore(packaging): HA add-on packaging** — for installation through HA Supervisor. (labels: `epic:docs`, `component:infrastructure`, `priority:p2`)
10. **docs: video walkthrough (install + first rule)** — for the HA community. (labels: `epic:docs`, `component:docs`, `priority:p2`)
