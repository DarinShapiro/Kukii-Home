# SentiHome add-on configuration

The Supervisor add-on UI shows a top-level form mapped to the SentiHome
topology config schema. Field-level reference:

## Top-level options

| Option          | Description                                                                                                                                                                                                                                                                                                                    | Default             |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------- |
| `profile`       | Deployment shape: `yellow_single_box`, `yellow_plus_inference`, or `distributed`                                                                                                                                                                                                                                               | `yellow_single_box` |
| `household_id`  | Stable identifier used in logs + metrics                                                                                                                                                                                                                                                                                       | `my_home`           |
| `timezone`      | IANA tz name, e.g. `America/New_York`                                                                                                                                                                                                                                                                                          | `UTC`               |
| `ha_token`      | **Leave empty in normal add-on use.** Supervisor injects `SUPERVISOR_TOKEN` automatically and that's what `http://supervisor/core` accepts. Long-lived access tokens from HA's user UI only work against HA Core directly — if you want to use one, ALSO change a `ha_url` override to e.g. `http://homeassistant.local:8123`. | _empty_             |
| `log_level`     | `DEBUG` / `INFO` / `WARNING` / `ERROR`                                                                                                                                                                                                                                                                                         | `INFO`              |
| `auto_discover` | Zero-config camera onboarding (v0.3.11+). When ON, the add-on auto-discovers HA cameras and AI-picks the best stream + motion sensors per device. Per-device overrides live in the Web UI — no YAML editing required.                                                                                                          | `true`              |

## HA notifications (v0.3.12+)

To get pushed when an alert fires, add to the add-on Configuration
(YAML mode):

```yaml
notify:
  alert_services:
    - notify.mobile_app_YOUR_DEVICE
    # Add more services for fan-out:
    # - notify.alexa_media_kitchen
```

Each alert fans out concurrently to every service. Payload includes
title (the alert headline), message (classification + camera +
timestamp), a link to the SentiHome status page, and the snapshot
image (HA Companion app renders it inline). Empty list = no
notifications (default; opt-in).

The Web UI's Capabilities card shows which services are wired so you
can verify the configuration without checking logs.

## Configuring cameras

### Zero-config (recommended — the default)

Leave `auto_discover: true` (default) and **leave `adapters` empty**.
Open the Web UI status page. The **"HA cameras"** card shows one row
per physical device with:

- A live **Enable / Disable** toggle.
- The AI-picked stream + motion sensors (annotated "AI pick" / "override").
- An **Override** disclosure for changing stream / motion / cooldown
  via radio + checkbox + number inputs. No restart needed — changes
  apply within a second.
- A **Reset to AI defaults** button if you want to drop overrides.
- **Re-discover now** to pick up cameras you just added in HA. The
  page also re-discovers automatically every 5 minutes.

The AI picks follow conventions learned from bring-up:

- **Stream**: prefer low-bandwidth substream (`_fluent`, `_sub`) over
  `_main` / `_clear` / `_mainstream`. Skip Reolink `_profile*`
  (ONVIF mainstream auth is broken on common firmware) and duplicate
  Dahua substreams (`_sub_2`, `_sub_3`).
- **Motion sensors**: prefer AI-classified (`_smart_motion_human`,
  `_person_detection`, `_intrusion_area_*`) over noisy generics
  (`_motion_alarm`, `_cell_motion_detection`, `_video_motion_info`).
- **Cooldown**: 10 s.

Overrides are persisted at `/data/sentihome/adapter_overrides.json`
and survive add-on updates.

### Advanced — hand-written adapters

Set `auto_discover: false` (or, for back-compat, leave it `true` but
populate `adapters` non-empty). Two adapter kinds:

#### `ha-camera` — ride on HA's camera integration

```yaml
adapters:
  - name: pool-cam
    kind: ha-camera
    camera_entity: camera.pool_cam
    motion_entities:
      - binary_sensor.pool_cam_smart_motion_human
      - binary_sensor.pool_cam_smart_motion_vehicle
    snapshot_cooldown_seconds: 10
```

SentiHome subscribes to the listed motion / AI sensors, snapshots via
`/api/camera_proxy/` on trigger, surfaces alerts in the Web UI.

#### `rtsp-direct` — cameras HA doesn't manage

```yaml
adapters:
  - name: front-cam
    kind: rtsp-direct
    streams:
      - id: cam_front
        rtsp_url: rtsp://USER:PASS@192.168.1.50:554/stream
```

SentiHome pulls the RTSP stream itself, runs MOG2 motion detection. Use
when the camera isn't in HA or doesn't expose motion events to HA.

## Nested sections

The `bus`, `memory`, `vlm_router`, `notify`, and `adapters` keys accept
the full Topology schema as nested YAML. Example (paste into the add-on
options UI):

```yaml
profile: yellow_plus_inference
vlm_router:
  backends:
    - name: lan-ollama
      kind: ollama
      base_url: http://inference.lan:11434
      model: qwen2.5-vl:7b
      privacy_tier_max: local_only

notify:
  resident_to_push_service:
    resident_1: notify.mobile_app_pixel_8
  media_players:
    - media_player.kitchen

adapters:
  - name: front-cam
    kind: rtsp-direct
    streams:
      - id: cam_front
        rtsp_url: rtsp://user:pass@192.168.1.50/stream
```

See `infrastructure/docker/sentihome.example.yaml` in the repo for a
fully-annotated starter.

## Where data lives

- `/data/options.json` — Supervisor-managed; do not edit by hand
- `/data/sentihome/` — Postgres + Qdrant + Redis volumes and the object store
- `/share/sentihome/` — exported clips, daily digests

## Logs

Supervisor → SentiHome → **Log** tab. Each underlying service logs via
structlog with a `service=<name>` field; filter by service in the log
viewer.

## Troubleshooting

- **Add-on won't start:** check the log; usually a missing required field
  in options (e.g. an adapter declared without a URL). Topology validation
  errors print one human-readable line per problem.
- **HA entities don't appear:** install the SentiHome custom integration
  via HACS or manual copy. The add-on hosts the services; the integration
  exposes them to HA.
- **VLM requests time out:** confirm the backend URL is reachable from
  inside the add-on (`docker exec` into the container, curl the URL).
