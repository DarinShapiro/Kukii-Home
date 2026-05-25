# 07 — Tool Layer (MCP)

**Purpose:** What capabilities are exposed as MCP tools, where the MCP boundary sits, and what stays internal to the orchestrator. The HA agent is the most significant MCP server — it is bidirectional, serving both read-side synthesis and write-side device execution.
**Status:** drafting

---

## What's a tool vs. what's pipeline plumbing

**MCP tools** are called by pipeline components or models at runtime — things that require reaching outside the process boundary (HA, DVR, detector service, memory stores, notification delivery).

**Pipeline plumbing** stays internal — context assembly, frame sampling, triage scoring, queue routing, session state management. These are deterministic code paths, not tool calls. Putting them behind MCP would add unnecessary latency and indirection.

Rule of thumb: if it's a network call to another service, it's a tool. If it's in-process logic, it's plumbing.

---

## HA agent — bidirectional MCP server (device orchestration)

The HA agent is the single integration point for all of Home Assistant. It is **not** the automation engine — SentiHome is. Rather, HA provides:

1. **Device state truth** (queried by SentiHome for world context)
2. **Action execution** (SentiHome dispatches actions to HA services)
3. **User experience** (dashboards, mobile app, optional HA automations)
4. **Ecosystem integration** (all HA-native integrations available to SentiHome via query)

The HA agent exposes two sides with different performance characteristics.

### HA as a universal integration gateway

HA is a living platform with access to two layers of value:

**Layer 1 — HA-native intelligence.** Other AI systems run inside HA and write their outputs as HA native constructs: persistent notifications, entity states, repairs, scripts. Example: a Thread network AI assessment system analyzes mesh topology and device health over time series data and writes HA native alerts and repairs. The HA agent surfaces these findings as world state updates — device degraded, repair attempted, node re-paired — without SentiHome needing any knowledge of Thread networking.

**Layer 2 — Third-party service integrations.** HA connects to a vast ecosystem of external services. SentiHome gains access to all of them through the single HA agent interface, with no separate integrations required:

| Service category     | Examples                                                 | SentiHome use                                                              |
| -------------------- | -------------------------------------------------------- | -------------------------------------------------------------------------- |
| Calendars            | Google Calendar, Apple, Outlook, CalDAV                  | Proactive context priming, guest expectations, scheduled preparations      |
| Weather              | OpenWeatherMap, Met.no, AccuWeather                      | Camera quality expectations, outdoor activity context, pre-actions         |
| Messaging            | SMS, WhatsApp, Telegram                                  | Guest confirmations, delivery notifications, alert delivery channel        |
| Email                | Gmail, IMAP                                              | Delivery tracking, service appointment confirmations                       |
| Delivery tracking    | UPS, FedEx, USPS, Amazon                                 | Expected package arrival, known delivery driver                            |
| Energy monitoring    | Utility APIs, Tibber, Octopus                            | Schedule device actions in off-peak windows                                |
| Music / media        | Spotify, Sonos                                           | Presence signal, party mode detection                                      |
| Occupancy / presence | Phone GPS, BLE beacons                                   | Who's home, ETA, away mode                                                 |
| Smart appliances     | Pool, HVAC, irrigation                                   | Proactive preparation based on calendar/weather                            |
| Wearables            | Apple Watch, Fitbit, Garmin, Oura (via HA Companion App) | Sleep state, heart rate, presence, activity — biometric triggers for rules |
| Voice assistants     | Alexa, Google Home                                       | Additional alert surfaces                                                  |

**The principle:** SentiHome does not need its own calendar integration, weather API, or SMS gateway. HA already has them. The HA agent exposes them all through `ha.query()` and structured tools. Adding a new HA integration automatically makes it available to SentiHome's reasoning pipeline.

**Concrete example — proactive pool preparation:**

```
Proactive planning agent asks:
  ha.query("what's happening in the next 48 hours I should prepare for?")

HA agent synthesizes across:
  Calendar  → "BBQ party Saturday 4pm, ~12 guests"
  Weather   → "Saturday 85°F, sunny — good pool day"
  Pool temp → currently 72°F (via HA pool sensor)
  Pool heater → ~48h to reach 82°F
  Energy    → off-peak window tonight 9pm–6am

Reasoner output:
  → ha.call_service(climate, set_temperature, pool_heater, {temp: 82})
     scheduled for tonight 9pm
  → SituationalContext: "BBQ party Saturday 4pm — 12 guests expected,
     unknown faces at front/backyard are guests"
  → TransientIntent: "notify if pool not at 80°F by Saturday noon"
```

