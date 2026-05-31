# Rule Scenarios & SLAs

**Purpose:** Enumerate the types of rules the system must handle accurately, with concrete scenario examples and latency/quality targets per output class.
**Status:** drafting

A "rule" is a combination of **trigger conditions** and an **output action**. Output actions are broader than alerts — they include reports, memory writes, device actions, conversational responses, and proactive suggestions.

---

## Output action taxonomy

| Class           | Description                                        | Latency target                    | Example                                                    |
| --------------- | -------------------------------------------------- | --------------------------------- | ---------------------------------------------------------- |
| `urgent_alert`  | Immediate interruption — wake people, fire siren   | < 10s from trigger                | Unknown person trying door handle at 2am                   |
| `notify`        | Push / in-app notification, non-urgent             | < 30s                             | Package dropped at front door                              |
| `ask`           | Conversational confirmation before acting          | < 20s for prompt; human loop open | "Unfamiliar person at back fence 90s — turn floods on?"    |
| `speak`         | Voice announcement inside home                     | < 10s                             | "Someone's at the front door"                              |
| `device_action` | Flip a switch, lock/unlock, change a scene         | < 5s from decision                | Turn on flood lights on perimeter breach                   |
| `log`           | Silent record in episodic memory, no notification  | best-effort < 60s                 | Regular mail carrier at mailbox                            |
| `report`        | Scheduled or on-demand structured summary          | within scheduled window ± 5 min   | Daily event digest, overnight summary                      |
| `propose_rule`  | Agent surfaces a candidate rule for user review    | async, best-effort                | "I've seen this vehicle 4 Tuesday mornings — want a rule?" |
| `label_prompt`  | Ask user to label a recurring unknown face/vehicle | async, batched                    | "This person has appeared 3 times — who is this?"          |

---

## Rule trigger taxonomy

| Trigger type      | Description                                                  |
| ----------------- | ------------------------------------------------------------ |
| `motion_event`    | Camera-native or fast-detector motion/object detection       |
| `presence`        | Known or unknown person/vehicle enters/exits an area         |
| `dwell`           | Subject remains in an area beyond a time threshold           |
| `session_pattern` | Multi-camera journey matches a behavioral template           |
| `device_state`    | Door/lock/window/sensor changes state                        |
| `time`            | Cron-style schedule (daily, weekly, sunrise/sunset-relative) |
| `composite`       | Boolean combination of multiple triggers + context           |
| `absence`         | Expected thing did not happen (no motion at usual time)      |
| `query`           | On-demand: user asks a question about past or future         |

---

## Scenario catalog

Each scenario has: description, trigger type, output class, SLA, accuracy target, and notes on what makes it hard.

---

### S1 — Unknown person at door (night)

**Trigger:** `presence` — unknown face, front_porch area, 22:00–06:00
**Output:** `urgent_alert` → `ask` if no policy pre-approval for `device_action`
**Latency SLA:** Alert delivered < 10s from first frame with confident detection
**Accuracy target:** False-positive rate < 1/week; false-negative rate near zero (missed intrusion is unacceptable)
**Hard parts:** Low-light face detection quality; known resident returning late must not fire; delivery driver finishing at 11pm should not page everyone

---

### S2 — Package delivery

**Trigger:** `presence` — person + carried-object detection, front_porch, daytime
**Output:** `notify` — "Package delivered at front door"
**Latency SLA:** < 30s from drop
**Accuracy target:** False-positive rate < 1/day; should not re-fire if person lingers after drop
**Hard parts:** Distinguishing drop-and-leave from loitering; no face needed (de-identified OK)

---

### S3 — Known resident arrives home

**Trigger:** `presence` — recognized face or vehicle plate, driveway/front area
**Output:** `notify` (low-priority) or `device_action` (disarm, unlock, turn on lights) per policy
**Latency SLA:** < 15s from vehicle/person visible
**Accuracy target:** Misidentifying a stranger as a resident and triggering unlock is a critical failure; false-negative (missed arrival) is tolerable
**Hard parts:** Night/weather degraded face confidence; resident in unfamiliar vehicle

