# 12 — Recognition & Identity

**Purpose:** How people, vehicles, and recurring unknowns are identified across frames, sessions, and days — with uncertainty surfaced honestly.
**Status:** drafting

---

## Face recognition pipeline

### Tier 1: Face detection and geometric quality

```
Raw frame
    │
    ▼
Face detector (SCRFD / RetinaFace)
    │ (all faces, per frame)
    ▼
Quality gates:
  - Size ≥ 20×20 pixels (readable at typical alert size)
  - Yaw/pitch ≤ ±45° (frontal enough for recognition)
  - Blur < 0.3 (not motion-blurred)
  - Occlusion < 0.2 (not heavily obscured)
  
Quality outcomes:
  - pass         → continue to embedding
  - fail         → flag `face_present_unresolved`, don't guess identity
  - borderline   → compute embedding but tag `confidence_tentative`
```

Quality is stricter for unknowns (high stakes: false ID is worse than no ID) than for resident confirmations (low stakes).

### Tier 2: Face embedding (model-agnostic)

```
Frontalized face image (alignment + crop)
    │
    ▼
Embedding model (ArcFace / AdaFace)
    │
    ▼
Embedding vector (512-d)
    + model_version (embeddings versioned with model)
    + confidence_tier (based on Tier 1 quality)
    + capture_ts
    + source_frame_ref
```

Store embeddings in vector DB (HNSW index for ANN search). Keep capture context: the embedding's reliability depends on where it came from — a security camera vs. a blurry side-angle still matter.

### Tier 3: Gallery matching

```
Gallery entry: "Sarah (resident)"
  embeddings: [
    { vector, model_v: "arcface_r100", confidence_tier: "confirmed", ts },
    ...
  ]
  
Incoming embedding + context:
  vector, confidence_tier: "high|tentative|unresolved"
  
Match algorithm:
  1. Filter gallery to same model_version
  2. Cosine-similarity search: top-K candidates
  3. Apply thresholds per confidence tier:
  
  Incoming: "high" (good geometry)
    → threshold 0.60 for confirmed match
    → threshold 0.55 for tentative claim ("Sarah? (0.72)")
    → below 0.55 → unknown, embed only
    
  Incoming: "tentative" (marginal geometry)
    → threshold 0.70 for confirmed match
    → do not make tentative claims from marginal inbound quality
    
  Incoming: "unresolved" (failed quality gates)
    → embed only, no identity claim; flag for manual review
  
Output: identity_claim: "sarah" | null
        confidence: 0.0–1.0
        similarity: 0.72
```

**Illustrative thresholds** (site-calibrated per §14):

| Scenario | Threshold | Rationale |
|----------|-----------|-----------|
| Resident face detection | 0.60 | High stakes: false positive (wrongly alert for resident = annoying) mild. False negative (miss a resident) acceptable cost. |
| Unknown person, day | 0.65 | Medium stakes: facial recognition on a stranger needs higher confidence. Daylight, full face. |
| Unknown person, night/profile | 0.72 | Low confidence inbound; need very high gallery match to claim identity. |
| Service worker (expected) | 0.62 | Access profile + temporal context lowers threshold slightly. |

**No guessing.** If match score is below threshold, output `face_present_unresolved` + embedding. The VLM and reasoner get the context and can decide if it matters.

### Tier 4: Multi-frame aggregation

Within a single clip (e.g., 8 frames), the same face may appear in several. Aggregate embeddings from the best N frames:

```
Best N frames by quality score: {frame_ids}
Compute mean embedding = centroid of N vectors
Final confidence = mean(individual confidences)
Use centroid for gallery matching (more stable than any single frame)
```

Multi-frame reduces noise and increases reliability for low-res or angled captures.

---

## Body re-ID (in-session only)

Body re-ID (re-identification) uses appearance features (clothing, gait, height, build) to match a subject across frames *within a single session or short time window*. Unlike face embedding, body embeddings drift quickly (change clothes, lighting changes) so cross-day use is unreliable.

### When re-ID is used

- **In-session correlation:** does frame N in camera 2 match the person from frame M in camera 1, same session?
- **Temporal continuity check:** is this the same subject 10s later on a different camera?
- **Exclusion:** "that person went out the back door 2 minutes ago, so front-door detection now can't be the same person"

### Pipeline

```
Bounding box + crop
    │
    ▼
Pose estimation (OpenPose / MediaPipe)
    ├── keypoints for height-from-skeleton
    ├── gait classification
    └── posture signal
    
Body appearance features
    ├── Color histogram (clothing)
    ├── Texture (patterns)
    └── Shape features (build)
    
Re-ID embedding model (OSNet or similar)
    │
    ▼
Re-ID vector (256–512-d)
    + model_version
    + confidence (face-less re-ID is noisier)
    + source metadata
```

### Correlation decision tree