No SentiHome-specific calendar, weather, or energy integration needed.

### READ SIDE — LLM-backed synthesis

Handles polling, state retrieval, and semantic synthesis over HA's full ecosystem. Latency of seconds is acceptable here.

```
ha.get_snapshot()
  Returns: full current entity state snapshot (from internal cache)
  Used by: context assembly (world state for VLM prompt)
  LLM: no

ha.get_changes(since_ts)
  Returns: entities that changed since timestamp + significance flag
  Used by: scheduled poller → synthetic event emission
  LLM: no (diff is deterministic)

ha.get_area_resources(area_id)
  Returns: observational resources for an area
    { ptz_available, supplemental_lighting, camera_profiles,
      adjacent_cameras, sensor_coverage }
  Used by: remediation registry (§06) to resolve limiting factors
  LLM: no

ha.get_calendar_events(start, end)
  Returns: calendar events in window from any connected calendar (Google,
           Apple, Outlook, CalDAV, etc.)
  Used by: SituationalContext priming, proactive planning agent
  LLM: no

ha.list_capabilities()
  Returns: which service categories are currently connected in HA
    { calendar: true, weather: true, email: false, sms: true,
      energy_monitor: true, delivery_tracking: true, ... }
  Used by: proactive planning agent (knows what to ask about)
           pipeline components (know what world state is available)
  LLM: no

ha.query(natural_language)
  Returns: synthesized answer over HA state, native constructs,
           and all connected third-party services
  LLM: yes — small fast chat model, not VLM
       reads: persistent notifications, events, integration health,
              repairs, entity states, calendar, weather, energy,
              delivery status, presence — whatever is connected

  Reactive sweep examples (60s cadence):
    "Anything requiring SentiHome attention since last check?"
    "What HA-native alerts or repairs are currently active?"
    "Is the Thread network reporting any degraded devices?"

  Proactive planning examples (nightly / 6am):
    "What's happening in the next 48 hours I should prepare for?"
    "Any guests, deliveries, or service appointments expected today?"
    "What's the weather doing that affects outdoor plans this weekend?"
    "Are there any energy rate windows worth scheduling around tonight?"

  On-demand user query examples:
    "Summarise the current security state of the home"
    "Is anyone expected to visit today?"
    "What's the weather like for the next 3 hours?"
```

`ha.query()` is the discovery mechanism in practice — the LLM knows what HA has connected and routes questions to the right data sources automatically. `ha.list_capabilities()` is the structured companion for pipeline components that need to know what's available before constructing a query.

### WRITE SIDE — action execution (MCP + REST), no LLM

Executes device commands on behalf of SentiHome rules. Called directly by the action dispatcher (§15), session manager, and attention mode manager. No LLM, no queue, sub-second execution.

**Core device actions:**

```
ha.illuminate_area(area_id, brightness?, color_temp?)
  Resolves area → lighting entity group internally
  Returns: { success, entities_activated, latency_ms }

ha.darken_area(area_id)
  Inverse of illuminate_area

ha.set_scene(scene_id)
  Activates a named HA scene

ha.lock(entity_id)
  Returns: { success, confirmed_state }

ha.unlock(entity_id)
  POLICY-GATED — see Autonomous action policy below

ha.call_service(domain, service, entity_id, data?)
  General-purpose HA service call
  Examples: sonos.play_media, notify.send, lock.unlock, light.turn_on
  Policy table checked before execution

ha.get_entity_state(entity_id)
  Direct single-entity state fetch (for confirmation after action)
```

**Important:** `ha.trigger_automation()` is NOT how SentiHome rules execute. SentiHome rules (defined conversationally in SentiHome) are evaluated and executed by the SentiHome action dispatcher. HA automations are optional user-defined extensions (e.g., "if X happens in SentiHome, then do Y in HA"), but they are not the primary rule mechanism.

**Semantic resolution happens inside the agent.** `illuminate_area("perimeter")` resolves to the correct HA entity group without the caller needing entity IDs. The area/zone model (§13) defines which entities belong to which area; the HA agent holds that mapping.

### Autonomous action policy

The write side enforces the policy defined in §15. Certain commands are auto-allowed; others require pre-approval or an `ask` confirmation loop.

```
Auto-allowed (fire immediately):
  illuminate_area, darken_area, set_scene
  call_service(light.*, switch.* — non-security)

Policy-gated (require pre-approval or ask):
  unlock, call_service(lock.*)
  trigger_automation(security_*)
  call_service(alarm_control_panel.*)

Always denied without explicit human confirmation:
  siren activation
  disarm alarm
```

