# 02 — High-Level Architecture

**Purpose:** Component map and request/event flow at the highest level. Anchors every deeper section.
**Status:** drafting

---

## Design philosophy

SentiHome is built around four separation-of-concerns principles:

1. **SentiHome is the vision & rule intelligence layer; HA is the device orchestration layer.** SentiHome owns detection, reasoning, rule creation, and learning. HA owns device state truth, action execution, user experience (dashboards, mobile, automations), and ecosystem integration. They communicate via REST API and MCP: SentiHome queries HA for world context, then calls HA services to execute actions.

2. **Cameras are the primary perception layer.** Camera events drive the reactive pipeline. HA state enriches reasoning (world context) but is not the primary trigger — SentiHome's vision detections are. Rules live in SentiHome and are conversationally created; HA executes actions.

3. **LLMs are called with complete context, not incrementally.** The hot path assembles all context deterministically before a single VLM call. LLM-mediated orchestration is reserved for latency-tolerant deliberative paths. Conversational rule creation uses an LLM, but rule firing/execution is deterministic.

4. **NVR is a pluggable data source, not a core dependency.** SentiHome works with any frame source via the NVR Adapter layer (§03.5): Agent DVR, Frigate, Blue Iris, Synology, QNAP, UniFi Protect, or direct RTSP from cameras (no NVR at all). The v1 default is service mode (universal RTSP compatibility); native and built-in modes are performance optimizations layered on top. Long-term vision: as SentiHome matures (v3–v4), it absorbs NVR responsibilities (motion detection, archival, clip generation) such that direct camera → HA → SentiHome becomes the recommended path and the NVR layer becomes optional.

---

## Component map

```
┌─ Sensing ──────────────────────────────────────────────────────┐
│  Cameras (RTSP/ONVIF)          Sensors (Thread/Zigbee/Z-Wave)  │
│  Microphones / doorbells       Smart locks / garage / gates    │
└──────────┬─────────────────────────────┬───────────────────────┘
           │                             │
┌─ Capture & Ecosystem Hub ─────────────┴───────────────────────┐
│                                                                 │
│   NVR Adapter Layer (§03.5)        Home Assistant              │
│   ─────────────────────            ─────────────────           │
│   Pluggable frame sources:         - Device state truth        │
│   - Agent DVR (service mode)       - HA-native AI add-ons      │
│   - Frigate (built-in mode)        - Thread AI assessments     │
│   - Blue Iris (service mode)       - Other integrations        │
│   - Synology / QNAP (service)      - Calendar, presence        │
│   - UniFi Protect (service)                                    │
│   - Direct RTSP (no NVR)           HA Agent (MCP server)       │
│   - Future: native plugins         READ: poll, cache, query    │
│                                    WRITE: device commands      │
│   Preprocessing layer:                                          │
│   - Motion gating (24/7)                                        │
│   - Frame markup + enrichment                                   │
│   - Mode-adaptive (native > built-in > service)                │
│                                                                 │
└──────────┬──────────────────────────────────────────┬──────────┘
           │ push (on-camera AI / motion / webhooks)  │ poll → synthetic events
           │                                          │
           └──────────────┬───────────────────────────┘
                          ▼
┌─ Event Bus (NATS JetStream) ───────────────────────────────────┐
│  vlm.urgent   vlm.normal   vlm.background                      │
│  sensor.bypass (Tier-1 safety: smoke, CO, flood — no VLM)      │
└──────────┬─────────────────────────────────────────────────────┘
           │
┌─ Orchestration ───────────────────────────────────────────────┐
│                                                                 │
│   Triage worker                 Session / journey manager      │
│   - dedup, score, route         - multi-cam subject tracking   │
│   - TransientIntent boost       - LangGraph state machine      │
│                                 - LLM only at escalation/close │
│   Attention mode manager                                        │
│   - life-safety vigilance       Sequence watch manager         │
│   - bypasses queue              - completion detection         │
│   - specialized models          - adaptive frame sampling      │
│                                                                 │
└──────────┬─────────────────────────────────────────────────────┘
           │
┌─ Context assembly (parallel) ─────────────────────────────────┐
│  Rules retrieval  │  HA world state  │  Identity candidates    │
│  Active contexts  │  Active intents  │  Episodic recall        │
└──────────┬────────────────────────────────────────────────────┘
           │
┌─ Inference ───────────────────────────────────────────────────┐
│                                                                 │
│   ONE VLM CALL (standard)           Two-step fallback          │
│   frames + context + persona        (weaker backends only)     │
│   → structured decision JSON                                    │
│                                                                 │
│   Ollama hosts (LAN)    ←──── Model router ────→  Cloud VLMs   │
│   vLLM / TGI                  (capability,          (fallback +  │
│   Fast detector GPU           privacy, cost,        preferred   │
│                               health, affinity)     tasks)      │
└──────────┬────────────────────────────────────────────────────┘
           │ structured decision JSON
┌─ Rule Engine & Action Dispatch ───────────────────────────────┐
│                                                                 │
│  Rules (live in SentiHome):                                     │
│  ├─ Matched from rule registry based on detection              │
│  ├─ Evaluate conditions (confidence, world state from HA)      │
│  ├─ Determine actions [notify, speak, unlock, light, ...]     │
│  └─ Policy gate (auto-allowed vs. policy-gated vs. blocked)    │
│                                                                 │
│  Action dispatch:                                               │
│  ├─ Call HA services (notify, light, lock, TTS, etc.)         │
│  │  via ha.call_service() MCP or REST API                      │
│  ├─ Deeper assessment if needed                                │
│  │  (re-sample + second VLM call)                              │
│  ├─ Session open/update (via memory MCP)                       │
│  └─ Remediation registry (PTZ, profile switch on limiting     │
│     factors, then escalate to HA)                              │
│                                                                 │
└──────────┬────────────────────────────────────────────────────┘
           │
┌─ MCP tool servers ────────────────────────────────────────────┐
│  ha-agent-mcp    dvr-mcp    detector-mcp    memory-mcp         │
│  notify-mcp                                                     │
└───────────────────────────────────────────────────────────────┘
           │
┌─ State ───────────────────────────────────────────────────────┐
│  Vector DB (rules, galleries, episodic summaries)              │
│  SQL (sessions, events, KnownActors, calibration, audit)       │
│  Object store (clips, frames, montages)                        │
│  Time-series log (raw event stream)                            │
└───────────────────────────────────────────────────────────────┘
           │
┌─ Surfaces ────────────────────────────────────────────────────┐
│  Phone push    Voice (TTS via HA)    In-app    Ambient lights  │
└───────────────────────────────────────────────────────────────┘
```

