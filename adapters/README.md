# adapters/

Pluggable NVR adapters. Each adapter translates between a specific NVR platform (or no NVR at all) and the unified `nvr.*` MCP contract that SentiHome's core consumes. Architecture: [§03.5 NVR Adapter Layer](../docs/architecture/03.5-nvr-adapter-layer.md).

## Adapters (v1 priority order)

| Adapter | Mode | Status | Notes |
|---------|------|--------|-------|
| [`nvr-rtsp-direct/`](nvr-rtsp-direct/) | Direct (internal native) | v1 target | No NVR; consumes RTSP directly from cameras. The long-term recommended path. |
| [`nvr-agent-dvr/`](nvr-agent-dvr/) | Service | v1 target | Agent DVR via OpenAPI 2.0; the original design choice |
| [`nvr-frigate/`](nvr-frigate/) | Built-in | v1 target | Best fit for HA ecosystem; consumes Frigate's MQTT + REST |
| [`nvr-blueiris/`](nvr-blueiris/) | Service | v1 target | Biggest external user base; uses ha-blueiris for events + RTSP |
| [`nvr-synology/`](nvr-synology/) | Service | v1.x | Surveillance Station Web API v3.11 + webhooks |
| [`nvr-qnap/`](nvr-qnap/) | Service | v1.x | QVR Pro OpenAPI with frame-level access |
| [`nvr-unifi/`](nvr-unifi/) | Service | v1.x | Official UniFi Protect API |

## Adapter contract

All adapters implement the same MCP interface. SentiHome's core is mode-agnostic; the adapter handles the platform-specific translation.

See [`shared/protos/nvr-adapter.proto`](../shared/protos/) (TODO) for the formal contract. Key operations:

- `nvr.list_cameras()` — enumerate cameras + capabilities + active mode
- `nvr.get_frame_window(camera_id, ts_start, ts_end, with_metadata)` — returns frames + preprocessing metadata
- `nvr.subscribe_motion_events(camera_id, callback)` — push notifications when motion / on-camera AI fires
- `nvr.enrich_frame(...)` — on-demand enrichment
- `nvr.get_stream_url(...)` — direct RTSP for attention modes
- `nvr.slew_ptz(...)`, `nvr.switch_profile(...)` — observation actions where supported

## Operating modes

- **Service mode** (v1 default): adapter consumes RTSP from NVR, runs preprocessing in a companion service. Universal compatibility. Higher resource overhead (~1.8–2.2×).
- **Built-in mode**: NVR provides preprocessing (Frigate). Adapter consumes pre-enriched results. ~1.1–1.3× overhead.
- **Native mode** (future): plugin runs in-process inside the NVR. Direct frame buffer access. ~1.0× overhead. Currently planned for Agent DVR (v2+).
- **Direct mode**: no NVR; SentiHome's internal preprocessing is effectively native by definition.

## Adding a new adapter

1. Create the adapter folder with a README describing platform-specific behavior
2. Implement the `nvr.*` MCP contract using platform-specific APIs
3. Document supported features in a capability matrix
4. Add integration tests against a mock or real instance of the platform
5. Register in `infrastructure/docker/docker-compose.yml` as an optional service
6. Update this README's adapter table