Policy violations return a structured MCP error:

```json
{
  "error": "policy_gate",
  "action": "unlock",
  "reason": "unlock requires pre-approval or user confirmation",
  "suggest": "ask"
}
```

The action dispatcher routes `policy_gate` errors to an `ask` output — the pipeline asks the user and waits for a response before retrying.

---

## Other MCP servers

### `nvr.*` — NVR Adapter (pluggable, see §03.5)

The NVR adapter is an MCP server that abstracts away the underlying frame source. The same tool calls work whether the user has Agent DVR, Frigate, Blue Iris, Synology, QNAP, UniFi Protect, or just raw RTSP cameras (no NVR). One adapter implementation per platform; SentiHome calls the unified contract.

```
nvr.list_cameras()
  Returns: [ { camera_id, name, capabilities, preprocessing_mode, stream_profiles } ]
  Used by: bootstrap, configuration UI

nvr.get_frame_window(camera_id, ts_start, ts_end, with_metadata?)
  Returns: { frames: [...], metadata: { motion_regions, detections, embeddings,
                                         quality_score, preprocessing_mode,
                                         preprocessing_latency_ms } }
  Used by: context assembly, deeper_assessment re-sampling
  Notes: metadata content varies by mode (native > built-in > service)
         service mode runs preprocessing on-the-fly; built-in returns pre-computed

nvr.subscribe_motion_events(camera_id, callback)
  Returns: subscription_id
  Used by: triage worker (push-driven event ingestion)
  Notes: source varies — NVR-native motion, on-camera AI, or preprocessor service

nvr.enrich_frame(camera_id, frame_uri, models?)
  Returns: { detections, embeddings, quality_score }
  Used by: on-demand enrichment outside the standard frame window flow

nvr.get_stream_url(camera_id, profile?)
  Returns: RTSP URL for live frame sampling (attention modes)

nvr.slew_ptz(camera_id, preset_id)
  Returns: { success, settled_at }
  Used by: remediation registry (subject_too_small limiting factor)
  Notes: not all adapters support PTZ; capability advertised via list_cameras()

nvr.switch_profile(camera_id, profile)
  Returns: { success }
  Used by: remediation registry (low_resolution limiting factor)
```

**Implementations (v1 priority):**

- `adapter-rtsp-direct` — no NVR, direct from cameras (SentiHome internal preprocessing)
- `adapter-agent-dvr-service` — Agent DVR via OpenAPI 2.0
- `adapter-frigate-builtin` — Frigate via MQTT + REST
- `adapter-blueiris-service` — Blue Iris via HA integration + RTSP
- Additional adapters as v1.x: Synology, QNAP, UniFi Protect

Legacy note: The pre-§03.5 design referenced `dvr.*` tools coupled to Agent DVR. Those have been generalized to `nvr.*` under the adapter contract. The Agent DVR-specific adapter still exposes the same capabilities, but other adapters now plug in alongside it.

### `detector.*` — Fast detector service

```
detector.enrich(frame_uris, options?)
  Returns: structured enrichment JSON (objects, faces, reid, pose, attributes)
  Used by: hot path enrichment stage

detector.identify_face(crop_uri)
  Returns: { identity_candidates, confidence }

detector.reid(crop_uri, session_id)
  Returns: { reid_embedding, session_match_candidates }

detector.ocr_plate(crop_uri)
  Returns: { plate_text, confidence }
```

### `memory.*` — Memory stores

```
memory.retrieve_rules(event_context)
  Returns: top-K rules (hybrid SQL+ANN retrieval)

memory.get_active_contexts(area_id)
  Returns: active SituationalContexts for this area

memory.get_active_intents(area_id?)
  Returns: active TransientIntents (filtered to area if provided)

memory.resolve_identity(detections)
  Returns: top-N identity candidates with confidence + access profiles

memory.recall_episodic(event_context, top_k)
  Returns: similar past sessions, summarised

memory.write_episodic(session_id, record)
  Writes a closed session to episodic memory

memory.update_visit_ledger(subject_ref, area, visit)
  Updates the VisitLedger for a subject

memory.open_session(subject_descriptor)
  Returns: session_id

memory.append_segment(session_id, segment)
  Appends a new camera segment to an open session

memory.close_session(session_id)
  Triggers episodic write + journey-close VLM call
```

### `notify.*` — Notification delivery

