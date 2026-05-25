# Epic 09: Home Assistant Integration

**Architecture refs:** §07, ARCHITECTURE-CLARIFICATION.md
**Components:** services/ha-agent, ha-integration
**Priority:** P0
**Blocked by:** Epic 01, 02

## Description

Bidirectional HA integration. Service-side: `ha-agent` MCP server provides read access to HA state + write access to HA services. Integration-side: `ha-integration/custom_components/sentihome/` exposes SentiHome entities, services, and events to HA users and their dashboards.

## Definition of done

- `ha-agent` MCP server implements all read + write tools per §07
- HA custom integration installable + configurable via UI flow
- SentiHome state surfaces as HA entities (binary sensors, sensors, images, buttons, numbers)
- HA users can call SentiHome services (acknowledge_alert, run_optimization, label_person)
- SentiHome events fire on HA event bus (sentihome_alert, sentihome_feedback_complete, etc.)
- Custom HA cards render camera grid + alerts + identity gallery + rule status

## Issues

1. **feat(ha-agent): HA WebSocket/REST client wrapper** — auth via long-lived token, reconnect logic. (labels: `epic:ha-integration`, `component:ha-agent`, `priority:p0`)
2. **feat(ha-agent): `ha.get_snapshot` + `get_changes`** — full state cache + delta queries. (labels: `epic:ha-integration`, `component:ha-agent`, `priority:p0`)
3. **feat(ha-agent): `ha.get_area_resources`** — observational capabilities per area (lights, PTZ, etc.). (labels: `epic:ha-integration`, `component:ha-agent`, `priority:p1`)
4. **feat(ha-agent): `ha.get_calendar_events`** — calendar integration. (labels: `epic:ha-integration`, `component:ha-agent`, `priority:p2`)
5. **feat(ha-agent): `ha.list_capabilities`** — what HA integrations are connected. (labels: `epic:ha-integration`, `component:ha-agent`, `priority:p2`)
6. **feat(ha-agent): `ha.query(natural_language)` LLM-backed synthesis** — read across HA state, native AI alerts, third-party services. (labels: `epic:ha-integration`, `component:ha-agent`, `priority:p2`)
7. **feat(ha-agent): `ha.illuminate_area` / `darken_area` / `set_scene`** — auto-allowed write tools. (labels: `epic:ha-integration`, `component:ha-agent`, `priority:p0`)
8. **feat(ha-agent): `ha.lock` / `ha.unlock`** — policy-gated write tools. (labels: `epic:ha-integration`, `component:ha-agent`, `priority:p0`)
9. **feat(ha-agent): `ha.call_service` general-purpose service call** — with policy table enforcement. (labels: `epic:ha-integration`, `component:ha-agent`, `priority:p0`)
10. **feat(ha-agent): semantic area → entity-group resolution** — `illuminate_area("perimeter")` resolves to correct entity IDs. (labels: `epic:ha-integration`, `component:ha-agent`, `priority:p1`)
11. **feat(ha-integration): integration scaffolding** — manifest, config flow, coordinator. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p0`)
12. **feat(ha-integration): config flow (UI)** — connect to SentiHome instance, validate auth. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p0`)
13. **feat(ha-integration): binary sensor platform** — alert / detection binary sensors. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p0`)
14. **feat(ha-integration): sensor platform** — latest detection, confidence values, system health. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p0`)
15. **feat(ha-integration): image platform** — latest alert frame with annotations. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p1`)
16. **feat(ha-integration): button platform** — run_optimization, retrain_identity. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p1`)
17. **feat(ha-integration): number platform** — tunable thresholds. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p2`)
18. **feat(ha-integration): services** — `sentihome.acknowledge_alert`, `run_optimization`, `label_person`. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p1`)
19. **feat(ha-integration): event firing** — `sentihome_alert`, `sentihome_feedback_complete`, `sentihome_anomaly_detected`. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p1`)
20. **feat(ha-integration): WebSocket subscription for real-time updates** — push from SentiHome → HA entities. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p1`)
21. **test: HA integration tests against real HA instance** — config flow, entity creation, service calls. (labels: `epic:ha-integration`, `component:ha-integration`, `priority:p1`)
22. **docs: HA installation guide + example automations** — getting started for HA users. (labels: `epic:ha-integration`, `component:docs`, `priority:p1`)
