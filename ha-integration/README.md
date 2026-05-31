# ha-integration/

Home Assistant custom integration that surfaces Kukii-Home to HA users. This is the bridge between Kukii-Home's intelligence layer and HA's device/UX layer.

Architecture: [§07 Tool Layer (MCP)](../docs/architecture/07-tool-layer-mcp.md), [ARCHITECTURE-CLARIFICATION.md](../docs/ARCHITECTURE-CLARIFICATION.md)

## What this integration provides

### Entities exposed to HA

- **Binary sensors:** `binary_sensor.kukiihome_person_at_door`, `binary_sensor.kukiihome_unknown_visitor`, etc.
- **Sensors:** `sensor.kukiihome_latest_detected_person`, `sensor.kukiihome_gpu_utilization`, `sensor.kukiihome_rule_<name>_confidence`, etc.
- **Image entities:** `image.kukiihome_latest_alert_frame` (with annotations)
- **Buttons:** `button.kukiihome_run_optimization`, etc.
- **Numbers:** `number.kukiihome_detection_threshold`, etc.

### Services callable from HA automations

- `kukiihome.acknowledge_alert` — dismiss / confirm / forward an alert
- `kukiihome.run_optimization` — trigger feedback optimization on a rule
- `kukiihome.label_person` — label a face for identity learning

### Events emitted to HA event bus

- `kukiihome_alert` — fired when a rule triggers
- `kukiihome_feedback_complete` — fired when optimization rollout completes
- `kukiihome_anomaly_detected` — fired when observability flags an anomaly

## Important: rules live in Kukii-Home, not HA

This integration **exposes** Kukii-Home state and **executes** device actions on Kukii-Home's behalf — it is **not** where rules live. Conversational rule creation happens in Kukii-Home's core, against Kukii-Home's rule engine. HA automations are optional user extensions on top of Kukii-Home events.

See [ARCHITECTURE-CLARIFICATION.md](../docs/ARCHITECTURE-CLARIFICATION.md) for the full explanation.

## Layout

```
ha-integration/
├── custom_components/
│   └── kukiihome/
│       ├── __init__.py          Integration entry point
│       ├── manifest.json        HA integration manifest
│       ├── config_flow.py       UI-based config flow
│       ├── const.py             Constants
│       ├── coordinator.py       DataUpdateCoordinator
│       ├── api.py               Kukii-Home REST/WS client
│       ├── binary_sensor.py     Binary sensor platform
│       ├── sensor.py            Sensor platform
│       ├── image.py             Image platform
│       ├── button.py            Button platform
│       ├── number.py            Number platform
│       ├── services.yaml        Service definitions
│       └── translations/        i18n
└── README.md
```

## Installation (target end state)

```bash
# Via HACS (eventually)
HACS → Integrations → Custom repositories → DarinShapiro/Kukii-Home
HACS → Install Kukii-Home

# Manual
cp -r ha-integration/custom_components/kukiihome \
   /config/custom_components/kukiihome
# Restart HA, add via Settings → Devices & Services → Add Integration
```

## Conventions

- **HA version:** target 2026.5+ (uses choose selector, custom dashboard strategies)
- **Async only:** all I/O is async per HA conventions
- **Polling:** uses DataUpdateCoordinator for entity state; WebSocket push for events
- **Config flow:** UI-based; no YAML configuration required
- **Translations:** at minimum English; community PRs welcome for other languages
