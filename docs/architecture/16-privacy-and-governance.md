# 16 — Privacy & Data Governance

**Purpose:** Data classification, what can leave the home, retention rules, and auditability. Privacy is a data-plane concern, not just policy.
**Status:** drafting

---

## Core principle: privacy by architecture, not by policy alone

Privacy is enforced at the data-plane level, not just documented. Every byte of data carries metadata about where it can go and when it must be deleted.

```
Data ingress → Tag with privacy tier → Routed according to tier rules
                                    ↓
                          Can go to local storage
                          Can go to local GPU?
                          Can go to cloud VLM?
                          Can be cached?
                          Retention window?
```

This prevents accidents where data is "not supposed to" go to cloud but ends up there anyway.

---

## Data classes

### Class A: Highest sensitivity (resident identity biometrics)

```
Content:
  - Face frames of residents (full resolution, > 30 pixels)
  - Resident voice recordings (audio from home speakers)
  - Resident face embeddings (ArcFace vectors)
  - Gait/body re-ID embeddings for residents
  
Sensitivity rationale:
  - Resident faces = identity + liveness proof
  - Can be used for unlocking doors, authentication
  - Can be misused for impersonation
  
Default handling:
  - Storage: local only (vector DB + object store on home server)
  - Cloud: NEVER (not even optional; not in product)
  - Retention: indefinite (linked to household; user can delete)
  - Sharing: never externally; internal SentiHome system only
  
Exceptions:
  - Emergency override if resident explicitly opts to backup to personal cloud (encrypted, user-controlled)
  - Not applicable to non-household backups (e.g., Dropbox)
```

### Class B: High sensitivity (household interior, visitors)

```
Content:
  - Video frames from interior rooms (bedrooms, bathrooms, common areas)
  - Visit patterns of service workers (when they come, where they go)
  - Visitor face embeddings (not household members)
  - Interior activity (eating, watching TV, sleeping patterns)
  
Sensitivity rationale:
  - Interior of home is private by default (legal privacy expectation)
  - Visitor patterns reveal household routines, security posture
  - Interior activity can be misused for stalking, theft patterns
  
Default handling:
  - Storage: local only
  - Cloud: NO (except episodic summaries, stripped of identity, below)
  - Retention: 14 days rolling (configurable 7–30 days)
  - Sharing: never externally; opt-in encrypted backup only
  
Exceptions:
  - Episodic memory SUMMARY: a 2–3 sentence description of activity,
    stored locally + optionally backed up to user's cloud
    Example: "Doorbell visitor on 5/23 at 3pm, 10 min dwell, face match 75%"
    (no frames, no identifying info beyond face match confidence)
```

### Class C: Medium sensitivity (exterior, visitors, vehicles)

```
Content:
  - Frames from exterior cameras (porch, driveway, street)
  - Visitor face crops (unidentified individuals)
  - Vehicle plates (when readable)
  - Delivery driver appearances (low-res, not household)
  
Sensitivity rationale:
  - Exterior is semi-public (people may appear in street view anyway)
  - Visitor identification has weaker privacy expectation than residents
  - Vehicle plates = public info (visible to anyone on street)
  
Default handling:
  - Storage: local
  - Cloud: YES, optional, user-configurable
    (can send for VLM if local GPU saturated, with user permission)
  - Retention: 30 days local; configurable cloud archive (7–90 days)
  - Sharing: scrubbed for unknowns (see Scrubbing pipeline below)
  
Processing pipeline:
  - Faces of unknowns: auto-blurred or cropped before cloud
  - Plates: numeric hashes instead of raw text
  - Scene description: text summary without identifying frames
```

### Class D: Low sensitivity (detector-derived, aggregated)

