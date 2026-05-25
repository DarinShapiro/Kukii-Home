# 15 — Alerting & Action Policy

**Purpose:** How SentiHome rules are executed: rule firing → action dispatch → device control, with confidence tiers and human-in-the-loop where appropriate.
**Status:** drafting

---

## Flow: Detection → Rule Match → Action Dispatch

```
Camera event
  ↓
VLM reasoning → {criticality, confidence, rules_fired}
  ↓
Rule matching (from §10):
  ├─ Evaluate rule conditions against world state (from HA)
  ├─ Filter applicable rules (scope, temporal, subject type)
  └─ Match top rules
  ↓
Action dispatch (this section):
  ├─ Determine output tier (Tier 0-4 based on criticality + confidence)
  ├─ Determine routing (who gets notified, when)
  ├─ Execute actions via HA services (notify, light, lock, speak, etc.)
  └─ Policy gate (auto-allowed vs. policy-gated vs. hard-blocked)
  ↓
HA service execution:
  └─ Call ha.call_service() to execute device commands
     (this is where user experiences the result: lights on, speaker announces, etc.)
```

**Key:** Rules are defined and executed in SentiHome. HA executes the actions (device control, notifications, etc.). SentiHome queries HA for world context when evaluating conditions.

---

## Confidence tiers & escalation

The VLM output `criticality` field routes decisions through escalation tiers. Each tier determines _what surfaces are activated_ and _who gets notified_.

```
VLM output:
  criticality: "info" | "warning" | "alert"
  confidence: 0.0–1.0
  rules_fired: [rule_ids]
```

### Tier 0: Silent log (info, confidence < 0.7)

```
Example: Package delivery detected

Action:
  - log to episodic memory (captured for daily digest)
  - NOT pushed to user (no app notifications)
  - NOT voice alert
  - NOT device actions (no lights, no speaking)

Surface:
  - visible in in-app history only
  - included in nightly digest if relevant
  - queryable later ("show me all package deliveries from May")

Use case: routine expected events, low novelty
```

### Tier 1: In-app silent notification (info / warning, 0.7–0.85 confidence)

```
Example: Known visitor arriving

Action:
  - badge in-app ("visitor arriving")
  - NO push to phone
  - NO voice alert
  - NO device actions

Surface:
  - in-app activity stream / timeline
  - notification tray (when app open)
  - accessible within 5 min after event

Use case: expected events, moderate confidence, doesn't need immediate interrupt
```

### Tier 2: Push notification (warning / alert, 0.85–0.95 confidence)

```
Example: Unknown person at front door

Action:
  - push notification to specified residents
  - haptic feedback (phone vibrate)
  - optional sound (silent, low vibration, bells)
  - 60s timeout → expire unread

Content:
  "Unknown person at front door
   Confidence: 92%
   Evidence: Package delivery driver detected (no known plate)
   [View] [Dismiss] [Call police]"

Surface:
  - lock screen notification
  - app badge
  - (click → launch app → stream + clip)

Routing:
  - respect quiet hours (push silent 11pm–7am unless urgent)
  - who's home check (see Routing section below)
  - escalate to Tier 3 if unread after 30s + rule has escalate_on_timeout
```

### Tier 3: Call / wake household (alert, 0.92–0.98 confidence + high severity)

```
Example: Possible break-in attempt detected

Action:
  - push notifications ON (even in quiet hours)
  - phone call ringdown (silent mode disabled)
  - text to primary contact + secondary
  - voice announcement via home speakers

Content (voice):
  "Alert: Unknown person attempted entry at garage door.
   Police recommended. Press 1 to confirm, 2 for false alarm."

Surface:
  - cannot be dismissed (notification "sticks")
  - visible to all household members
  - confirmation required to acknowledge

Routing:
  - wake sleeping residents (if home)
  - escalate to Tier 4 if no response in 60s
```

### Tier 4: Automated emergency (alert, 0.98+ OR urgent_alert flag)

```
Example: Drowning detected in pool

Action:
  - activate sirens (internal home alarm)
  - activate emergency lights (strobing exterior lights)
  - call 911 (with pre-recorded message)
  - lockdown (lock exterior doors; optional)

Human override:
  - "False alarm" button (press within 30s to abort)
  - Cancel 911 call (if local LE already dispatched, too late)

Surface:
  - audible in entire home
  - visible to household + emergency contacts (via app push + SMS)
  - logged for legal liability (never deleted)

Use case: immediate life-safety threat
```

### Tier escalation rules

```
Tier 0 (silent log) automatically escalates to Tier 1+ if:
  - any rule with severity: "alert" fires
  - confidence exceeds 0.90
  - event matches active TransientIntent with escalate: true
  - same subject detected again within 5min with suspicious behavior

Tier 1 (in-app) escalates to Tier 2+ if:
  - user explicitly requests escalation ("call me")
  - unread for 5min AND rule has escalate_on_timeout: true
  - follow-up detection of same subject in sensitive area

Tier 2 (push) escalates to Tier 3+ if:
  - unread for 60s AND no one home (occupancy check)
  - home alarm armed + entry attempt detected
  - medical alert (elderly resident motionless > 10 min)

Tier 3 (wake) escalates to Tier 4 (automated emergency) if:
  - no household member acknowledges within 120s
  - secondary urgent condition detected (fire + break-in simultaneously)
  - 911 already dispatched (imminent emergency)
```

