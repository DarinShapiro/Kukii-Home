# 10 — Rule Schema & Retrieval

**Purpose:** How rules are defined, stored, retrieved per event, and executed in SentiHome. Rules are created conversationally and evaluated deterministically (not via HA automations).
**Status:** drafting

---

## Core principle: Rules live in SentiHome

**Rules are NOT HA automations.** SentiHome is the rule engine:

- Rules are created conversationally (user talks to LLM)
- Rules fire based on SentiHome detections (people, animals, objects, identity)
- Rules evaluate conditions against HA world state (but are owned by SentiHome)
- Rules dispatch actions to HA services (lights, notifications, locks, speakers, etc.)

**HA automations are optional user extensions** (e.g., "if X event happens, do Y in HA"), but they are not the primary way SentiHome alerts trigger.

---

## Rule record shape

```json
{
  "rule_id": "uuid",
  "text": "Alert if dog in front yard without person",
  "embedding": [0.234, ..., 0.891],
  "scope": "area | camera | zone | journey | composite | global",
  "scope_ref": "area_id | camera_id | zone_id | null",

  "temporal": {
    "active_hours": "08:00–22:00 | null",
    "active_days": ["Mon", "Tue", ...] | null,
    "exclusions": ["2026-05-23 09:00–10:30"],
    "ttl": "30d | null"
  },

  "conditions": {
    "subject_type": "person | pet | vehicle | object | null",
    "subject_known": "known | unknown | specific_actor",
    "location": "area_id | zone_id | null",
    "context_required": ["alone", "unattended", "night"] | null,
    "detections_required": ["dog", "person"] | null,
    "exclude_if_detected": ["owner_name"] | null
  },

  "severity": "alert | warning | info",
  "actions": [
    {
      "type": "notify | speak | ask | light_scene | open_session | escalate",
      "targets": ["resident_1", "app", "speaker"] | null,
      "message_template": "...",
      "evidence_required": true | false
    }
  ],

  "confidence_required": 0.0–1.0,
  "deeper_assessment_if_low": true | false,

  "lifecycle": {
    "created_by": "user | agent | system",
    "created_at": "2026-05-23T09:14:00Z",
    "hit_count": 42,
    "last_fired": "2026-05-22T18:33:00Z",
    "dismiss_count": 3,
    "dismiss_count_24h": 0,
    "suppress_until": null | timestamp,
    "edit_count": 2
  }
}
```

**Scope hierarchy (from most to least specific):**

- **zone** — precise spatial region within an area (e.g., "front-door entrance mat")
- **camera** — specific camera/viewpoint (e.g., "doorbell camera")
- **area** — logical space (e.g., "front_door", "backyard")
- **journey** — subject-scoped rule (fires if this actor passes through any camera)
- **composite** — multiple conditions across cameras/areas
- **global** — fires in any area, any time, any subject (lowest precedence)

---

## Authoring sources

### User natural language

User says: _"Alert me if the dog is alone in the front yard"_

Pipeline:

1. LLM normalizes to structured rule shape
2. Extract scope (front_door area), conditions (dog, alone), severity (alert)
3. Store with `created_by: user`
4. At creation time: **conflict resolution** (see below) — surface any hard conflicts

### Agent-proposed from dismissal patterns

When the same rule fires repeatedly and the user dismisses it within 60 seconds (N times across 24h), propose a suppression rule:

```json
{
  "text": "Suppress: dog in front yard 10am–6pm (user dismisses repeatedly)",
  "scope": "area",
  "scope_ref": "front_yard",
  "temporal": { "active_hours": "10:00–18:00" },
  "actions": [{ "type": "suppress", "target_rule_id": "..." }]
}
```

### Default rule pack

System-provided rules for high-confidence safety scenarios:

- Tier-1 safety alerts (smoke, CO, flood) — always fire, highest severity
- Package delivery confirmation
- Known guest arrival confirmation
- Pool person-detected → continuous monitoring alert
- Repeated unanswered knock alert

---

## Hybrid retrieval

```
Input: event + enrichment + world state

1. SQL filter (fast):
   - scope matches event location (area_id, camera_id, zone_id)
   - temporal conditions satisfied (hours, days, exclusions)
   - subject_type matches detection (person | pet | vehicle)
   - suppress_until not active
   - dismiss_count_24h < threshold (suppress noisy rules)

2. ANN ranking (embeddings):
   - embed event context (subject, location, actions, detections)
   - cosine-similarity rank remaining rules
   - return top-K per event

3. Budget enforcement:
   - max 5 rules evaluated per event (latency)
   - critical scope (zone, camera, area) prioritized
   - global rules only if budget remaining

Output: ordered list of firing rules (tied to context assembly, parallel stage)
```

**Cost note:** Retrieval adds 50–100ms to context assembly. Store must support:

- indexed temporal ranges (hours, days, TTL)
- scope-based partitioning
- fast vector similarity (HNSW or similar)
- recent-access boosting (rules that fire frequently ranked higher)

---

## Rule lifecycle & decay

### Suppression and dismissal

**Suppress (`suppress_until` timestamp):**

- User explicitly silences a rule until a time
- Set when user clicks "don't alert me about this for N hours"
- Checked at retrieval time (SQL filter phase)

**Dismiss counter:**