```
Content:
  - Detection JSON: object classes, bounding boxes, no raw image
    { "person": {"confidence": 0.89, "bbox": [100, 200, 300, 400]} }
  - Aggregated statistics: "N people detected in backyard per hour"
  - Scene classification: "outdoor, daylight, clear sky"
  - Anomaly flags: "person motionless > 60s", "rapid movement detected"
  
Sensitivity rationale:
  - No pixels = harder to re-identify
  - No raw frames = no incidental private details visible
  - Aggregated stats = pattern, not individual moments
  
Default handling:
  - Storage: local
  - Cloud: YES, frequently (for analysis, optional user reports)
  - Retention: 90 days local (compress after 30); indefinite cloud (user can purge)
  - Sharing: can be shared with researchers (anonymized) if user opts in
  
Use case: "Show me detection patterns over 30 days — when are most visitors arriving?"
```

---

## Privacy tiers

Every message/file in the system carries a `privacy_tier` tag:

### `local_only`

```
Constraints:
  ✗ Cannot be cached in cloud
  ✗ Cannot be processed by cloud VLM
  ✗ Cannot be sent to external APIs
  ✓ Can be processed locally
  ✓ Can be stored locally
  ✓ Can be backed up to user's personal cloud (encrypted by user)

Example: Class A (resident faces), Class B (interior frames)
```

### `cloud_eligible`

```
Constraints:
  ✓ Can be sent to cloud VLM (on-demand, user control)
  ✓ Can be cached in cloud temporarily (< 1 hour)
  ✓ Can be sent to analysis APIs
  ✓ Can be stored in cloud backup (encrypted)
  ✗ Cannot be shared with third parties without consent

Example: Class C (exterior, visitor faces)
  
Usage: "GPU is full locally, can I use cloud VLM for this?"
  → Yes, if privacy_tier: cloud_eligible
```

### `cloud_any`

```
Constraints:
  ✓ Can go anywhere (cloud VLM, caching, analysis, backups)
  ✓ Can be anonymized + shared (research, etc.)
  ✗ Still respects retention (delete after N days)
  ✗ Still respects audit (all processing logged)

Example: Class D (detection JSON, aggregates)
```

---

## Tagging at ingress, enforcement at router

### Tagging

Every event gets tagged at source:

```python
# Fast detector output
enrichment = {
  "event_id": "...",
  "privacy_tier": "cloud_eligible",  ← from frame source
  "scrub_before_cloud": true,         ← face crops blur unknown faces
  "retention_days": 30,
  "objects": [ ... ]
}

# Interior camera frame
frame = {
  "camera_id": "bedroom_cam",
  "privacy_tier": "local_only",       ← interior = local only
  "source_area": "master_bedroom",
  "can_cloud_backup": false,
  "retention_days": 14
}

# Exterior camera frame
frame = {
  "camera_id": "doorbell_main",
  "privacy_tier": "cloud_eligible",   ← exterior = ok for cloud
  "source_area": "front_door",
  "can_cloud_backup": true,
  "retention_days": 30
}
```

### Enforcement at router

Model router (§04) checks privacy_tier before routing:

```
VLM request received:
  privacy_tier: "local_only"
  preferred_backend: "cloud" (user preferred)
  
Router decision:
  ✗ Cloud backend would require local_only data
  → Deny cloud backend selection
  → Try local backends only
  → If all local saturated, escalate to user:
      "Cloud VLM unavailable for this camera (interior).
       Local GPU busy. Escalate tier? [Yes] [No, wait]"
  → If user escalates: ask for confirmation each time
    (not automatic; user aware)
```

---

## Scrubbing pipeline (optional pre-cloud)

Before sending exterior frames to cloud, optionally scrub sensitive details:

```
Input: frame (person at door, possibly known or unknown)

Scrubbing stages:
  1. Face detection: is there a face?
  2. Identity check: do we recognize this face?
     If YES (resident or known visitor): don't send raw frame, 
             send only detection JSON + identity
     If NO (unknown): continue to next stage
     
  3. Face anonymization:
     - Blur unrecognized face (preserve body, context)
     - Or: crop to body only (drop face)
     - Or: send face as low-res descriptor, not pixels
     
  4. Background removal:
     - Interior windows visible? Blur them
     - License plates of other vehicles? Hash instead of display
     
  5. Output: scrubbed frame (safe to send) + metadata
     "Face blurred (unknown, confidence 0.72)" ← sent to cloud
     Raw frame never sent.
```