---

### S4 — Person loitering / casing pattern (multi-camera)

**Trigger:** `session_pattern` — subject visits ≥ 3 perimeter areas, no approach to door, dwell > threshold per area, within a session window
**Output:** `urgent_alert` or `ask` depending on confidence
**Latency SLA:** Alert on pattern completion < 30s; pattern must be detected before subject exits
**Accuracy target:** High false-negative cost (missed casing); false-positive 1–2/week acceptable if alert explains evidence
**Hard parts:** Re-ID across cams (clothing consistent, face often not visible); spatial plausibility check (must use adjacency graph); benign explanations (neighbor walking dog)

---

### S5 — Repeated late-night perimeter approach (cross-day pattern)

**Trigger:** `composite` — episodic memory match: same-or-similar subject, same approach route, N≥3 nights, same time window
**Output:** `notify` with session montage; escalate if pattern continues
**Latency SLA:** Async — delivered during business-hours review window or at next morning's digest
**Accuracy target:** Evidence-backed; UX must express uncertainty ("someone matching Tuesday's visitor, same route, similar height — no face match")
**Hard parts:** Cross-day identity is a composite probability, not a track ID; avoid false certainty in UX

---

### S6 — Door/window left open or unlocked (time-based)

**Trigger:** `composite` — device_state (door unlocked) + time (quiet hours start) + who_home (someone home)
**Output:** `notify` → `ask` ("Back door has been unlocked for 3 hours, want me to lock it?")
**Latency SLA:** Alert within 5 min of condition becoming true
**Accuracy target:** Near-zero false-positive (HA device state is authoritative); main risk is alert fatigue if threshold is too tight
**Hard parts:** Intentional unlock (guest, airing out) — user-dismissal should back off

---

### S7 — Unknown vehicle parked outside (repeated)

**Trigger:** `presence` — plate not in known list, parked > dwell threshold, recurrence across days
**Output:** First occurrence: `log`; second: `notify`; third+: `label_prompt`
**Latency SLA:** Notify < 60s; label_prompt batched to next digest
**Accuracy target:** Plate OCR must be high-confidence before firing — partial reads should not match as "same vehicle"
**Hard parts:** OCR failure rates at angle/night; partial plates; vehicles belonging to neighbors

---

### S8 — Child left unattended in pool/yard area

**Trigger:** `presence` — child (age-estimated small person) in pool_zone or side_gate_zone, no adult present, dwell > Ns
**Output:** `urgent_alert`
**Latency SLA:** < 10s
**Accuracy target:** False-negative is critical failure; false-positive at 1–2/week acceptable given severity
**Hard parts:** Age estimation from detector; "no adult present" requires tracking multiple subjects; pets may look like small persons at camera angles

---

### S9 — Unusual behavior: person on roof / climbing fence

**Trigger:** `motion_event` + VLM scene reasoning — pose/position implies climbing; height-above-ground via calibration
**Output:** `urgent_alert`
**Latency SLA:** < 15s
**Accuracy target:** High; requires calibrated geometry or VLM spatial reasoning
**Hard parts:** Needs either stereo calibration or VLM reasoning; ladder-using contractors must not fire

---

### S10 — Familiar unknown: recurring but unlabeled face/vehicle

**Trigger:** `presence` — embedding similarity to prior unlabeled gallery entry, 3rd+ occurrence
**Output:** `label_prompt` — show user a montage of appearances, ask for label
**Latency SLA:** Async; batched into daily digest or next convenient moment
**Accuracy target:** Only prompt when embedding similarity is above confident threshold across multiple sightings
**Hard parts:** Avoid accumulating too many gallery entries; merge logic for same person seen at different times

---

### S11 — Daily event digest

