# 09 — VLM Prompt Assembly Contract

**Purpose:** Exact shape of the prompt sent to the VLM — what's image, what's text, how context is structured, and what the output schema looks like. The VLM makes the full decision; this is the contract that makes that reliable.
**Status:** drafting

---

## Design principle

The VLM is not a scene describer feeding a downstream reasoner. It receives frames, annotations, and full situational context together, and returns a structured decision. The persona carries the evaluative judgment; the context provides the situational frame; the rules define what to act on.

**The VLM has no knowledge of HA, lighting devices, or any remediation actions.** It reports what it observes — including the factors limiting its confidence — and the action dispatcher owns the response to those factors. The VLM's job is honest observation and judgment, not knowing what to do about bad lighting.

**Observation and evaluation are one unified act.** The VLM does not observe first and evaluate second. It evaluates the scene *through the lens of the rules and context it was given* in a single pass. Every output field reflects that evaluation:

- `confidence` is confidence in the **rule evaluation**, not raw image quality
- `confidence_limiting_factors` are factors preventing a **confident rule evaluation** — relative to the rules in scope, not absolute image conditions. Low light may not limit confidence for a dwell-time rule but will for one requiring identity confirmation.
- `rules_fired` is the direct result of applying rules to what was seen
- `reasoning` explains the evaluation, not just the scene
- `deeper_assessment_reason` describes what would allow a more confident **rule evaluation**

The same scene with different rules in scope can produce different `confidence_limiting_factors`. The VLM is always answering: *"given what I was asked to look for, can I say with confidence whether it's happening?"*

---

## Image budget

| Element | Count | Resolution | Notes |
|---------|-------|-----------|-------|
| Full annotated frames | 4–8 | 768–1024px longest side | Detector-guided selection (best visibility per subject) |
| Clean reference frames | 2–4 | Same | Sent alongside annotated; VLM uses both |
| Subject crops | 1–2 per unknown subject | Up to 512px | Tight high-res crop for face/appearance detail |
| Journey montage (session close only) | 1 per segment, max 12 | 512px | 1–2 best frames per camera segment |

Frame count scales with sequence complexity. Standard event: 8 frames + crops. Sequence completion watch: 3–5 frames across phases (see §08). Journey close: up to 12 montage tiles.

Image budget is fixed early per model variant and enforced at assembly time — not adjusted per-event.

---

## Prompt structure

### System prompt (cacheable prefix)

The system prompt is stable across events for a given area/time-of-day context. It is the primary cache target — kept identical across calls so the provider's KV cache stays warm.

```
[PERSONA]
You are a thoughtful, experienced home security analyst for this household.
You know the residents, their routines, their regular service workers, and
what normal looks like here at different times of day and year. You are calm,
not paranoid. You notice what is genuinely off. You do not over-alert.

[HOME CONTEXT]
Property: {site_description}
Current time: {time}, {day_of_week}, {season}
Who is home: {occupancy_summary}
Alarm state: {alarm_state}
Recent relevant events: {brief_episodic_summary}  ← 2–3 sentences max

[ACTIVE SITUATIONAL CONTEXT]
{situational_contexts}   ← e.g. "Tonight is Halloween. Groups of children
                            in costumes approaching the front door repeatedly
                            is expected and normal."
  or: (none active)

[ACTIVE WATCHES]
{transient_intents}      ← e.g. "User asked: notify when Bob's car arrives"
  or: (none active)

[RULES IN SCOPE FOR THIS AREA AND TIME]
{retrieved_rules}        ← top-K hybrid-retrieved, formatted as plain text
                            e.g. "• Unknown person at front door after 10pm → urgent alert"
                                 "• Pool area occupied → check for unaccompanied child"

[KNOWN ACTORS]
{identity_candidates}    ← top 2–3 matches with confidence and access profile summary
                            e.g. "#3 Carlos (pool service) — 0.71 confidence
                                  Allowed: backyard, pool area, Thu 8am–5pm Apr–Oct"
  or: (no known match)
```

### Per-event variable tail (not cached)

Appended after the system prompt. Changes every call.