```
New segment arrives on different camera within 30s window
    │
    ├─ Re-ID similarity ≥ 0.85 AND
    │  Spatial plausibility (adjacency check)? AND
    │  Temporal window OK (could subject reach camera 2 in Δt)?
    │
    ├─ YES → append to session, high confidence
    │
    └─ NO
        ├─ Re-ID similarity 0.70–0.85 + spatial OK?
        │  → append with `correlation_confidence: tentative`
        │
        └─ Re-ID similarity < 0.70 OR spatial implausible?
           → new session (likely different subject)
```

**Height verification:** if skeleton-estimated heights differ by > 10cm, re-ID match is rejected even if embeddings match (prevents matching a tall person to a short person in similar clothing).

---

## Cross-day composite identity

Cross-day identity (same person on day 2 vs. day 1) is inherently uncertain — clothing changes, pose differs, time gaps are large. Rather than trying to achieve high certainty, the system surfaces *all available evidence* and lets the reasoner and user decide.

### Identity resolution record

Every memory object referencing a subject carries this:

```
IdentityResolution:
  resolved_id: "carlos_pool_service" | null
  
  candidate_ids: [
    {
      actor_id: "carlos_pool_service",
      confidence: 0.78,
      evidence: ["face_match_0.75", "plate_match_0.95", "behavioral_0.68"]
    },
    {
      actor_id: "unknown_regular_visitor",
      confidence: 0.15,
      evidence: []
    }
  ],
  
  evidence_sources: [
    {
      source: "face",
      score: 0.75,
      notes: "same model version, good quality inbound, 0.75 similarity to Carlos gallery"
    },
    {
      source: "plate",
      score: 0.95,
      notes: "exact match: XYZ789"
    },
    {
      source: "behavioral",
      score: 0.68,
      notes: "arrival time within observed window, area matches usual access"
    },
    {
      source: "height",
      score: 0.82,
      notes: "skeleton estimated 177cm (vs Carlos baseline 175cm ±3cm)"
    },
    {
      source: "gait",
      score: 0.61,
      notes: "gait classification inconclusive; lighting/angle different"
    }
  ],
  
  resolution_method: "composite",
  resolution_confidence: 0.78,
  asserted_by: "model",
  manually_confirmed: false
```

**Key principle:** each evidence source is independent; confidence is the maximum across all sources (if any single source is very confident, use that).

### Multi-modal matching

Composite identity uses all available signals, not just face:

1. **Face** — embedding similarity (if geometry passed quality gates)
2. **Vehicle plate** — exact OCR match (if readable)
3. **Behavioral pattern** — observed arrival window + dwell time + areas visited
4. **Body height** — skeleton-estimated cm + tolerance
5. **Clothing color** (weak) — if other signals inconclusive
6. **Gait** (weak, angle-dependent) — for unusual motion patterns (limp, stiffness)

**Scoring example:**

```
Session today: unknown person in driveway, unknown plate
Expected: Carlos (pool service)
  Face match: 0.75 (good but not perfect)
  Plate: no match (not Carlos's car)
  Behavior: arrival 10:15am (Carlos usually 10–11am, so consistent)
  Height: 174cm (Carlos ~175cm, within tolerance)
  Gait: normal walking (Carlos's baseline also normal)
  
Composite: 0.78 confidence → "likely Carlos, but unusual vehicle (might borrowed car)"

Output: identity_claim: "carlos"; confidence: 0.78; notes: "face + behavior match; different vehicle"
```

---

## UX of uncertainty

The system does not pretend to certainty it doesn't have. Uncertainty is surfaced honestly to the user.

### Alert explanations

```
Alert: Person at front door

Triggered by: Rule "guest arrival"

Identity: Likely Sarah (0.78 confidence)
  Evidence:
    • Face recognition: 0.75 match to Sarah's gallery
    • Behavioral: Matches typical arrival time on Saturday
    • Note: Side profile from this angle, lighting good
    • Alternative: Could be visitor resembling Sarah (0.12 confidence)

Seen before?: 3 times in last month (all Saturday afternoons)
```

### Dismissal with context

User can dismiss and provide feedback:

- ✓ "That was Sarah"
- ✗ "Not Sarah, it was a stranger"
- ? "Not sure, but don't alert me about this"
- ! "That's concerning — investigate"

Feedback is stored as `asserted_by: user` in the IdentityResolution, and used to retrain thresholds.

### New-face bootstrap prompt

When a new person is detected consistently and the system is uncertain:

```
"I've seen a new person at your front door 2 times in the last week.
 I'm not confident about their identity yet.
 
 Options:
 • They're [dropdown: resident, regular visitor, service worker, neighbor, other]
 • I can remember their face if you name them
 • [show montage of 2–3 best face crops]"
```

One confirmation trains gallery entry with label. Subsequent detections of that face are confirmed.

