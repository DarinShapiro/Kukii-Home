# Epic 03: NVR Adapter Layer

**Architecture refs:** §03.5
**Components:** adapters/*, services/preprocessor, shared/protos
**Priority:** P0
**Blocked by:** Epic 01, 02
**Blocks:** Epic 04

## Description

The pluggable NVR adapter layer that makes SentiHome work with any frame source — or none at all. V1 ships service-mode adapters for the major platforms plus direct RTSP for users without an NVR. Native modes are explicitly out of v1 scope.

## Definition of done

- `nvr.*` MCP contract defined and shared
- Direct RTSP adapter works against any ONVIF camera
- Agent DVR service-mode adapter ships
- Frigate built-in adapter ships (consumes MQTT + REST)
- Blue Iris service-mode adapter ships
- Auto-detection bootstrap picks the right adapter per camera
- Mode + per-camera latency surfaces in observability

## Issues

1. **feat(shared): define `nvr.*` MCP contract** — `list_cameras`, `get_frame_window`, `subscribe_motion_events`, `enrich_frame`, `get_stream_url`, `slew_ptz`, `switch_profile`. (labels: `epic:nvr-adapters`, `component:shared`, `priority:p0`)
2. **feat(shared): NVR capability matrix schema** — which adapters support which features. (labels: `epic:nvr-adapters`, `component:shared`, `priority:p0`)
3. **feat(adapters): adapter base class / shared scaffolding** — common error handling, retry logic, MCP boilerplate. (labels: `epic:nvr-adapters`, `component:adapters`, `priority:p0`)
4. **feat(adapter-rtsp-direct): RTSP ingest + ONVIF event subscription** — directly from cameras, no NVR. (labels: `epic:nvr-adapters`, `component:adapter-rtsp-direct`, `priority:p0`)
5. **feat(adapter-rtsp-direct): internal frame buffering for window queries** — 5-min rolling buffer per camera. (labels: `epic:nvr-adapters`, `component:adapter-rtsp-direct`, `priority:p0`)
6. **feat(adapter-agent-dvr): OpenAPI 2.0 client** — generated client for Agent DVR API. (labels: `epic:nvr-adapters`, `component:adapter-agent-dvr`, `priority:p0`)
7. **feat(adapter-agent-dvr): webhook receiver for events** — receives Agent DVR motion/AI events, normalizes to MCP. (labels: `epic:nvr-adapters`, `component:adapter-agent-dvr`, `priority:p0`)
8. **feat(adapter-agent-dvr): frame window retrieval via clips API** — translates `get_frame_window` to Agent DVR clip queries. (labels: `epic:nvr-adapters`, `component:adapter-agent-dvr`, `priority:p0`)
9. **feat(adapter-frigate): MQTT subscriber for events** — subscribes to Frigate's MQTT topics. (labels: `epic:nvr-adapters`, `component:adapter-frigate`, `priority:p0`)
10. **feat(adapter-frigate): REST API client for clips + snapshots** — leverages Frigate's pre-enriched data. (labels: `epic:nvr-adapters`, `component:adapter-frigate`, `priority:p0`)
11. **feat(adapter-frigate): map Frigate YOLO detections to SentiHome enrichment schema** — translate Frigate's metadata format. (labels: `epic:nvr-adapters`, `component:adapter-frigate`, `priority:p0`)
12. **feat(adapter-blueiris): event integration via ha-blueiris** — consume HA-side Blue Iris events. (labels: `epic:nvr-adapters`, `component:adapter-blueiris`, `priority:p1`)
13. **feat(adapter-blueiris): direct RTSP frame access from Blue Iris** — uses Blue Iris's RTSP server. (labels: `epic:nvr-adapters`, `component:adapter-blueiris`, `priority:p1`)
14. **feat(adapter-synology): Surveillance Station Web API client** — auth, webhook subscription, snapshot/clip retrieval. (labels: `epic:nvr-adapters`, `component:adapter-synology`, `priority:p2`)
15. **feat(adapter-qnap): QVR Pro OpenAPI client** — leverage frame-level API. (labels: `epic:nvr-adapters`, `component:adapter-qnap`, `priority:p2`)
16. **feat(adapter-unifi): UniFi Protect official API client** — auth, events, RTSP. (labels: `epic:nvr-adapters`, `component:adapter-unifi`, `priority:p2`)
17. **feat(core): adapter auto-detection at bootstrap** — discover which adapters are configured, register them, select per camera. (labels: `epic:nvr-adapters`, `component:core`, `priority:p1`)
18. **feat(core): mode + latency observability per camera** — surface in dashboard which mode each camera uses and measured latency. (labels: `epic:nvr-adapters`, `component:core`, `priority:p1`)
19. **test: adapter integration tests with mock NVRs** — fake NVR services for each adapter type. (labels: `epic:nvr-adapters`, `component:adapters`, `priority:p1`)
