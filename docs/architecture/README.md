# Kukii-Home Architecture

Living architecture docs. Each file is a focused section; this index defines reading order and ownership of concerns.

Source brainstorm: [`../../design_notes.md`](../../design_notes.md).
Requirements: [`../requirements/rule-scenarios-and-slas.md`](../requirements/rule-scenarios-and-slas.md) — rule scenario catalog, output taxonomy, SLAs.

## Reading order

### Foundations

1. [Overview & goals](01-overview.md)
2. [High-level architecture](02-high-level-architecture.md)

### Runtime plane

3. [Event bus & queueing](03-event-bus-and-queueing.md)
   3.5. [NVR adapter layer (pluggable frame sources)](03.5-nvr-adapter-layer.md)
4. [Model router & inference hosts](04-model-router-and-inference.md)
5. [Event & message schemas](05-event-schema.md)
6. [Agent orchestration](06-agent-orchestration.md)
7. [Tool layer (MCP)](07-tool-layer-mcp.md)

### Perception & reasoning

8. [Detection pipeline](08-detection-pipeline.md)
9. [VLM prompt assembly contract](09-vlm-prompt-contract.md)
10. [Rule schema & retrieval](10-rule-schema-and-retrieval.md)
    10.5. [Feedback-driven rule optimization & replay testing](10.5-feedback-driven-rule-optimization.md)
11. [Memory model (sessions, episodic, vector + SQL)](11-memory-model.md)
12. [Recognition & identity](12-recognition-and-identity.md)
    12.5. [Dynamic identity refinement & multi-camera fusion](12.5-dynamic-identity-refinement.md)

### Spatial model

13. [Camera, area & zone data model](13-camera-area-zone-model.md)
14. [Calibration (fixed + PTZ + stereo)](14-calibration.md)

### Outputs & ops

15. [Alerting & action policy](15-alerting-and-actions.md)
16. [Privacy & data governance](16-privacy-and-governance.md)
17. [Observability & operations](17-observability.md)
18. [Hardware sizing](18-hardware-sizing.md)
19. [Failure modes & degradation](19-failure-modes.md)

### Backlog

20. [Open questions & decision log](20-open-questions.md)

## Status summary

| Section                                | Status          | Notes                                                                                                                                                 |
| -------------------------------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01-overview                            | **stable**      | Vision, goals, principles, glossary                                                                                                                   |
| 02-high-level-architecture             | **stable**      | System layers, components, data flow                                                                                                                  |
| 03-event-bus-and-queueing              | **stable**      | NATS JetStream, triage worker, load shedding                                                                                                          |
| 03.5-nvr-adapter-layer                 | **drafting**    | Pluggable frame sources (Agent DVR, Frigate, Blue Iris, Synology, QNAP, UniFi, direct RTSP); service / built-in / native modes; v1 ships service mode |
| 04-model-router-and-inference          | **stable**      | Multi-backend routing, local+cloud fallback, circuit breaker                                                                                          |
| 05-event-schema                        | **stable**      | Trigger, enriched, VLM, reasoner messages                                                                                                             |
| 06-agent-orchestration                 | **stable**      | Triage scoring, event routing, deterministic context assembly                                                                                         |
| 07-tool-layer-mcp                      | **stable**      | Home Assistant integration, device actions, state queries                                                                                             |
| 08-detection-pipeline                  | **stable**      | YOLO, face detection, re-ID, pose, quality gates                                                                                                      |
| 09-vlm-prompt-contract                 | **stable**      | Frame selection, context assembly, prompt template                                                                                                    |
| 10-rule-schema-and-retrieval           | **stable**      | Rule authoring, conflict resolution, hybrid retrieval                                                                                                 |
| 10.5-feedback-driven-rule-optimization | **stable**      | Autonomous variant testing, safe rollout phases, learning loops                                                                                       |
| 11-memory-model                        | **stable**      | Five layers, lifecycle, episodic summaries, retention                                                                                                 |
| 12-recognition-and-identity            | **stable**      | Face/body/behavioral biometrics, multi-modal matching, gallery                                                                                        |
| 12.5-dynamic-identity-refinement       | **stable**      | Multi-camera fusion, stereo calibration, temporal accumulation                                                                                        |
| 13-camera-area-zone-model              | **stable**      | Camera records, spatial hierarchy, zones, adjacency graphs                                                                                            |
| 14-calibration                         | **stable**      | Intrinsics, extrinsics, ground plane, stereo, maintenance                                                                                             |
| 15-alerting-and-actions                | **stable**      | Confidence tiers, escalation, device actions, explanation                                                                                             |
| 16-privacy-and-governance              | **drafting**    | Data classes, privacy tiers, right-to-forget, GDPR/CCPA                                                                                               |
| 17-observability                       | **stable**      | Metrics taxonomy, AI synthesis layer, dashboard design, feedback loops                                                                                |
| 18-hardware-sizing                     | **preliminary** | Workload model, topologies, budget tiers, scaling decisions — estimates pending real-world validation                                                 |
| 19-failure-modes                       | **stable**      | 10 failure modes, degradation strategies, safe defaults matrix                                                                                        |
| 20-open-questions                      | **living**      | Open questions, decision log, resolved items                                                                                                          |

---

## Conventions

- Each doc opens with **Purpose** (one line) and **Status** (`skeleton | drafting | preliminary | stable | living`).
  - `preliminary` indicates a doc with engineering estimates that will be revised based on real-world validation.
- Decisions land in the doc they affect, with a one-line entry in [20-open-questions.md](20-open-questions.md) when resolved.
- Diagrams: ASCII inline; richer diagrams as `.svg`/`.png` siblings when needed.