```
notify.push(targets, message, evidence_ref?, priority?)
  Returns: { delivered_to, failed }

notify.speak(message, zone?)
  Triggers TTS on home speakers via HA

notify.ask(question, evidence_ref, response_callback_id)
  Surfaces a conversational confirmation; registers callback for response
  Returns: { ask_id }  ← pipeline suspends; resumes on response
```

---

## MCP server topology

One MCP server per logical domain, each a separate process:

```
ha-agent-mcp        ← HA read + write (bidirectional)
nvr-adapter-mcp     ← NVR adapter (pluggable per platform — see §03.5)
preprocessor-mcp    ← Shared preprocessing service (used by service-mode adapters)
memory-mcp          ← All memory stores (rules, sessions, episodic, identity)
notify-mcp          ← Notification delivery
```

The NVR adapter and preprocessor are typically deployed as a pair for service-mode platforms. For built-in mode (Frigate) the preprocessor is bypassed — the adapter consumes pre-enriched data directly. For native mode (future Agent DVR plugin) both adapter and preprocessor logic run in-process within the NVR.

The action dispatcher and other pipeline components are MCP clients. They call whichever server owns the capability they need.

Servers are independently deployable and restartable. The pipeline handles server unavailability per the failure modes in §19.

---

## AuthZ / scope

Each MCP server validates a bearer token on every call. Scopes are defined per tool:

| Tool namespace                                         | Scope required              | Notes                                                  |
| ------------------------------------------------------ | --------------------------- | ------------------------------------------------------ |
| `ha.get_*`                                             | `ha:read`                   | Any pipeline component                                 |
| `ha.query`                                             | `ha:read`                   | Any pipeline component                                 |
| `ha.illuminate_*`, `ha.set_scene`                      | `ha:write:light`            | Action dispatcher, attention mode manager              |
| `ha.lock`, `ha.unlock`                                 | `ha:write:lock`             | Action dispatcher only; unlock requires elevated scope |
| `ha.call_service`                                      | `ha:write:service:{domain}` | Scoped per HA domain                                   |
| `nvr.*` (read)                                         | `nvr:read`                  | Any pipeline component on hot path                     |
| `nvr.slew_ptz`, `nvr.switch_profile`                   | `nvr:write`                 | Action dispatcher, remediation registry                |
| `detector.*`                                           | `detector:infer`            | Any pipeline component on hot path                     |
| `memory.write_*`, `memory.update_*`                    | `memory:write`              | Action dispatcher, session manager                     |
| `memory.retrieve_*`, `memory.get_*`, `memory.recall_*` | `memory:read`               | Any pipeline component                                 |
| `notify.*`                                             | `notify:send`               | Action dispatcher only                                 |

---

## Latency budgets

| Tool                                   | Expected p50       | Hard timeout | On timeout                               |
| -------------------------------------- | ------------------ | ------------ | ---------------------------------------- |
| `ha.get_snapshot`                      | < 10ms (cache hit) | 200ms        | Proceed with stale snapshot, flag        |
| `ha.get_changes`                       | < 50ms             | 500ms        | Skip this poll cycle                     |
| `ha.query`                             | 1–3s (LLM)         | 8s           | Return partial + flag                    |
| `ha.illuminate_area`                   | < 200ms            | 1s           | Log failure, proceed without remediation |
| `ha.call_service`                      | < 300ms            | 2s           | Retry once, then log                     |
| `nvr.get_frame_window` (native mode)   | < 100ms            | 1s           | Fall back to service mode if available   |
| `nvr.get_frame_window` (built-in mode) | < 200ms            | 2s           | Skip metadata, return frames only        |
| `nvr.get_frame_window` (service mode)  | < 500ms            | 5s           | Skip enrichment, use raw frames          |
| `nvr.slew_ptz`                         | < 2s (mechanical)  | 5s           | Skip deeper assessment                   |
| `detector.enrich`                      | < 500ms (GPU)      | 1s           | Proceed without enrichment               |
| `memory.retrieve_rules`                | < 100ms            | 300ms        | Proceed with empty rule set, flag        |
| `notify.push`                          | < 1s               | 5s           | Retry via alternate channel              |

---

## Schema & versioning

- All tool inputs and outputs are typed JSON schemas, versioned alongside the server
- Breaking schema changes increment the major version; servers advertise supported versions
- Clients declare which version they call; the server rejects incompatible calls with a structured error
- Replay tooling (§06) records the exact tool call inputs/outputs for every hot-path run