```
[EVENT]
Camera: {camera_label} ({area})
Trigger: {trigger_kind} at {timestamp}
Fast detector summary: {enrichment_text}
  — subjects: {track_ids, classes, confidence}
  — face recognition: {results or "unresolved"}
  — re-ID: {embedding_ref}
  — attributes: {clothing, held objects, pose notes}

[AREA OBSERVATIONAL CAPABILITIES]
  ← what this area can do to improve observation; included so the VLM
     can make accurate deeper_assessment_reason statements.
     No HA entity names — only observational facts.
  e.g. "PTZ detail camera: available (shared, 1–2s slew time)"
       "Additional camera angles: east_side_cam (partial overlap)"
       "Supplemental lighting: none available for this area"
       "Camera profile: currently low-res sub-stream;
        high-res main stream available"
  or:  "No additional observational resources for this area"

[FRAMES]
{annotated_frames}    ← images inline
{reference_frames}    ← clean versions
{subject_crops}       ← tight crops of unknown/attention subjects

[TASK]
Evaluate what you see against the rules and context above.
If your confidence is limited, report the specific factors — do not
guess at remediation actions. Those are handled downstream.
Return a JSON object matching the output schema exactly.
```

---

## Output schema

The VLM returns a single JSON object. Downstream pipeline stages are deterministic consumers of this schema — no free-form prose parsing.

```json
{
  "alert_required": true,
  "criticality": "urgent | notice | info | none",
  "confidence": 0.41,

  "confidence_limiting_factors": ["low_light", "subject_partially_occluded"],

  "action": "urgent_alert | notify | ask | speak | log | none",
  "draft_notification": "Human-readable alert text, cited to what was seen",
  "reasoning": "Brief explanation of why rules fired or why nothing warranted action",

  "rules_fired": ["rule_id_1", "rule_id_2"],

  "deeper_assessment": true,
  "deeper_assessment_reason": "low light limiting face and behavior read — improved illumination or tighter framing would increase confidence",

  "journey_open": true,
  "attention_mode": "pool_occupied | null",
  "sequence_watch": {
    "type": "completion_watch",
    "trigger": "dog_squat_detected",
    "duration_s": 90
  },

  "subjects": [
    {
      "track_id": 3,
      "identity_ref": null,
      "identity_confidence": 0.0,
      "behavior_summary": "possible person at perimeter, low confidence due to lighting"
    }
  ],

  "scene_summary": "Possible unknown person at perimeter, night, low confidence"
}
```

### Field definitions

| Field | Type | Purpose |
|-------|------|---------|
| `alert_required` | bool | Primary decision signal for action dispatch |
| `criticality` | enum | Maps to notification tier (see §15) |
| `confidence` | float | Compared against rule `confidence_required`; gates action |
| `confidence_limiting_factors` | enum[] | Why confidence is below ideal — action dispatcher maps these to remediations (see §06). VLM reports the problem; dispatcher owns the fix. |
| `action` | enum | Exact output class (see §15 taxonomy). Never `device_action` — VLM has no HA knowledge. |
| `draft_notification` | string | Human-readable text; cited to evidence |
| `reasoning` | string | Audit trail; shown alongside alert so user can edit the rule |
| `rules_fired` | string[] | Rule IDs that contributed to decision; required for explainability |
| `deeper_assessment` | bool | Request one additional bounded pass (see §06) |
| `deeper_assessment_reason` | string | Free-text description of what would resolve the gap — phrased in observational terms, not HA actions |
| `journey_open` | bool | Signal to session manager to open/update a session |
| `attention_mode` | string\|null | Attention mode to activate (see §08) |
| `sequence_watch` | object\|null | Sequence completion watch to open (see §08) |
| `subjects` | array | Relevant subjects for episodic memory and session tracking |
| `scene_summary` | string | Written to episodic memory on session close |

### `confidence_limiting_factors` enum

```
low_light                  ← insufficient illumination
subject_partially_occluded ← partially behind object, vehicle, foliage
subject_facing_away        ← back to camera, no face visible
subject_too_small          ← too distant for reliable feature read
motion_blur                ← subject or camera movement during exposure
adverse_weather            ← rain, fog, snow degrading image quality
camera_obstructed          ← lens dirty, spider web, physical obstruction
low_resolution             ← sub-stream profile, insufficient detail
glare_or_overexposure      ← direct light source, reflection
multiple_subjects          ← overlapping subjects confusing track assignment
```

