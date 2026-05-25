# 06 — Agent Orchestration

**Purpose:** How per-event work is composed across triage, enrichment, reasoning, and action — and what framework runs it.
**Status:** drafting

---

## Core principle

**LLMs are called with complete pre-assembled context, not incrementally through a tool-use loop — on the hot path.**

An agentic loop where an LLM decides which tool to call next adds a full LLM roundtrip per step. Four tools = five LLM calls = 5–15s of pure orchestration overhead on a local model before any real work happens. The hot path avoids this entirely: context is assembled deterministically and in parallel before the single LLM call.

---

## Hot path: deterministic pipeline, one LLM call

```
Event arrives on queue
         │
         ▼  deterministic
Triage worker
  - dedup, score, route to priority tier
  - check active TransientIntents (triage boost if match)
         │
         ▼  GPU inference, not LLM
Fast detector
  - object detection, face recognition, re-ID,
    pose, attributes, plate OCR, pet recognition
  - returns structured enrichment JSON
         │
         ▼  deterministic, all branches in parallel
Context assembly
  ├── hybrid rule retrieval (SQL filter + ANN rank)
  ├── HA world state snapshot
  ├── active SituationalContexts + TransientIntents
  ├── subject identity candidates + access profiles
  └── relevant episodic summaries (top 2–3, summarized)
         │
         ▼  ONE LLM call
VLM (with visual reasoning)
  - annotated frames + enrichment + full context + persona
  - returns structured decision JSON (see §09)
         │
         ▼  deterministic
Action dispatch
  - reads action, criticality, rules_fired
  - notify / memory write / speak / ask
  - opens session if journey_open: true
  - triggers attention mode if attention_mode set
  - opens sequence watch if sequence_watch set
  - if deeper_assessment: consult remediation registry
      → execute environmental action via HA agent (lights, PTZ, profile)
      → wait re_assess_delay
      → re-sample frames → second VLM call (hard cap)
  - HA device actions resolved here via HA agent; VLM never sees entity names
```

Standard case: **one LLM call** on the hot path. Everything else is deterministic plumbing running in parallel where possible.

### Context assembly is the latency-critical parallel block

```python
rules, world_state, contexts, identity, episodes = await asyncio.gather(
    retrieve_rules(event),
    get_ha_world_state(),
    get_active_contexts(event.area),
    resolve_identity(event.detections),
    recall_episodic(event)
)
```

This block should complete in 100–300ms. It is the last deterministic step before the LLM call.

---

## Bounded deliberation: the one escape hatch

When the VLM returns `deeper_assessment: true`, the action dispatcher consults the **remediation registry** — a deterministic lookup table that maps `confidence_limiting_factors` + available area resources to a remediation action. The VLM names the problem; the dispatcher decides what to do about it.

### Remediation registry

```
limiting_factor          + area_resource              → remediation
─────────────────────────────────────────────────────────────────
low_light                + area has lighting           → illuminate_area, re_assess: 3s
low_light                + no lighting available       → escalate to cloud model
low_light                + night_vision cam available  → switch cam profile, re_assess: 1s
subject_too_small        + PTZ available               → slew PTZ to subject, re_assess: 2s
subject_too_small        + no PTZ                      → escalate to cloud model
subject_partially_occluded + PTZ available             → slew PTZ to alternate angle, re_assess: 2s
low_resolution           + main stream available       → switch to high-res profile, re_assess: 1s
camera_obstructed        + (any)                       → alert ops + surface as `ask`
adverse_weather          + (any)                       → escalate to cloud; note in prompt
multiple_subjects        + (any)                       → request additional crops, re_assess: 0s
ambiguous_rule           + (any)                       → one targeted rule retrieval, re_assess: 0s
needs_human_judgment     + (any)                       → surface as `ask` with evidence clip
```

Area resources come from the area/zone model (§13) — not from HA directly. The dispatcher knows what each area can do observationally; the HA agent handles the execution.

### Environmental action feedback loop

When a remediation involves a physical action (lights on, PTZ slew, profile switch), the pipeline waits for the environment to settle before re-sampling:

```
Remediation action fires (e.g. illuminate_area via HA agent)
         │
         ▼  wait re_assess_delay (2–4s for lights; 1–2s for PTZ)
Re-sample frames from same camera(s)
         │
         ▼
New VLM call with:
  - fresh frames (improved conditions)
  - reassessment note: "low_light addressed — area illuminated;
    subject_too_small addressed — PTZ crop provided"
  - same context as original call
```

