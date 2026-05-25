# services/ha-agent/

Bidirectional MCP server for Home Assistant. Read side (LLM-backed synthesis over HA's full ecosystem) + write side (direct device commands, sub-second execution).

**Architecture:** [§07](../../docs/architecture/07-tool-layer-mcp.md), [ARCHITECTURE-CLARIFICATION.md](../../docs/ARCHITECTURE-CLARIFICATION.md)

## Responsibilities

### Read side

- `ha.get_snapshot()` — full entity state cache
- `ha.get_changes(since_ts)` — delta queries
- `ha.get_area_resources(area_id)` — observational capabilities per area
- `ha.get_calendar_events(...)` — calendar integration
- `ha.list_capabilities()` — what's connected (calendar, weather, energy, etc.)
- `ha.query(natural_language)` — LLM-backed synthesis over HA state, native AI alerts, third-party services

### Write side

- `ha.illuminate_area(...)`, `ha.darken_area(...)`, `ha.set_scene(...)`
- `ha.lock(...)`, `ha.unlock(...)` (policy-gated)
- `ha.call_service(domain, service, entity_id, data)` — general HA service call
- `ha.get_entity_state(...)` — single-entity state fetch for confirmation

## Important

This service is the boundary between SentiHome (intelligence + rules) and HA (devices + UX). It does **not** define rules or trigger automations — SentiHome's core handles those and calls write-side tools here to execute actions.

## Status

Skeleton. Implementation tracked in [`planning/epics/09-ha-integration.md`](../../planning/epics/).
