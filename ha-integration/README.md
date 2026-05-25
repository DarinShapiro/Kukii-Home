# ha-integration/

Home Assistant custom integration that surfaces SentiHome to HA users. This is the bridge between SentiHome's intelligence layer and HA's device/UX layer.

Architecture: [§07 Tool Layer (MCP)](../docs/architecture/07-tool-layer-mcp.md), [ARCHITECTURE-CLARIFICATION.md](../docs/ARCHITECTURE-CLARIFICATION.md)

## What this integration provides

### Entities exposed to HA

- **Binary sensors:** `binary_sensor.sentihome_person_at_door`, `binary_sensor.sentihome_unknown_visitor`, etc.
- **Sensors:** `sensor.sentihome_latest_detected_person`, `sensor.sentihome_gpu_utilization`, `sensor.sentihome_rule_<name>_confidence`, etc.
- **Image entities:** `image.sentihome_latest_alert_frame` (with annotations)
- **Buttons:** `button.sentihome_run_optimization`, etc.
- **Numbers:** `number.sentihome_detection_threshold`, etc.

### Services callable from HA automations

- `sentihome.acknowledge_alert` — dismiss / confirm / forward an alert
- `sentihome.run_optimization` — trigger feedback optimization on a rule
- `sentihome.label_person` — label a face for identity learning

### Events emitted to HA event bus

- `sentihome_alert` — fired when a rule triggers
- `sentihome_feedback_complete` — fired when optimization rollout completes
- `sentihome_anomaly_detected` — fired when observability flags an anomaly

## Important: rules live in SentiHome, not HA

This integration **exposes** SentiHome state and **executes** device actions on SentiHome's behalf — it is **not** where rules live. Conversational rule creation happens in SentiHome's core, against SentiHome's rule engine. HA automations are optional user extensions on top of SentiHome events.

See [ARCHITECTURE-CLARIFICATION.md](../docs/ARCHITECTURE-CLARIFICATION.md) for the full explanation.

## Layout

```
ha-integration/
├── custom_components/
│   └── sentihome/
│       ├── __init__.py          Integration entry point
│       ├── manifest.json        HA integration manifest
│       ├── config_flow.py       UI-based config flow
│       ├── const.py             Constants
│       ├── coordinator.py       DataUpdateCoordinator
│       ├── api.py               SentiHome REST/WS client
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
HACS → Integrations → Custom repositories → DarinShapiro/SentiHome
HACS → Install SentiHome

# Manual
cp -r ha-integration/custom_components/sentihome \
   /config/custom_components/sentihome
# Restart HA, add via Settings → Devices & Services → Add Integration
```

## Conventions

- **HA version:** target 2026.5+ (uses choose selector, custom dashboard strategies)
- **Async only:** all I/O is async per HA conventions
- **Polling:** uses DataUpdateCoordinator for entity state; WebSocket push for events
- **Config flow:** UI-based; no YAML configuration required
- **Translations:** at minimum English; community PRs welcome for other languages