**Trigger:** `time` — daily at configured hour (e.g., 7:00am)
**Output:** `report` — narrative + structured list of notable events from past 24h, optionally upcoming-day context (HA calendar, expected visitors)
**Latency SLA:** Delivered within ±5 min of scheduled time
**Accuracy target:** Should not hallucinate events; should surface interesting/unusual events, not just all events; must indicate confidence where appropriate
**Hard parts:** What counts as "notable" — needs a novelty/significance signal, not just recency; must aggregate across sessions and device events

---

### S12 — Overnight security summary

**Trigger:** `time` — morning, or on-demand
**Output:** `report` — what happened while household slept; any concerns flagged; any rules that fired
**Latency SLA:** Ready when first person wakes (HA presence detection) or at configured time
**Accuracy target:** Complete — every rule-firing event should appear; false negatives in report are a trust failure
**Hard parts:** Session close/open around overnight window; events that span midnight; context about what was expected (known alarm armed, guest expected)

---

### S13 — On-demand natural-language query

**Trigger:** `query` — user asks "Was anyone here between 2 and 4pm?" or "How many times has that white truck been by this week?"
**Output:** `report` or `speak` — grounded answer with timestamps and evidence
**Latency SLA:** < 10s for simple retrieval; < 30s if VLM re-analysis needed
**Accuracy target:** Must be grounded in actual memory/events — no hallucination; uncertainty stated explicitly
**Hard parts:** Query decomposition, temporal reasoning, identity ambiguity in answer

---

### S14 — Proactive rule suggestion

**Trigger:** `composite` — pattern mining over episodic memory: high-confidence recurring event with no existing rule, or rule that fires but is always dismissed
**Output:** `propose_rule` — "Every Tuesday between 8–9am, a white van parks outside for ~30min. Want a rule for that?"
**Latency SLA:** Async; weekly batch or triggered by N dismissals
**Accuracy target:** Suggestions must be well-evidenced; false suggestions damage trust; show the underlying evidence
**Hard parts:** Distinguishing noise from pattern; phrasing rule proposals naturally

---

### S15 — Alarm-armed + any motion

**Trigger:** `composite` — alarm_armed (HA state) + any motion event, any exterior cam
**Output:** `urgent_alert` immediately; escalate to siren+lights if no dismiss within Ns
**Latency SLA:** < 5s; siren escalation < 30s if unacknowledged
**Accuracy target:** Near-zero false-negative; false-positives here are high-cost (wakes everyone, triggers alarm company) so confidence gate must be high OR require explicit pre-arming by user
**Hard parts:** Animals, wind-triggered motion; must not fire on residents who arm-and-exit

---

### S16 — Known dog unaccompanied in front yard (escaped)

**Trigger:** `presence` — animal/dog detected in front_yard area, no person detected within proximity threshold, within same frame window; escalated if dog is recognized from gallery
**Output:** `urgent_alert` to household — "Rex is in the front yard unaccompanied — possible escape from backyard"
**Latency SLA:** < 15s from first confident dog detection without person
**Accuracy target:** False-negative is high-cost (dog in road); false-positive from neighbor walking a leashed dog past the yard is the main noise source — person accompaniment check must cover leash/proximity, not just frame co-presence
**Hard parts:**

- Dog detection and species/breed recognition are less mature than person detection — confidence thresholds need tuning
- "Accompanied" definition: person must be in the same area, not just the same wide frame; a person visible on the sidewalk is not accompanying a dog in the yard
- Cross-camera corroboration: if dog was last seen in backyard and is now in front without any gate/door event from HA, that's strong supporting signal — system should cite it
- Known dog recognition: requires a pet gallery with enrollment UX; if dog is unknown, alert is lower severity ("unfamiliar dog in front yard — may be loose in neighborhood")
- Day vs night: nocturnal escape is higher urgency (harder to find)
- Don't fire when the dog is intentionally in the front yard on a leash with a resident

---

### S17 — Repeated unanswered door approach across time