---

## Gallery management

### Gallery entry lifecycle

```
Detection occurs (face, plate, pet face, or vehicle)
    │
    ├─ Auto-gallery-entry created with:
    │  { embedding, capture context, confidence_tier }
    │  status: candidate
    │
    ├─ (User) Labels the entry
    │  "That's Sarah" → status: confirmed
    │  "Not Sarah" → archived (negative example)
    │
    └─ (System) Periodically sweeps candidates
       If not labeled in 30 days → archive
       If labeled → create KnownActor or link to existing
```

### Enrollment flows

**Manual enrollment** (user adds new resident, service worker, etc.):

```
User: "Add a new person to the gallery"
    ↓
Dialog: Name, relationship, areas allowed, expected days/times
    ↓
System: "Capture some photos — best quality helps"
    ↓
User provides reference images (phones, previous clips)
    ↓
System computes embeddings + stores in KnownActor gallery
    ↓
"Ready to recognize [Name] from now on"
```

**Auto-enrollment from frequent unknowns**:

```
System detects same unknown face 5+ times in a week
    ↓
"You have a recurring visitor I don't know yet. Name them?"
    ↓
User labels or ignores
    ↓
If labeled → promote to KnownActor
If ignored 3 more times → suppression rule offered
```

### Human-in-the-loop labeling

Every alert can trigger a labeling opportunity:

```
Alert: "Unknown person at front door"
[Show best face crop + annotation]

"Who is this?"
  • [Dropdown of known actors + "add new"]
  • "I don't know, don't ask again for 2 hours"
  • [X dismiss]

[User selects "Sarah"]

System:
  • Creates GalleryEntry with embedding
  • Links to Sarah's KnownActor
  • Updates IdentityResolution on this session
  • Re-scores past similar sessions with new info
```

---

## Drift & re-enrollment

Face embeddings can drift over time due to aging, weight changes, facial hair, systematic lighting. The system detects drift and prompts re-enrollment.

### Drift detection

```
KnownActor: Sarah
  gallery_embeddings: [created 2024-01, created 2024-06, created 2025-03]
  behavioral_profile: { observed_arrival_window, observed_areas, ... }
  
Comparison: new detection vs. existing gallery
  Similarity to oldest embedding: 0.68
  Similarity to newest embedding: 0.72
  
Trend: decreasing similarity over time (drift detected)
```

Thresholds:
- If new match is valid (≥ 0.60 confidence) but drift detected → re-enrollment prompt after N hits
- If new match drops below threshold but user confirms identity → add as new enrollment (new time period)

### Re-enrollment prompt

```
"I've detected Sarah several times, but recent detections are
 less confident than before.
 
 This can happen with lighting changes, styling changes, or aging.
 
 Would you like me to update my reference images for Sarah?
 [Yes] [No, keep old] [Skip this month]"
```

If yes, system includes recent best-quality detections in gallery update.

---

## Pet recognition

Pets require separate galleries and matching logic. Pets can't be reliably identified by face across different angles and lighting (unlike humans with stable facial geometry). Re-ID + behavioral patterns are primary.

### Pet gallery

```
PetActor:
  id: "max_golden_retriever"
  species: "dog"
  breed: "Golden Retriever"
  
  gallery:
    face_embeddings: [{ vector, model_v, source_frame_ref }]
    coat_pattern: "image descriptor"
    height_estimate_cm: 65
    
  home_areas: [backyard, interior]
  alert_if_detected_in: [front_yard, street]
    reason: "Max should not be unsupervised outside the back fence"
    
  last_known_location: {
    area: backyard,
    confirmed_at: "2026-05-23T10:00Z"
  }
```

### Pet detection logic

```
Detection: Dog in frame
    │
    ├─ Face recognition
    │  Similarity to Max gallery: 0.84
    │  Confidence tier: "tentative" (pet faces have more variation than human)
    │
    ├─ Body features
    │  Color: golden (matches breed)
    │  Size: ~65cm (matches estimated height)
    │  Gait: normal (no limp/distress)
    │
    └─ Location + time context
       Frame from: front_yard
       Max should be: backyard
       Gate sensor: no recent open
       
       → Likely escaped, high alert (S16)
```

---

## Privacy of non-household embeddings

Full details in §16 (Privacy & Governance). Summary:

- **Household members:** indefinite retention, full embeddings stored locally
- **Known visitors / service workers:** indefinite while relationship active; embeddings stored locally; can be user-deleted
- **Unknown faces:** 30-day rolling retention; embeddings deleted after window (reduced re-ID capability but not complete privacy loss); promoted to indefinite if user labels them
- **Cross-linking:** Unknown face embeddings are NOT linked to external databases; matches are gallery-only
- **Cloud:** Face crops and scene descriptions can go to cloud if needed for VLM (policy-gated); full resolution embeddings never leave local