---

## Routing logic

### Quiet hours

```
Quiet window: user-configurable (default 11pm–7am)

During quiet hours:
  Tier 0 → still silent log (no change)
  Tier 1 → still in-app only (no change)
  Tier 2 → silent push (badge only, no sound/vibration)
  Tier 3 → wake call (sound enabled, despite quiet hours)
  Tier 4 → automated emergency (always audible)

Exception: TransientIntent with force_audio: true overrides quiet hours
  (e.g., "notify me immediately if the gate opens" at any hour)
```

### Occupancy-aware routing

```
System knows who's home via:
  - HA occupancy sensors (phone GPS, Bayesian occupancy)
  - Explicit user state (manual "I'm leaving" button in app)

Routing decision:
  Alert with criticality: "alert"

  Who's home?
    If no one home → escalate to Tier 3 (wake call) immediately
                    (push won't be seen; call gets immediate attention)

    If someone home → Tier 2 push first
                     escalate to Tier 3 if unread after 60s

    If mixed (some home, some away) → push to both
                                       call to away residents only
```

### Last-responder bias mitigation

```
Problem: when multiple residents get alerts, whoever happens to look first
         responds; others assume someone else handled it.

Solution: explicit delegation + confirmation

Flow:
  Alert received by resident_1 + resident_2 simultaneously

  resident_1 views alert (app opens)
  → alert changes to "resident_1 is reviewing"

  resident_2 sees: "resident_1 is reviewing" (de-prioritize locally)

  resident_1 dismisses → marked as "resident_1 dismissed, no action needed"

  If 5min passes without explicit resolution → escalate to resident_2:
    "resident_1 didn't respond; can you check this?"
```

### Do-not-disturb & preferences per resident

```
Resident profile includes:
  quiet_hours: 23:00–07:00 (local time)
  vacation_mode: false (if true, escalate all tiers +1)
  emergency_only: false (if true, suppress Tier 2, allow Tier 3+)
  preferred_contact: phone_push | phone_call | sms | in_app_only

Rule-level routing override:
  Rule can specify: "always call resident_1, push to resident_2"
  "do_not_alert_these_residents": []
```

---

## Conversational confirmation for ambiguous events

When the VLM is uncertain and escalation would be noisy, offer a lightweight confirmation before full alert.

```
VLM output:
  criticality: "warning"
  confidence: 0.72
  limiting_factor: "face_oblique"

Reasoner decision:
  → This is borderline. Confidence is OK but limiting factor suggests uncertainty.
  → Use conversational confirmation (ask) before pushing notification

Action:
  notify.ask(
    question: "Was that Sarah at the front door?",
    evidence_clip: clip_uri,
    response_callback_id: "ask_xyz123"
  )

  Possible responses:
    ✓ "Yes, that was Sarah" → create confirmation alert, bump confidence to 0.95
    ✗ "No, that was someone else" → alert as "unknown person", confidence unchanged
    ? "Not sure" → resolve to "unknown", suggest rule edit
    [timeout 60s] → default to "unknown person", send low-tier warning

  Alert sent only after user responds or timeout
  Pipeline waits for response (session held open)
```

**When to use ask:**

- Confidence 0.70–0.80 + limiting factor present
- Subject matches multiple candidates with similar confidence
- Rule uncertainty flag set (e.g., "birthday party; guests expected; hard to know who's guest vs. intruder")
- Medium-severity action proposed (device unlock, alarm disarm)

---

## Autonomous action policy

The action dispatcher enforces strict policy before executing any device commands via HA MCP.

### Auto-allowed (immediate execution)

```
No gate, fire immediately:
  - Lights (illuminate_area, darken_area, set_scene)
  - Non-security switches (exhaust fan, sprinkler, pump)
  - Speaking (TTS via speakers)
  - Notifications (push, SMS, email)
  - Session opens / memory writes
  - PTZ slew / profile switch (observation actions)

Examples:
  - "lights on in backyard" (to illuminate for better camera view)
  - "speak: visitor detected" (via home speaker)
  - "open session: tracking unknown visitor"
```

### Policy-gated (requires pre-approval or ask confirmation)

```
These require one of:
  1. Pre-approval rule (rule text includes "turn on lights if low_light")
  2. User confirmation via ask() → response recorded

Gated actions:
  - Lock any door (potential trap / lockout)
  - Unlock any door (potential security breach)
  - Trigger security automation (armed state changes)
  - Adjust alarm system
  - Activate siren (audible to neighborhood)

Example:
  VLM: "Face recognition low-confidence. Subject approaching door.
        Recommend: lock door if low-light."

  Dispatcher sees: ha.lock(front_door) requested

  Policy check:
    - Is there a pre-approved rule? No.
    - → surface as ask: "Lock the front door?"
    - User response needed before action executes

  Action on response:
    ✓ User confirms → lock executed
    ✗ User denies → no lock; note dismissal; learn from it
    [timeout 10s] → default conservative: no lock (better safe)
```

