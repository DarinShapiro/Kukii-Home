# Kukii-Home + HA Architecture Clarification

**Core insight:** Kukii-Home is the **rule engine** (intelligence layer). Home Assistant is the **device orchestration layer** (UX + action execution).

---

## The Correct Model

### What Kukii-Home Owns

✅ **Detection & Vision**

- YOLO object detection
- Face recognition + re-ID
- Multi-camera fusion
- Identity confidence scoring

✅ **Rule Definition & Execution**

- Conversational rule creation ("Alert me when the mailman arrives")
- Rule storage in Kukii-Home database
- Deterministic rule evaluation (fire based on conditions)
- Action dispatch to device services

✅ **Intelligence & Learning**

- VLM reasoning
- Feedback-driven optimization
- Identity learning across time
- Observability + synthesis layer
- Seasonal learning

✅ **Memory & State**

- Session management
- Episodic memory
- Identity gallery
- Visit ledger
- Active contexts + intents

### What Home Assistant Owns

✅ **World State (Input to Kukii-Home)**

- Device state (is door locked? is alarm armed?)
- Calendar events
- Weather
- Time of day / occupancy
- Energy pricing
- **Queried by Kukii-Home when evaluating rule conditions**

✅ **Action Execution (Output from Kukii-Home)**

- Notifications (push, SMS, email)
- Speaker TTS
- Lights (on/off, brightness, color)
- Locks (lock/unlock)
- Sirens & alarms
- Climate (thermostat)
- Any service in HA ecosystem
- **Called by Kukii-Home via REST API or MCP**

✅ **User Experience**

- Dashboard (web UI)
- Mobile app (Companion)
- Push notifications
- Voice interface (Assist)

✅ **Ecosystem Integration**

- Integrations (Zigbee, Z-Wave, Matter, Thread, MQTT, WiFi devices)
- Third-party services (Spotify, Sonos, Google Home, etc.)
- Calendar (Google, Apple, Outlook)
- Weather services
- Delivery tracking

---

## Flow Example: Conversational Rule Creation

```
User (to Kukii-Home):
  "Let me know when the mailman arrives.
   Unlock the front door and announce it over the Sonos."

Kukii-Home LLM:
  Parses intent:
    - Trigger: person at door + identity matches "mailman"
    - Actions: [unlock, announce]

  Creates rule in Kukii-Home:
    {
      rule_id: "rule_mailman_001",
      trigger: "person_at_door",
      conditions: [
        { type: "identity_match", entity: "mailman", confidence: 0.80 },
        { type: "occupancy", state: "someone_home" }  // ← queries HA
      ],
      actions: [
        { type: "unlock", device: "front_door" },  // → calls HA service
        { type: "announce", device: "sonos_kitchen",
          message: "Mailman has arrived" }  // → calls HA service
      ]
    }

  Returns to user:
    "Rule created. Testing on recent clips... Works on 12/14 mailman
     visits. Ready to deploy? [Yes] [Edit] [Delete]"

User:
  "Deploy it"

Kukii-Home:
  Rule is now active. Next mailman visit will trigger it.

---

Mailman arrives (real-time):

Kukii-Home detector:
  - Person detected at door (YOLO)

Kukii-Home VLM:
  - Confirms: "mailman, 0.92 confidence"

Kukii-Home rule engine:
  - Matches rule: trigger ✓, conditions ✓
  - Fires rule

Kukii-Home action dispatcher:
  - Calls HA: POST /api/services/lock/unlock
    { entity_id: "front_door" }
  - Calls HA: POST /api/services/sonos/play_media
    { entity_id: "sonos_kitchen", message: "Mailman has arrived" }

Home Assistant:
  - Executes unlock
  - Executes Sonos announcement

User:
  ✓ Front door unlocks
  ✓ Sonos announces: "Mailman has arrived"
```

---

## What This Is NOT

❌ **Kukii-Home does not:**