The VLM receives better inputs and a note explaining what changed. It does not need to know that lights were turned on — only that illumination improved.

### What the VLM never knows

- Which HA entities were activated
- That lights, PTZ, or profile switches occurred
- Any device names, entity IDs, or HA service calls

The VLM only sees: better frames + a note about what observational conditions changed.

**Hard cap:** two VLM calls maximum on the hot path. If the second pass also returns `deeper_assessment: true`, take the conservative action and exit. No further loops.

---

## Two-step fallback for weaker backends

The single-call approach requires a VLM capable of visual reasoning over a complex prompt. Weaker or smaller local vision models may only reliably describe — not reason.

Backend capability flag `supports_visual_reasoning` (see §04) drives the call pattern:

```
supports_visual_reasoning: true
  → single call: frames + full context + persona → decision JSON

supports_visual_reasoning: false
  → call 1: frames + minimal context → scene description text
  → call 2: scene text + full context + rules → decision JSON (text LLM)
```

The two-step is the **fallback for weaker backends**, not the primary design. The intermediate scene description JSON is still useful for caching and reuse in this mode but is not a required pipeline stage on the primary path.

---

## Framework by component

| Component | Framework | Rationale |
|-----------|-----------|-----------|
| Hot path pipeline | Plain async Python | Zero framework overhead; LLMs are async function calls; full control over parallelism and error handling |
| Session/journey manager | LangGraph | Explicit state machine with defined transitions; long-running stateful graph; LLM called only at specific state nodes |
| Attention mode loop | Plain async Python | Tight sampling loop; no framework needed |
| Sequence completion watch | Plain async Python | Short-lived; deterministic phase transitions |
| Proactive planning agent | LangGraph or plain async | Latency tolerant; multi-step reasoning over HA ecosystem; tool use natural |
| Journey close / summary | LangGraph or plain async | Latency tolerant; tool use acceptable here |
| Report generation | LangGraph or plain async | Multi-step synthesis; latency tolerant |
| Rule authoring / NL query | LangGraph | Interactive; tool use natural; latency tolerant |

**CrewAI:** appropriate for background analytical tasks where LLM-mediated role collaboration adds value. Wrong choice for any latency-sensitive path — its LLM-as-manager pattern adds 2–4 roundtrips before work starts.

---

## Session manager: explicit state machine

The session manager is long-lived and tempting to make agentic. It is a state machine with explicit transitions — the graph topology drives routing, not an LLM.

```
States:
  open
  segment_appended    → update journey_score, check session-scoped rules
  attention_elevated  → session score crossed threshold, monitoring intensified
  escalated           → alert fired at session level
  closing             → silence timeout or known egress detected
  closed              → episodic write triggered

LLM called only at:
  - escalation decision when score crosses threshold
    (one VLM call with full session context + stitched montage)
  - session close
    (one VLM call for summary → episodic memory write)

All state transitions: deterministic based on incoming segment
data and journey_score thresholds
```

---

## Proactive planning agent

A scheduled agent that reasons *forward* rather than reacting to events. It queries the HA agent's full ecosystem view to anticipate what's coming, prepare the environment, and pre-arm context objects before they're needed.

### Why it exists

The reactive pipeline handles what's happening now. The proactive agent handles what's about to happen. Without it, SituationalContexts are created after the first alert fires (too late), device preparation happens manually (pool heater example), and the system is always catching up instead of anticipating.

### Schedule

| Run | Trigger | Purpose |
|-----|---------|---------|
| Nightly sweep | 11pm or configurable | 48h lookahead — calendar, weather, energy windows |
| Morning briefing | First presence detected + 6am | Today's summary — expected visitors, deliveries, conditions |
| On-demand | User request | "What should I know about this weekend?" |

### What it queries

```python
capabilities = ha.list_capabilities()

questions = [
    "What calendar events or gatherings are expected in the next 48 hours?",
    "Any guests, service workers, or deliveries expected?",
    "What's the weather doing that affects outdoor or pool plans?",
]

if capabilities.energy_monitor:
    questions.append("Any off-peak energy windows worth scheduling around?")

if capabilities.delivery_tracking:
    questions.append("Any packages expected today or tomorrow?")

summary = ha.query(" ".join(questions))
```