**User control:** "Scrubbing level" slider:
- Off (never cloud)
- Aggressive (blur everything unknowns, remove context)
- Moderate (blur faces only)
- Minimal (send scene JSON only, no pixels)

---

## Retention by data class

| Data class | Default | Configurable? | Cloud | Backup |
|-----------|---------|---------------|-------|--------|
| **Resident faces** | Indefinite | No | Never | User opt-in only |
| **Resident voice** | Indefinite | No | Never | User opt-in only |
| **Interior frames** | 14 days | 7–30 days | Never | Never (privacy) |
| **Interior episodic** | 1 year | 3mo–2yr | Never | User opt-in, encrypted |
| **Exterior frames** | 30 days | 7–30 days | 7–90 days | User opt-in |
| **Exterior episodic** | 1 year | 6mo–2yr | 30–90 days | User opt-in |
| **Visitor embeddings** | 30 days | 7–30 days | 7 days | User opt-in |
| **Unknown faces** | 30 days | 7–30 days | Never | User opt-in (encrypted) |
| **Detection JSON** | 90 days | 30–180 days | 180 days | Indefinite (opt-out) |
| **Rules, intents** | Indefinite | User controlled | Never | Yes (local) |

**Auto-cleanup processes:**

```
Every midnight:
  1. Check all data for expiry date
  2. Soft-delete expired data (mark, not immediate erase)
  3. Secure-erase after 7 days in soft-delete state
     (in case user wants to recover)
  
Every week:
  - Cloud data: sync retention policy
  - Embeddings: compress old vectors (cheaper storage)
  
Every 30 days:
  - Archived episodes: summarize (keep summary, discard raw)
  - Cold storage: archive old events to cheaper tier
```

---

## Resident vs non-resident embeddings

### Resident embeddings (household members)

```
Stored: indefinite (until household member removed from gallery)
Location: local vector DB only
Cloud: never
Backup: user opt-in only
Deletion: only by explicit user action "forget [resident]"

Lifecycle:
  1. First enrollment (user adds "Sarah")
  2. Regular detections (face updated as Sarah ages / changes style)
  3. User deletes "Sarah" (all embeddings + history purged)
    → 7-day soft-delete grace period (user can restore)
    → Secure erase after grace period
```

### Non-resident embeddings (visitors, unknowns)

```
Stored: 30 days (default, configurable 7–30)
Location: local vector DB
Cloud: never (stays local)
Backup: never (privacy)
Deletion: automatic (30-day TTL)

Exceptions:
  - User labels unknown face ("That's Bob, friend") → promote to known actor (stay indefinite)
  - Unknown face matches multiple times over weeks → auto-promote to known visitor
    (system learns recurring pattern, asks user for name)
  - Explicitly deleted by user: immediate + secure erase
```

### Right-to-forget flow

User can request deletion of all data about a person:

```
User request: "Forget all data about [visitor name / face]"

System checks:
  1. Is this a household member? (requires special confirmation)
  2. How many places is this person in the system?
     - Gallery entries
     - Episodic records
     - Visit ledgers
     - Raw frame archives
     - Analytics
     
Action:
  1. Mark all entries for deletion
  2. Search episodic for sessions involving this person → delete or anonymize
  3. Soft-delete all data (7-day grace period)
  4. Show user: "Marked for deletion: 47 records, 1.3GB storage"
     [Cancel] [Confirm deletion]
  5. On confirm: immediate + secure erase

Log: "Deletion request for [name] by [resident], 47 records purged"
```

---

## Cloud egress audit log

Every byte that leaves the home is logged.