- User dismisses an alert → `dismiss_count++`, `dismiss_count_24h++`
- At 24h boundary, reset `dismiss_count_24h`
- If `dismiss_count_24h >= 3`: automatically suppress for 4 hours or surface suppression rule proposal

**Hit count & decay:**

- Frequently firing rules boosted in ANN retrieval (relevance ranking)
- Stale rules (last_fired > 30 days ago) deprioritized in retrieval
- Rules with TTL: auto-delete at expiry

### Editability after firing

After an alert fires, show the user:

- Full rule text
- Why it fired (which conditions matched)
- Suggestions: suppress, edit, delete, keep

Editing creates a new rule version; old version marked `superseded_by`. No breaking changes mid-session.

---

## Rule conflict resolution

**Philosophy:** Resolve conflicts at rule _creation_ time, not at evaluation time. Most conflicts auto-resolve through scope specificity. Only genuinely irreconcilable conflicts surface for user decision.

### Conflict detection (at creation)

When a new rule is authored:

1. Retrieve all existing rules with overlapping scope/conditions
2. Check for directly contradictory actions:
   - Rule A: "alert if person detected in zone"
   - Rule B: "suppress if person detected in zone"
   - → Hard conflict if both would fire on same event

3. Scope compatibility check:
   - Global rule + area-specific rule → **no conflict** (area-specific wins)
   - Area-specific rule + zone-specific rule → **no conflict** (zone-specific wins)
   - Same scope + contradictory intents → **hard conflict**

### Conflict resolution algorithm

When multiple rules fire on the same event:

```
1. Collect all firing rules from retrieval

2. Apply SituationalContext modifiers:
   - SituationalContext can suppress or boost rules (e.g., "guests expected" boosts guest-arrival rule)

3. Scope resolution (specificity wins):
   zone > camera > area > journey > composite > global

   Example:
     Global rule: "notify if unknown person detected"
     Area rule (front_door): "notify if unknown person detected"
     Zone rule (entry_mat): "suppress notification for 5 min after delivery"

     → Zone rule applies (most specific), other rules de-prioritized

4. Severity resolution (highest wins):
   - Collect severity from all firing rules after scope resolution
   - Take maximum (if any rule says "alert" and another says "warning" → "alert")
   - Higher severity actions always execute

5. Suppression rules (equal/higher specificity override):
   - Suppression rules must be at same scope or more specific
   - Global suppression cannot override area-specific alert
   - Same-scope suppression blocks the action

6. Notify targets (union of all rules):
   - Rules are per-resident (different residents may have different rules)
   - If rule A targets resident_1 and rule B targets resident_2 → both notified
   - No de-duplication across rules

7. Device actions (all fire / additive):
   - All device actions from all firing rules execute
   - "turn on landscape lights" + "unlock door" → both fire
   - No conflict unless physically contradictory (lock + unlock at same door)

8. Contradictions (hard conflicts):
   - Lock *and* unlock same door → surface to user: "Rules X and Y both fire on this event but have contradictory intents. Which takes precedence?"
   - User picks one; system auto-creates suppression rule for loser at matching scope
   - Conflict logged for audit + rule authoring suggestions

Resolution output: *merged action* (single unified action across all rules)
```

### Example: pool party scenario

User creates three rules for Saturday's BBQ:

```
Rule A (global):
  "Alert if person detected in backyard"
  severity: warning
  action: notify

Rule B (area: backyard):
  "Don't alert on person in backyard 4pm–10pm Saturday"
  severity: suppress
  scope: area (backyard)
  temporal: Sat 4pm–10pm

Rule C (journey):
  "If person arrives at front door, notify resident_1"
  severity: alert
  action: notify resident_1
```

On Saturday, 5pm: person detected in backyard

1. Retrieval returns A, B, C
2. SituationalContext "BBQ party Saturday" boosts B, C; deprioritizes A
3. Scope: B is area-specific (backyard), A is global → B wins
4. Severity: B is suppress, C is alert → union is alert (C's action still fires)
5. Suppression: B blocks backyard alerts for this window
6. Notify targets: C targets resident_1 → notify resident_1
7. Device actions: none in this example
8. Output: alert fired (from C), backyard alert suppressed (from B)

No hard conflict — A and B are compatible (specific scope overrides global).

### Editing rules post-alert

After an alert fires with multiple rules, show the user a card:

```
Alert fired: Person at front door
Triggered by: Rules "Guest arrival" + "Motion alert front yard"
Suggestion: Edit "Motion alert front yard" to suppress 10am–6pm weekdays?
```

User can edit either rule inline or create a new suppression rule.

---

## Editability surface

Rules are editable through:

1. **In-app UI:** Rule cards with inline edit for text, temporal windows, severity
2. **Natural language:** User says "don't alert me about the dog in the front yard before 8am" → LLM parses, updates temporal conditions
3. **Alert explanations:** When alert fires, offer "Edit this rule" + pre-filled suggestions
4. **Conflict resolution UI:** When hard conflict detected, prompt user to resolve

Edit lifecycle:

- Changes create `rule_version: 2` (immutable history)
- Previous version marked `superseded_by: new_rule_id`
- Retroactive rules: user can opt to re-evaluate past 24h events against new rule
- Audit trail: every edit logged with user + timestamp