The agent checks `ha.list_capabilities()` first so it only asks about services that are actually connected — no hallucinated answers about calendar events if no calendar is integrated.

### What it produces

**SituationalContexts** — pre-armed before the event starts:
```
"BBQ party Saturday 4pm — ~12 guests expected.
 Unknown faces at front door and backyard are likely guests, not suspicious.
 Increased foot traffic at front door is expected from 3:30pm."
```

**Scheduled device actions** — via HA write-side MCP:
```
Pool heater → 82°F, scheduled Thursday 9pm (off-peak, 48h before party)
Irrigation  → skip Saturday morning (guests arriving, weather warm)
Lighting scene → "party" preset Saturday 4pm
```

**TransientIntents** — watches for expected but unconfirmed events:
```
"Notify if pool hasn't reached 80°F by Saturday noon"
"Notify when first guest arrives Saturday"
"Alert if pool heater hasn't started by Friday 6am"
```

**Morning briefing** — injected into the day's world state context:
```
"Today: grocery delivery expected 2–4pm (Amazon Fresh).
 Pool service (Carlos) scheduled 10am.
 Weather: cloudy, 68°F, light rain possible after 3pm — outdoor cameras
 may see reduced visibility."
```

### Reasoning loop

The planning agent uses tool calls freely — latency is not a constraint here:

```
1. ha.list_capabilities()             ← what services are available?
2. ha.query(forward-looking questions) ← what's coming up?
3. memory.retrieve_rules(context)      ← any standing rules triggered by this?
4. memory.get_active_contexts()        ← avoid creating duplicate contexts
5. Reason over all of the above
6. For each preparation action:
   - ha.call_service(...) via write-side MCP (immediate or scheduled)
   - memory.write_situational_context(...)
   - memory.write_transient_intent(...)
7. Compose morning briefing → store as world state annotation
```

Unlike the hot path, this agent is allowed to call multiple tools sequentially and reason between calls. Total runtime of 10–30s is acceptable — it runs hours before it matters.

### Interaction with the reactive pipeline

Proactive outputs flow seamlessly into the reactive pipeline:

- SituationalContexts created by the planning agent are picked up by the reactive pipeline's context assembly — the VLM already gets them
- TransientIntents created by the planning agent get the same triage boost as user-created ones
- The morning briefing is part of the world state snapshot the HA agent serves to context assembly

The reactive pipeline doesn't know or care whether a SituationalContext was created by a user's dismissal, by the planning agent, or by calendar priming. It's the same object.

---

## Step budgets and timeouts

Every LLM call on the hot path has an enforced timeout. Exceeded timeout = take conservative action, log the failure, continue.

| Stage | Timeout | On timeout |
|-------|---------|------------|
| Fast detector | 500ms | Skip enrichment, proceed with raw detections |
| Context assembly (parallel) | 300ms | Proceed with partial context, flag in output |
| VLM call (primary) | 8s local / 15s cloud | Log, escalate to human or take conservative action |
| VLM call (deeper assessment) | 6s | Skip second pass, act on first result |
| Action dispatch | 2s | Retry once, then log failure |

---

## Failure handling

- **Fast detector crash:** proceed to VLM with raw camera frame only; note `enrichment_unavailable` in prompt
- **Context assembly partial failure:** proceed with what assembled; note missing context in prompt so VLM can account for uncertainty
- **VLM timeout/error:** circuit breaker per backend (see §04); try next backend; if all fail, conservative action + human notification
- **Action dispatch failure:** log with full event context for replay; do not silently drop
- **Session manager crash:** sessions are durable in SQL; resume from last committed segment on restart

---

## Replayability & determinism

Every hot path run should be replayable for debugging and prompt tuning:

- Inputs logged: enrichment JSON, assembled context snapshot, prompt (minus frames for storage), output JSON
- Frames stored in object store with event_id reference
- Replay tool: re-run any event_id against current or historical prompt version
- Determinism: LLM calls use `temperature=0` on the hot path; deliberative paths may use higher temperature

---

## Versioning

- Prompt templates versioned alongside model version; breaking changes increment major version
- Output schema versioned; downstream consumers declare which schema version they accept
- Replay tool can run old events against new prompt versions to measure regression