### Hard-blocked without explicit human confirmation

```
These require out-of-band human action (not even ask will execute):
  - Disarm alarm (prevent accidental disarm via false positive)
  - Unlock all doors at once (potential catastrophic lockout)
  - Siren activation (audible emergency; legal liability)
  - Garage door open (potential injury if mechanism broken)

Behavior:
  VLM or rule requests action → policy returns:

  {
    "error": "policy_block",
    "action": "siren_activate",
    "reason": "siren requires explicit human confirmation out-of-band",
    "suggest": "user calls 911; police disable siren on arrival",
    "fallback": "send urgent alert to household + emergency contacts"
  }

  System instead:
    - sends urgent Tier 4 alert to residents
    - residents can manually activate siren if needed
    - notifies emergency contacts
```

### Pre-approval rules

Pre-written rules can authorize actions at creation time:

```
Example rule (user writes):
  "If person detected at night AND face_confidence < 0.5
   AND no one home:
   illuminate back_door lights + lock all doors
   (because low confidence + occupancy check means suspicious)"

  Dispatcher sees: ha.lock(all_doors) + illuminate requested

  Policy check:
    Lock is gated; look for pre-approval → found (this rule authorizes it)
    All conditions met (night, face_low_confidence, no one home) → YES

  Execute: locks engage
  Log: "Pre-approved rule XYZ auto-locked doors due to suspicious activity"
```

---

## Explanation requirement

Every alert must cite its reasoning. Users see:

```
Alert: "Unknown person at front door"

Why:
  • Face recognition: no match to known residents/visitors
  • Behavior: lingering (3min in entry zone)
  • Time: 22:45 (unusual hour for unexpected visitors)

Rules fired:
  + Rule "Alert on unknown person at night" (severity: alert)
  + Rule "Linger detection" (severity: warning)

Confidence:
  Unknown person: 87%
    Evidence: face not in gallery, new appearance
  Suspicious behavior: 72%
    Evidence: standing motionless 3min, looking toward window

Action:
  [Edit Rule]  [Dismiss + Learn]  [See All Rules]
```

### Direct edit path

User sees a rule fired and wants to adjust it:

```
User clicks [Edit Rule]
→ In-app rule editor opens with this rule pre-loaded
→ Can change:
    - severity
    - temporal conditions (suppress after 10pm)
    - target residents (don't alert resident_3)
    - actions (don't lock, just notify)

→ Save → rule updated
→ Next similar event uses new rule
```

---

## Notification surfaces

### Phone (primary surface)

```
Push notification:
  - title: "Person at front door"
  - body: "Unknown visitor, 87% confidence"
  - badge count: increments per unread alert
  - sound: configurable (on/off per alert type, quiet hours aware)

Tap → opens app → stream view of camera + clip
```

### Voice (TTS via HA)

```
Triggered for:
  - Tier 3 events (when household needs to wake up immediately)
  - Explicit "speak" action in rule
  - TransientIntent with voice: true

Example:
  "Alert: person detected at front door.
   Confidence 92%. Consider reviewing the camera."

Speakers:
  - primary (bedroom); see Routing section for quiet-hours handling
  - selected zones (if rule specifies areas)
```

### In-app activity stream

```
Per-resident feed:
  - chronological list of all detected events
  - filters: by area, by rule, by confidence, by status
  - infinite scroll (queryable by date range)

Quick actions:
  - tap to view clip + enrichment
  - edit rule
  - dismiss + provide feedback
```

### Ambient / display surfaces

```
Wall-mounted display (if present):
  - shows live stream during presence
  - shows recent alerts in passive mode
  - VoiceOver / accessibility support

Garage/entry displays:
  - "Package arrived" when delivery detected
  - "Gate open, check it" when motion detected post-dark

Integration:
  - HA can push state changes to displays
  - SentiHome sends alerts via notify.push → HA → display
```

---

## Alert acknowledgment & feedback loop

```
Alert shown → User interaction recorded:

Timeline:
  t=0: Alert generated
  t=5s: User views notification (opens clip)
  t=20s: User dismisses or acknowledges

Feedback options:
  [✓ Correct alert] → rule was right, improve its recall
  [✗ False alarm] → rule was wrong, reduce its firing rate
  [? Not sure] → borderline, request more evidence
  [Edit rule] → opens rule editor

Data collected:
  - response_latency (how long before user responded)
  - feedback_type (correct/wrong/unsure)
  - user_action (dismissed/acknowledged/acted)
  - dwell_time_on_clip (how long user watched)

Aggregated:
  - Rule accuracy metrics (FP rate, FN rate)
  - Alert fatigue tracking (users dismissing N% of alerts)
  - Calibration signals for future threshold tuning
```