---

## End-to-end flow — reactive path (camera event)

```
Camera motion/ONVIF event
  → DVR webhook → NATS ingress
  → Triage (dedup, score, route to vlm.normal or vlm.urgent)
  → Fast detector (GPU enrichment — faces, objects, re-ID)
  → Context assembly (parallel: rules, HA state, identity, contexts, episodes)
  → VLM call (frames + context + persona → decision JSON)
  → Action dispatch:
      alert_required? → notify.push
      deeper_assessment? → remediation registry → ha.illuminate_area →
                           re-sample → second VLM call
      journey_open? → memory.open_session
      attention_mode? → attention mode manager activates
  → Memory write (episodic log, visit ledger update)
```

Total LLM calls: **1** (standard) or **2** (with bounded deliberation).

---

## End-to-end flow — HA state change (sensor / ecosystem event)

```
Scheduled poller fires (per cadence group)
  → ha.get_changes(since_ts) [read-side MCP, no LLM]
  → Diff against last snapshot
  → Tier-1 safety events (smoke, CO, flood)?
      → sensor.bypass lane → immediate action dispatch (no VLM)
  → Other state changes?
      → synthetic event → NATS → triage → normal pipeline

OR

  → ha.query("anything needing attention?") [LLM-backed, for semantic sweep]
      → HA-native AI alerts (Thread network, other add-ons) surfaced
      → synthetic events for anything significant
```

---

## End-to-end flow — journey / session path

```
First segment: camera event → VLM outputs journey_open: true
  → session manager opens session in SQL

Subsequent segments (same subject, different cameras):
  → re-ID + spatial plausibility check → append to session
  → incremental journey_score update
  → check session-scoped rules → escalate if threshold crossed

Session close (silence timeout or known egress):
  → journey-close VLM call (stitched montage + session context)
  → episodic memory write (summary + embedding)
  → visit ledger update
  → rule proposals if pattern flagged
```

---

## Trust & data boundaries

```
Local network boundary (LAN):
  All camera frames, resident face data, interior footage
  HA device states, presence information
  Rule evaluation, memory reads/writes

Cloud boundary (conditional — privacy-gated):
  Detector-derived scene JSON (no raw resident faces)
  Non-interior event analysis when local is saturated
  Journey summaries (stripped of raw embeddings)
  Never: raw clips of residents, interior frames, resident biometrics

HA agent write-side boundary:
  Auto-allowed: lights, scenes, non-security switches
  Policy-gated: locks, alarms, sirens
  Hard-blocked without explicit human confirmation: disarm, siren
```

---

## Key design decisions (summary)

| Decision | Rationale |
|----------|-----------|
| Rules live in SentiHome, not HA | Conversational rule creation; rules fire based on SentiHome detections, not static automations |
| HA is device orchestration layer | HA provides world state (query), executes actions (services), owns UX (dashboards, mobile) |
| SentiHome queries HA for context, calls HA services | Clean REST API / MCP boundary; SentiHome is agnostic to device types |
| NVR is pluggable data source via adapter layer (§03.5) | Universal compatibility (any NVR or none); v1 ships service mode; native modes added over time |
| Service mode is v1 baseline | Works with any RTSP/ONVIF source day one; native plugins are optimization, not requirement |
| Long-term NVR-optional vision | As SentiHome matures, it absorbs NVR responsibilities; direct camera → HA → SentiHome becomes recommended |
| Single VLM call on hot path | Eliminates orchestration overhead; capable VLMs reason directly |
| VLM has no HA knowledge | Clean separation; VLM reports observations, action dispatcher interprets + executes |
| Camera events push; HA state poll | Cameras are latency-critical; HA state is eventually consistent |
| Five memory layers | Different lifetimes and access patterns require different stores |
| Policy gate at action dispatcher | Single enforcement point; no policy logic scattered in pipeline |