**Trigger:** `composite` + `session_pattern` (cross-day) — same or likely-same person appears at front door, knocks or rings, departs without door opening, recurs across multiple visits; escalating severity with recurrence count and time span
**Output:** `log` (first visit) → `notify` (2nd visit same day or within 48h) → `notify` with montage (3rd+ visit or first cross-week recurrence) → `urgent_alert` + `propose_rule` if pattern continues
**Latency SLA:**

- Per-visit classification (unanswered knock): < 60s after departure confirmed
- Cross-visit pattern notification: < 10 min after triggering visit ends
  **Accuracy target:**
- "Unanswered" must be inferred from evidence, not assumed — door sensor (HA) did not open AND person departed; false positives from "answered via intercom / resident chose not to answer" require a dismiss path
- Cross-visit identity: same caveats as S5 — composite probability, not certainty; UX must express it
- Solicitors, campaigners, and delivery-attempted notices share this pattern — low-severity first occurrences are expected; the pattern only becomes notable at 3+ visits, especially cross-week
  **Hard parts:**
- "No answer" inference: strongest signal is door sensor (HA) not opening while person is present + person departing; secondary signal is VLM scene — person knocked, waited, left; audio doorbell ring from HA is a clean trigger if available
- Distinguishing "they didn't answer" from "I didn't see them answer" — if interior cameras are in scope, resident appearance in entry zone counts as an answer
- Time-span memory: this pattern spans weeks; requires episodic memory retention and cross-day identity at the session-history level, not just within a journey session
- Escalation policy: 3 visits in a day is a different severity signal than 3 visits across 3 weeks — escalation logic needs both recency and span dimensions
- Intentional non-answers by residents (screening calls at the door) should not keep escalating — a single in-app "I saw them, not interested" dismiss should suppress the pattern

---

### S18 — Dog walker doesn't pick up

**Trigger:** `composite` + temporal sequence — dog detected squatting/defecating in front yard, sidewalk, or street adjacent to property; walker departs without the pickup action occurring within a ~90s observation window
**Output:** `notify` with timestamped clip — low urgency, evidence-grade (useful if you want to address it with the person)
**Latency SLA:** < 3 min after walker departs (clip must be retained)
**Accuracy target:** High false-positive tolerance — this is a courtesy issue, not safety; a few false positives per month are acceptable. False-negatives are also tolerable (missing it is not harmful). What matters is: when it fires, the evidence should be clear enough to be convincing.
**Hard parts:**

- The alert is triggered by **absent behavior** — no pickup — not by a positive detection. The system watches for an expected action that doesn't occur, not for something that does.
- Dog squat pose is a specific detection target; may need pose estimation fine-tuned on dogs or VLM reasoning over a short clip sequence
- The "pickup" action is subtle: person bends down, waste bag appears/used, deposit disappears from ground. Heavily angle- and distance-dependent. From street-facing cameras at normal distances, this may be barely resolvable.
- If detection confidence is low, output should be `log` not `notify` — send to digest as a "possible" rather than a confident alert
- Clip must be retained as evidence; standard short-clip retention applies

**Architectural note — sequence completion watch:**
This introduces a pattern distinct from both normal event processing and full AttentionMode: a **short-duration sequence completion watch**. On dog-squat detection, a lightweight 60–90s sustained observation window opens on that camera. The question is binary: did the completion action (pickup) occur before the subject left frame? If yes → log only. If no → notify with clip. This is lighter than a life-safety AttentionMode but requires the same "watch beyond the initial event" mechanism at a lower resource level.

---

## SLA summary table

| Output class         | Latency target                                 | Notes                                  |
| -------------------- | ---------------------------------------------- | -------------------------------------- |
| `urgent_alert`       | < 10s                                          | Clock starts at first actionable frame |
| `device_action`      | < 5s from decision                             | Decision latency not included          |
| `speak`              | < 10s                                          |                                        |
| `ask`                | < 20s to surface question                      | Human loop then open-ended             |
| `notify`             | < 30s                                          |                                        |
| `log`                | < 60s, best-effort                             |                                        |
| `report` (scheduled) | ±5 min of scheduled time                       |                                        |
| `report` (on-demand) | < 10–30s depending on retrieval vs re-analysis |
| `propose_rule`       | Async, weekly or on N dismissals               |                                        |
| `label_prompt`       | Async, batched to digest                       |                                        |

