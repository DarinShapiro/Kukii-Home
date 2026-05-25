# frontend/

TypeScript frontend code: custom Home Assistant cards and an optional standalone operator dashboard for advanced diagnostics.

## Subprojects

| Folder | Purpose | Stack |
|--------|---------|-------|
| [`ha-cards/`](ha-cards/) | Custom HA Lovelace cards (camera grid, alerts, identity gallery, optimization status) | Lit + TypeScript + Vite |
| [`operator-dashboard/`](operator-dashboard/) | Standalone web dashboard for operators (deep diagnostics, AI chat synthesis, replay tooling) — optional; falls back to HA if not deployed | React + TypeScript + Vite |

## Why two surfaces?

HA dashboards cover the homeowner UX (simple, trustworthy, actionable). The operator dashboard targets power users / developers who want deeper diagnostics: per-rule precision/recall histograms, variant testing rollouts, identity confidence drift, end-to-end traces, AI chat interface for root cause analysis.

For most users, HA cards are enough. The operator dashboard is opt-in.

## Architecture references

- [§17 Observability & Operations](../docs/architecture/17-observability.md) — what the dashboards display
- [§15 Alerting & Action Policy](../docs/architecture/15-alerting-and-actions.md) — alert tiers + UX
- [§12 Recognition & Identity](../docs/architecture/12-recognition-and-identity.md) — identity gallery UX

## Conventions

- **Language:** TypeScript strict mode
- **Build:** Vite for both projects
- **Component library:** Lit for HA cards (interop with HA's frontend), React for standalone dashboard
- **Styling:** CSS modules or Tailwind (TBD per project)
- **API client:** generated from `shared/schemas/` OpenAPI specs
- **Testing:** Vitest + Playwright for e2e
- **Accessibility:** WCAG AA target

## HA card distribution

Custom HA cards are published via HACS frontend section. See [`ha-cards/README.md`](ha-cards/) for build + publish flow.