The action dispatcher maps these to available remediations (see §06). The VLM names the problem; the dispatcher decides what — if anything — can be done about it.

### Schema enforcement

Structured output is enforced at the API level, not just prompted:

- Ollama: `format: "json"` + JSON schema in system prompt
- OpenAI-compatible: `response_format: { type: "json_schema", json_schema: ... }`
- Cloud providers: native structured output / function-call response mode

Schema is strict: required fields, enum constraints on `criticality` and `action`. A malformed response on the hot path is a reliability failure — the pipeline must handle it with a conservative fallback action and an alert to ops.

---

## `deeper_assessment` handling

When `deeper_assessment: true`, the action dispatcher consults the remediation registry using `confidence_limiting_factors` as the lookup key (see §06 for the full registry). The VLM does not prescribe the remediation — it only reports the limiting factors and a free-text reason.

The second VLM pass receives updated inputs (better frames, additional context) and a note explaining what changed:

```
[REASSESSMENT NOTE]
Previous assessment confidence: 0.41
Limiting factors addressed: low_light (area illuminated), subject_too_small (PTZ crop provided)
Reassess with updated frames below.
```

Hard cap: two VLM calls on hot path. If the second pass also returns `deeper_assessment: true`, take the conservative action and exit. Do not loop.

---

## Journey-close prompt

Called once when a session closes (silence timeout or known egress). Latency-tolerant.

```
[SYSTEM: same persona as hot path]

[SESSION SUMMARY]
Session ID: {id}
Duration: {open → close}
Areas visited: {sequence}
Journey score: {suspicion level, intent hypotheses}

[MONTAGE]
{1–2 best frames per segment, captioned:}
  [cam_label | area | t+Δs | dwell | entry→exit direction]
{Site map tile with inferred path, including blind-spot gaps}

[TASK]
Summarize what happened in this session. Note anything worth
remembering. Flag if this warrants a rule proposal.
Return structured JSON: { summary, notable, propose_rule, rule_text? }
```

---

## Two-step fallback (weaker backends)

When `supports_visual_reasoning: false` (see §04), the pipeline splits the call:

**Call 1 — description only:**
```
[minimal system prompt — no rules, no context]
Describe what you see: subjects, locations, actions, attributes.
Return structured scene description JSON.
```

**Call 2 — text reasoning (no images):**
```
[full context + rules + persona]
Scene description: {output of call 1}
Evaluate against rules and return decision JSON.
```

The intermediate scene description JSON is cached per clip — if the same clip is evaluated against a different rule set (e.g., during report generation), call 1 can be skipped.

---

## Prompt cache layout

The system prompt is structured to maximize cache hit rate:

```
[stable across all events]          ← cache target
  persona
  home context (changes slowly)
  area layout description

[stable within a time window]       ← secondary cache target
  active situational contexts
  who is home (changes per presence event)

[per-event, never cached]
  retrieved rules (depend on event query)
  identity candidates (depend on detection)
  event details + frames
```

Cloud providers (Anthropic, OpenAI) cache on exact prefix match. The stable section should be kept byte-identical across calls. Any field that changes frequently (timestamps, exact occupancy) belongs in the variable tail.

---

## Annotation conventions

The VLM prompt references annotation marks that appear in the frames:

- `#N` — track ID label on each subject bounding box
- Color tiers: green=resident, blue=known-visitor, yellow=unknown-benign, red=unknown-attention
- `✓` — confident identity match; `?` — tentative; no mark — unknown
- Confidence shown when below threshold: `Sarah? (0.72)`
- Motion arrows on multi-frame montages
- Area/zone boundary overlays where relevant to active rules

Set-of-Mark vs. native label strategy: A/B per model. Qwen2.5-VL and InternVL behave differently — test both and set per backend in the model registry.

---

## Model-specific variants

| Backend | Notes |
|---------|-------|
| Qwen2.5-VL 72B (local) | Primary local target; strong visual reasoning; test SoM vs native labels |
| InternVL2 (local) | Alternative; different annotation grounding behavior |
| Claude (cloud) | Native structured output via tool-call response; prompt cache via `cache_control` |
| GPT-4o (cloud) | `response_format: json_schema`; strong reasoning; higher cost |
| Smaller local VLMs | `supports_visual_reasoning: false` → two-step fallback path |