---

## False-positive tolerance by scenario class

| Class                                  | Tolerance                                                         | Reasoning                                             |
| -------------------------------------- | ----------------------------------------------------------------- | ----------------------------------------------------- |
| Intrusion / security (S1, S4, S9, S15) | Very low (1–2/week max)                                           | Each false alert erodes trust; 3am alert is high cost |
| Safety — pet escape (S16)              | Near-zero false-negative; moderate false-positive OK              | Dog in road is a safety event                         |
| Safety — child alone (S8)              | Near-zero false-negative; moderate false-positive OK              | Miss is critical                                      |
| Informational (S2, S3, S7)             | Moderate (a few/day)                                              | Low interrupt cost                                    |
| Reports & digests (S11, S12, S13)      | Zero hallucination; completeness required                         |                                                       |
| Pattern / cross-day (S5, S14, S17)     | Evidence must be shown; uncertainty stated; dismiss path required | User can verify                                       |

---

## Open questions (rule-requirements specific)

- How does "confidence_required" on a rule interact with the triage priority scorer? Same threshold or separate?
- Who resolves conflicting rules (e.g., "never notify after 11pm" vs "always notify for unknown at door")?
- What's the minimum retention window needed to support cross-day pattern detection (S5, S14)?
- Seasonal/conditional rules: how are sunrise/sunset, weather, and school-schedule conditions sourced and kept fresh?
- Multi-resident: whose `notify` target list is used when two conflicting preference rules match?
- Pet gallery: enrollment UX for known pets (S16); how does pet re-ID work across coats/seasons?
- "Unanswered" inference (S17): if no door sensor is available, can VLM scene alone reliably determine the door wasn't opened?

---

### S20 — Person fell asleep watching TV (wearable sleep detection)

**Trigger:** `ha_state` — resident's sleep state sensor transitions to `asleep` or `in_bed` (via Apple Watch → HA Companion App → HA entity); AND media player or TV on in that person's bedroom or associated area

