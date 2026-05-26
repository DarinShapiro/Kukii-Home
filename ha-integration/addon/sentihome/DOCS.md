# SentiHome add-on configuration

The Supervisor add-on UI shows a top-level form mapped to the SentiHome
topology config schema. Field-level reference:

## Top-level options

| Option         | Description                                                                                | Default             |
| -------------- | ------------------------------------------------------------------------------------------ | ------------------- |
| `profile`      | Deployment shape: `yellow_single_box`, `yellow_plus_inference`, or `distributed`           | `yellow_single_box` |
| `household_id` | Stable identifier used in logs + metrics                                                   | `my_home`           |
| `timezone`     | IANA tz name, e.g. `America/New_York`                                                      | `UTC`               |
| `ha_token`     | Long-lived access token. Leave empty — Supervisor injects `SUPERVISOR_TOKEN` automatically | _empty_             |
| `log_level`    | `DEBUG` / `INFO` / `WARNING` / `ERROR`                                                     | `INFO`              |

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
