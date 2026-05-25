# Epic 07: Rule Engine & Conversational Rule Creation

**Architecture refs:** §10
**Components:** services/core
**Priority:** P0
**Blocked by:** Epic 02, 05, 06

## Description

The rule engine is the heart of SentiHome's intelligence: how rules are represented, retrieved, evaluated, and created conversationally. Rules live in SentiHome (not HA). Conversational creation uses an LLM to parse intent; rule evaluation is deterministic.

## Definition of done

- Rule record schema implemented per §10
- Hybrid retrieval (SQL filter + ANN rank) returns top-K rules per event
- Rule conflict resolution algorithm (scope specificity + severity hierarchy) implemented
- Conversational rule creation: user message → structured rule
- Rule lifecycle: create, edit, suppress, decay
- Default rule pack ships
- Rule editing via NL or structured updates

## Issues

1. **feat(core): rule record data model + validation** — full schema per §10. (labels: `epic:rule-engine`, `component:core`, `priority:p0`)
2. **feat(core): hybrid rule retrieval** — calls into `memory.retrieve_rules`, applies budget. (labels: `epic:rule-engine`, `component:core`, `priority:p0`)
3. **feat(core): rule condition evaluator** — temporal, subject, location, context conditions. (labels: `epic:rule-engine`, `component:core`, `priority:p0`)
4. **feat(core): rule conflict resolution algorithm** — scope specificity (zone > camera > area > journey > composite > global), severity hierarchy (max wins). (labels: `epic:rule-engine`, `component:core`, `priority:p0`)
5. **feat(core): conversational rule creation** — LLM parses NL into structured rule; confirmation flow before deploy. (labels: `epic:rule-engine`, `component:core`, `priority:p0`)
6. **feat(core): rule testing against archived clips** — before deploying a new rule, optionally test against recent matching events. (labels: `epic:rule-engine`, `component:core`, `priority:p1`)
7. **feat(core): rule editing via NL** — "make this rule only fire at night" → structured update. (labels: `epic:rule-engine`, `component:core`, `priority:p1`)
8. **feat(core): suppression and dismissal counters** — `suppress_until`, `dismiss_count_24h`, decay logic. (labels: `epic:rule-engine`, `component:core`, `priority:p1`)
9. **feat(core): agent-proposed suppression rules** — when same rule dismissed N times in 24h, propose a suppression. (labels: `epic:rule-engine`, `component:core`, `priority:p2`)
10. **feat(core): default rule pack** — Tier-1 safety, delivery confirmation, known guest arrival, pool monitoring, unanswered knock. (labels: `epic:rule-engine`, `component:core`, `priority:p1`)
11. **test: rule engine integration tests** — conflict scenarios, NL parsing, condition evaluation. (labels: `epic:rule-engine`, `component:core`, `priority:p1`)