```
Audit record:
  timestamp: "2026-05-23T14:33:22Z"
  data_type: "scene_json | frame_crops | episodic_summary | detection | metadata"
  privacy_tier: "cloud_eligible"
  size_bytes: 45320
  destination: "cloud_vlm_api"
  scrubbed: true | false
  scrub_details: "face blurred (unknown, confidence 0.72)"
  initiated_by: "system (gpu_saturation)" | "user (backup)" | "scheduled"
  user_who_approved: "resident_1"
  data_retention_days: 30
  
Query examples:
  "Show me all data sent to cloud in the last 7 days"
  "How much raw interior data has left the home? (should be zero)"
  "What is the current cloud storage size?"
  "When will archived episodic data be deleted?"
```

**User interface:** "Data leaving home" section in app

```
Cloud usage (last 30 days):
  ✓ 1.2GB exterior frames (VLM processing)
  ✓ 340MB detection JSON (analysis + backups)
  ✗ 0B resident faces
  ✗ 0B interior frames
  
Largest transfers:
  - 2026-05-22 18:30: 45MB exterior frames (GPU saturation, 3 events)
  - 2026-05-21 21:00: 22MB detection aggregate (nightly backup)
  
Retention: deletion scheduled 2026-06-22

[View audit log] [Configure cloud usage]
```

---

## Multi-resident consent & conflict

When multiple people live in a home, privacy expectations may differ.

### Consent model

```
Residents:
  - resident_1: "Sarah" (owner)
    privacy_level: "high" (don't send my face to cloud)
    
  - resident_2: "Bob" (guest / roommate)
    privacy_level: "medium" (ok to backup interior)
    
  - resident_3: "Alice" (child)
    privacy_level: "very_high" (parental override; no cloud data)
```

### Conflict resolution

If one resident wants to cloud-backup and another doesn't:

```
Scenario: Sarah wants weekly backup of all data (privacy_level: medium)
          Bob wants no cloud data (privacy_level: very_high)
          
Policy: "Most restrictive wins"
  → No cloud backup of data where Bob appears
  → Cloud backup allowed only for Sarah-only frames
  
Rule:
  - Interior frames: only if Sarah alone (no Bob) → backup ok
  - Interior frames: if Bob visible → never backup (block Bob's privacy)
  - Exterior frames: ok to backup (everyone consents to outside)
  
User sees: "Cloud backup blocked for [N] interior frames (Bob present)"
           [Override] [Accept] [Change settings]
           
If Bob leaves for weekend:
  - System allows backup of Bob's absence period
  - Resumes restriction when Bob returns home (occupancy change detected)
```

### Parental override (for minors)

```
If household includes minors:
  - Parent/guardian can set privacy_level: "very_high" for child
  - All data involving child (face, voice, location in home) = local_only
  - Overrides other residents' cloud backup preferences
  - Special case: child's consent at age 18 (local law dependent)
```

### Visitor consent

When a visitor is detected and identified:

```
Rule: If visitor is known + face in system + cloud backup enabled:
  
  Check: Does visitor have privacy exception?
    If YES: "I do not consent to cloud backups" → block backup
    If NO: proceed
    
  First time: System doesn't know visitor's preference
    → Surface in morning briefing: "New visitor detected. 
       Did they consent to being backed up? [Yes] [No]"
    → Store response for future visits
    
  Persistent guest: After 3 visits, ask "Add to known visitors?"
    → If yes: add to actor gallery (ask for name + privacy preference)
    → If no: continue labeling as "unknown visitor"
```

---

## Compliance & legal

**SentiHome is designed for GDPR, CCPA, and similar privacy frameworks:**

- Data minimization: collect only what's needed (§08 quality gates)
- Purpose limitation: data used only for home security (no re-sale, no ads)
- Consent: users control cloud backup + data sharing
- Retention: automatic deletion per policy
- Right to access: audit log + data export available
- Right to delete: right-to-forget flow (see above)
- Data portability: export sessions + episodic in standard formats

**Operator responsibility:**

- Privacy by default (local-only unless explicitly enabled)
- Transparency (audit log, health dashboard)
- Governance (retention policies, multi-resident consent)
- Oversight (regular audits of cloud usage, deletion verification)
