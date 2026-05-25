# Epic 02: Event Bus & Messaging

**Architecture refs:** §03, §05
**Components:** infrastructure, shared, services/core
**Priority:** P0
**Blocked by:** Epic 01
**Blocks:** Epic 03, 04, 05, 07, 08

## Description

The event bus is the spinal cord of the system. NATS JetStream provides durable streams, tiered priority lanes, and backpressure. Every event in the system flows through here: camera detections, HA state changes, VLM requests/responses, action dispatches.

## Definition of done

- NATS JetStream stack runs in docker-compose with declared streams + consumers
- Event schemas are versioned and validated at publish time
- Priority lanes (`vlm.urgent`, `vlm.normal`, `vlm.background`, `sensor.bypass`) route correctly
- Backpressure handling and load shedding work as documented in §03
- Publisher and subscriber libraries exist in `shared/lib-python` with clean async APIs

## Issues

1. **chore(infra): NATS JetStream container in docker-compose** — single-node config sufficient for dev. (labels: `epic:event-bus`, `component:infrastructure`, `priority:p0`)
2. **chore(infra): declare JetStream streams + consumers as YAML** — applied via `nats` CLI on bootstrap. (labels: `epic:event-bus`, `component:infrastructure`, `priority:p0`)
3. **feat(shared): NATS async publisher with schema validation** — typed wrapper, validates against JSON Schema before publish. (labels: `epic:event-bus`, `component:shared`, `priority:p0`)
4. **feat(shared): NATS async subscriber with handler registration** — typed callbacks, deserialize and validate. (labels: `epic:event-bus`, `component:shared`, `priority:p0`)
5. **feat(shared): event schemas per §05** — trigger event, enriched event, VLM request/response, reasoner request/response, session/journey updates, action/notification messages. (labels: `epic:event-bus`, `component:shared`, `priority:p0`)
6. **feat(core): tiered queue routing** — `vlm.urgent`, `vlm.normal`, `vlm.background`, `sensor.bypass` per §03. (labels: `epic:event-bus`, `component:core`, `priority:p0`)
7. **feat(core): load-shedding policy** — frame budget reduction, enrichment downshift, priority preemption as documented in §03. (labels: `epic:event-bus`, `component:core`, `priority:p1`)
8. **feat(core): backpressure handling** — bounded queues, drop policies per tier, observability for dropped messages. (labels: `epic:event-bus`, `component:core`, `priority:p1`)
9. **test: event bus integration tests** — publish/subscribe round-trip, schema rejection, priority ordering. (labels: `epic:event-bus`, `component:infrastructure`, `priority:p0`)