- Create YAML automations in HA
- Use HA's automation engine for rules
- Define triggers in HA UI
- Depend on HA blueprints

❌ **HA automations are not:**

- The primary way Kukii-Home rules execute
- How conversational rules work
- Coupled to Kukii-Home detection events

---

## Where HA Automations Still Fit

HA automations are **optional user extensions** for power users:

```
Example HA automation (user-created, optional):

Trigger:  Kukii-Home alert (binary_sensor.rule_mailman_fired)
Condition: time > 20:00  // quiet hours
Action:   Reduce Sonos volume to 30%

---

Example 2:

Trigger:  Kukii-Home detects unknown person at door
          (binary_sensor.unknown_person_at_door)
Condition: alarm armed
Action:   Send security team notification
          Turn on exterior lights
          Record 10-minute clip
```

But these are **add-ons**, not the core mechanism. The core is:

- **Kukii-Home rules** execute automatically based on detections
- **HA automations** are optional, user-defined enhancements

---

## Information Flow

```
┌─────────────────────────────────────────────────────┐
│ Sensors / Cameras                                   │
└──────────┬──────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────┐
│ Kukii-Home Detection & Vision                        │
│ (YOLO, face recognition, re-ID)                     │
└──────────┬──────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────┐
│ Kukii-Home VLM Reasoning                             │
│ + Rule Evaluation                                   │
└──────────┬──────────────────────────────────────────┘
           ↓ queries world context
           │
     ┌─────┴──────┐
     │             │
┌────▼──────┐  ┌──┴──────────────────┐
│ HA State  │  │ Kukii-Home Rules     │
│ (cached)  │  │ (stored in          │
│           │  │  Kukii-Home DB)      │
└───────────┘  └────────────────────┘
           │     ↑
           │     │ evaluate against
     ┌─────┴─────┘
     ↓
┌─────────────────────────────────────────────────────┐
│ Kukii-Home Action Dispatch                           │
│ (policy gate, confidence tier routing)              │
└──────────┬──────────────────────────────────────────┘
           ↓ calls HA services
     ┌─────────────────────────────────────┐
     │ Home Assistant Service Execution    │
     ├─────────────────────────────────────┤
     │ - Notifications (push, SMS, email)  │
     │ - Lights (on/off, dimming, color)   │
     │ - Locks (lock/unlock)               │
     │ - Speakers (TTS announcement)       │
     │ - Climate (thermostat)              │
     │ - Sirens / alarms                   │
     │ - Any HA service                    │
     └──────────┬────────────────────────┘
                ↓
     ┌─────────────────────────────────────┐
     │ Physical Actions                    │
     ├─────────────────────────────────────┤
     │ ✓ Door unlocks                      │
     │ ✓ Sonos announces                   │
     │ ✓ Notification sent                 │
     │ ✓ Lights turn on                    │
     └─────────────────────────────────────┘
```

---

## Key Design Advantages

1. **Conversational rule creation** — rules can be created/edited via conversation, not YAML
2. **Vision-native detection** — rules fire on what Kukii-Home detects, not arbitrary HA events
3. **Confidence-aware reasoning** — rules evaluate thresholds, limiting factors, multi-modal signals
4. **Learning & optimization** — Kukii-Home improves rules autonomously
5. **Clean separation** — Kukii-Home doesn't know about Zigbee/Z-Wave/Matter; HA doesn't know about vision
6. **Reuse HA ecosystem** — all HA integrations available without Kukii-Home needing plugins
7. **User control** — HA users can still create optional automations on top

---

## Updated Sections

The following architecture docs have been updated to reflect this understanding:

- **§02 (High-level architecture):** Design philosophy clarified; component map updated
- **§07 (Tool layer / MCP):** HA agent role clarified as device orchestration
- **§10 (Rule schema):** Rules live in Kukii-Home, not HA
- **§15 (Alerting & actions):** Action dispatch flow from rule → HA service
- **§20 (Decision log):** Added decision: "HA is device orchestration; Kukii-Home is rule engine"