**Output:** `device_action` — silent, no notification (they're asleep). Gradual response to avoid disturbing light sleep.

**Latency SLA:** 5–10 min after sleep detection confirmed — not immediate (brief sleep detection can be a doze, not true sleep)

**Routing:** pure HA-state path, no camera, no VLM. HA poller detects sleep state change → synthetic event → sensor rule evaluator.

**Response sequence — gradual, not abrupt:**

```
sleep_state: asleep confirmed (stable for 3+ min)
  │
  ▼
Dim lights in room to 10% (if on)  ← gentler than immediate off
  │
  wait 2 min
  │
  ▼
sleep_state still asleep?
  ├── yes → turn off TV, turn off lights, set thermostat to sleep temp
  └── no (person woke up) → restore lights, do nothing to TV
```

**Multi-person nuance:** if two residents share the space, don't act if the other person's sleep state is `awake`. The rule only fires when all occupants of the area are asleep or absent.

**Sleep stage awareness:** Apple Watch distinguishes sleep stages (light, deep, REM). Deep sleep → act more confidently. Light sleep / in-bed → wait longer before acting, or skip TV-off entirely (person may be about to wake). Stage data is available as HA sensor attributes if the companion app exposes them.

**Why this works through HA with no Kukii-Home-specific integration:**

```
Apple Watch
  → detects sleep via heart rate + accelerometer
  → HA Companion App on iPhone
  → HA entity: sensor.person_name_sleep_state = "asleep"
  → HA poller detects state change
  → synthetic event on bus
  → rule evaluates: sleep + TV on → gradual device_action
  → ha.call_service(media_player, turn_off, bedroom_tv) via write-side MCP
```

No HealthKit SDK, no Apple Watch integration, no wearable-specific code in Kukii-Home. HA handles the data bridge; Kukii-Home reasons over the result.

**Generalises to any wearable:** Fitbit, Garmin, Oura Ring — anything that exposes sleep state to HA via its companion integration flows through the same path. The rule references the semantic concept `resident.sleep_state`, not a device-specific sensor name.

**Hard parts:**

- Sleep detection latency: Apple Watch typically takes 2–5 min to confirm sleep onset — rule should wait for stable confirmation, not react to the first `asleep` reading
- False positives: "theatre mode" on Apple Watch (intentional movie watching in dark) should be distinguishable — HA Companion App focus modes can suppress sleep detection
- TV control: depends on HA having a working media_player integration for the TV (smart TV native, Roku, Apple TV, IR blaster via Broadlink, etc.) — graceful no-op if TV not controllable

---

- Escalation span model (S17): should recurrence weighting decay (old visits matter less) or accumulate? What's the reset condition?
- Cross-entity reasoning: S16 requires correlating dog position with last-known-location (backyard) via HA device state (gate sensor) — is that a composite rule or a session-pattern rule?

---

### S19 — Lights left on at night outside bedrooms

**Trigger:** `time` + `ha_state` — quiet hours begin (configurable, e.g. 11pm) AND one or more lights are on in non-bedroom areas

**Output:** varies by occupancy confidence (see below) — `device_action` (turn off silently), `ask` (confirm before acting), or `log` (SituationalContext says leave it)

**Latency SLA:** 5–15 min after quiet hours start; not time-critical, but shouldn't fire at 3am for a light that came on at 11:01pm

**Routing:** pure HA-state path — no camera event, no VLM. `ha.query()` synthesizes occupancy confidence; deterministic rule evaluator decides action. Camera last-detection time is one input among several, not the primary signal.

**Occupancy confidence is the key variable.** The HA agent synthesises across multiple signals:

| Signal                    | Suggests occupied               | Suggests empty            |
| ------------------------- | ------------------------------- | ------------------------- |
| HA motion sensor          | Motion < 15 min ago             | No motion > 45 min        |
| Smart plug power draw     | TV/device drawing power         | Standby or off            |
| Camera last detection     | Person detected < 20 min        | No detection > 30 min     |
| Who's home (presence)     | Resident unaccounted for        | All residents in bedrooms |
| Active SituationalContext | Party, guests, late night event | None                      |

**Action policy by confidence:**

| Occupancy confidence        | Who's home                                 | Action                                                           |
| --------------------------- | ------------------------------------------ | ---------------------------------------------------------------- |
| Low — no signals of use     | Anyone or nobody                           | `device_action`: turn off silently, log                          |
| Medium — mixed signals      | Nobody home                                | `device_action`: turn off silently                               |
| Medium — mixed signals      | Someone home                               | `ask`: "Living room lights still on — want me to turn them off?" |
| High — TV on, recent motion | Someone home                               | `log` only — lights are in use                                   |
| Any                         | Active SituationalContext covers this area | `log` only — context says leave it                               |

**SituationalContext interaction:** if a context is active that marks an area as occupied or in-use (party, guest staying over, late-night work session), the rule does not fire for that area. The context gates the rule entirely — no ask, no action.

**Bedroom list is semantic, not hard-coded:** bedrooms are areas tagged `role: bedroom` in the area/zone model (§13). Guest rooms in use should be temporarily tagged as bedrooms (via TransientIntent or SituationalContext: "guest staying in office this week") so they're excluded from the rule.

**Hard parts:**

- Occupancy confidence synthesis requires the HA agent to query across heterogeneous signals (motion sensors, power meters, camera detections) — the `ha.query()` LLM layer is what makes this tractable without per-installation hardcoding
- "Left on accidentally" vs "left on intentionally" is the core ambiguity — the ask path is the safety valve when confidence is medium
- Quiet hours start time should respect who's home: if a night-owl resident is active, quiet hours for this rule may start later than the household default
