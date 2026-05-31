# Installing Kukii-Home

Kukii-Home ships as **two install steps**: an HA Supervisor **add-on** (the
backend services) and an HA **custom integration** (the entities + UI inside
Home Assistant).

## Step 1: Install the add-on

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**
2. Click the **⋮** menu in the top right → **Repositories**
3. Paste `https://github.com/DarinShapiro/Kukii-Home` and click **Add**
4. Close the dialog, refresh — the **Kukii-Home** tile appears in the store
5. Click the tile, then **Install**
6. After install, open the **Configuration** tab:
   - Pick a `profile` (`yellow_single_box`, `yellow_plus_inference`, or `distributed`)
   - Optionally paste full nested topology YAML for `bus`, `memory`, `vlm_router`, `notify`, `adapters`
   - See [`infrastructure/docker/kukiihome.example.yaml`](../infrastructure/docker/kukiihome.example.yaml) for an annotated starter
7. **Save**, then go back to **Info** → **Start**
8. Enable **Start on boot** and **Watchdog** so it survives HA reboots

The add-on logs (Settings → Add-ons → Kukii-Home → Log) will print
`[bootstrap]` and per-service startup lines. Wait for `bus.nats`,
`memory.postgres`, etc. probes to show `OK`.

## Step 2: Install the custom integration

The integration exposes Kukii-Home state into HA as entities, services, and
events. Two install options:

### Option A: HACS (recommended)

1. In HACS, open the **⋮** menu → **Custom repositories**
2. Add `https://github.com/DarinShapiro/Kukii-Home` with category **Integration**
3. Search HACS for **Kukii-Home** and click **Download**
4. Restart Home Assistant

### Option B: Manual

1. Copy `ha-integration/custom_components/kukiihome/` from this repo into
   your HA `config/custom_components/` directory
2. Restart Home Assistant

### Configure the integration

1. Settings → Devices & Services → **+ Add Integration**
2. Search for **Kukii-Home**
3. Enter:
   - **Host**: `homeassistant.local` (or `localhost` if HA is on the same machine; or the LAN IP if the add-on runs elsewhere)
   - **Port**: `8765` (the ha-agent HTTP API)
   - **Poll interval**: `10` seconds (default)
4. Click **Submit** — the integration appears under Devices & Services with
   the Kukii-Home entities populated

## What you get

After both steps, your HA instance has:

- `binary_sensor.kukiihome_online` — coordinator health
- `binary_sensor.kukiihome_alert_active` — fires when an unacknowledged alert is present
- `sensor.kukiihome_latest_alert` — latest alert headline + attributes
- `sensor.kukiihome_recent_alerts` — count of recent alerts
- `sensor.kukiihome_ha_capabilities` — domains Kukii-Home sees in HA
- `image.kukiihome_latest_alert_frame` — latest alert evidence frame
- `button.kukiihome_run_optimization`, `button.kukiihome_retrain_identity`
- `number.kukiihome_global_confidence_threshold`
- Services: `kukiihome.acknowledge_alert`, `kukiihome.run_optimization`, `kukiihome.label_person`
- Events: `kukiihome_alert`, `kukiihome_feedback_complete`, `kukiihome_anomaly_detected`

Build automations / Lovelace dashboards on top of these.

## Troubleshooting

- **Add-on installs but won't start:** check the **Log** tab. Most failures
  are topology validation errors — the loader prints one line per problem.
- **Integration can't connect:** confirm the add-on is running and port
  8765 is reachable. From inside HA: `Developer Tools → Services →
curl_command` to GET `http://localhost:8765/healthz`.
- **No Kukii-Home entities:** the coordinator only populates after the first
  successful poll. Check Settings → Devices → Kukii-Home → "Logs" tab.
- **Push notifications don't fire:** the add-on's `notify` section must map
  each resident to their `notify.mobile_app_<device>` service (install the
  HA Companion app on the phone first).
