# planning/

Source of truth for the implementation roadmap. Each epic is a markdown file under [`epics/`](epics/) that captures the epic body and all sub-issues. These get pushed to GitHub Issues via `scripts/dev/sync-issues.sh` (or were on initial repo setup).

## How epics work

GitHub doesn't have first-class epics on free accounts. We use the convention:

- **Epic = parent issue** with label `type:epic` and a task list of sub-issues
- **Sub-issues** are individual implementation tasks, labeled with `epic:<slug>` and the relevant `component:*`
- Sub-issues check off in the epic's task list as they close

## Files

| File                                                                         | Epic                                       |
| ---------------------------------------------------------------------------- | ------------------------------------------ |
| [`epics/01-foundation.md`](epics/01-foundation.md)                           | Project Foundation & Infrastructure        |
| [`epics/02-event-bus.md`](epics/02-event-bus.md)                             | Event Bus & Messaging                      |
| [`epics/03-nvr-adapters.md`](epics/03-nvr-adapters.md)                       | NVR Adapter Layer                          |
| [`epics/04-preprocessing-detection.md`](epics/04-preprocessing-detection.md) | Preprocessing & Detection                  |
| [`epics/05-vlm-router.md`](epics/05-vlm-router.md)                           | VLM Router & Inference                     |
| [`epics/06-memory-storage.md`](epics/06-memory-storage.md)                   | Memory & Storage                           |
| [`epics/07-rule-engine.md`](epics/07-rule-engine.md)                         | Rule Engine & Conversational Rule Creation |
| [`epics/08-action-dispatch.md`](epics/08-action-dispatch.md)                 | Action Dispatch & Alerting                 |
| [`epics/09-ha-integration.md`](epics/09-ha-integration.md)                   | Home Assistant Integration                 |
| [`epics/10-identity-recognition.md`](epics/10-identity-recognition.md)       | Identity & Recognition                     |
| [`epics/11-feedback-optimization.md`](epics/11-feedback-optimization.md)     | Feedback-Driven Optimization               |
| [`epics/12-observability.md`](epics/12-observability.md)                     | Observability & Diagnostics                |
| [`epics/13-privacy-governance.md`](epics/13-privacy-governance.md)           | Privacy & Governance                       |
| [`epics/14-calibration-spatial.md`](epics/14-calibration-spatial.md)         | Calibration & Spatial Model                |
| [`epics/15-failure-modes.md`](epics/15-failure-modes.md)                     | Failure Modes & Resilience                 |
| [`epics/16-docs-onboarding.md`](epics/16-docs-onboarding.md)                 | Documentation & Onboarding                 |

## Epic file format

Each file follows this structure:

```markdown
# Epic: <Title>

**Architecture refs:** §XX, §YY
**Component(s):** core, preprocessor, ...
**Priority:** P0 / P1 / P2

## Description

Why this epic exists. What "done" looks like.

## Issues

1. **<title>** — short description (labels: ...)
2. **<title>** — short description
3. ...

## Dependencies

- Blocks: epic-N (which epics depend on this one)
- Blocked by: epic-N (which epics this one depends on)
```

## Workflow

1. Add or edit an epic markdown file
2. Run `./scripts/dev/sync-issues.sh` to push new issues to GitHub (creates new; doesn't update closed)
3. Track work in GitHub Issues; close issues as they finish; the parent epic's checklist updates automatically
4. When all sub-issues close, close the epic
