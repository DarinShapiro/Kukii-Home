# services/core/

The SentiHome orchestration brain: triage worker, rule engine, action dispatcher, session manager, attention mode manager.

**Architecture:** [§02](../../docs/architecture/02-high-level-architecture.md), [§06](../../docs/architecture/06-agent-orchestration.md), [§10](../../docs/architecture/10-rule-schema-and-retrieval.md), [§15](../../docs/architecture/15-alerting-and-actions.md)

## Responsibilities

- Subscribe to event bus topics (`vlm.urgent`, `vlm.normal`, `vlm.background`, `sensor.bypass`)
- Triage worker: dedup, score, route to priority tier
- Context assembly (parallel: rules, HA world state, identity, episodes)
- Hand off to VLM router for inference
- Receive structured decision JSON, evaluate against rules
- Action dispatcher: call HA services, open sessions, trigger attention modes
- Session/journey manager: stitch multi-camera segments
- Remediation registry: map limiting factors to environmental actions

## Not responsible for

- Frame acquisition (that's NVR adapter)
- VLM inference (that's vlm-router)
- Memory storage (that's memory service)
- Device control (that's HA via ha-agent)

## Entry point

`python -m sentihome.core` (TBD)

## Status

Skeleton. Implementation tracked in [`planning/epics/`](../../planning/epics/).
