# 08 — Detection Pipeline

**Purpose:** The preprocessing + fast-detector service — motion-gated 24/7 monitoring, frame markup, structured enrichment for the VLM, plus the vigilance/attention mode system that commands sustained high-cadence monitoring for life-safety scenarios.
**Status:** drafting

---

## Preprocessing layer (mode-adaptive)

The detection pipeline runs inside the **preprocessing layer**, which is mode-adaptive per the NVR Adapter Layer (§03.5):

```
Mode        | Where preprocessing runs                  | Latency overhead
────────────┼──────────────────────────────────────────┼─────────────────
Service     | Separate preprocessor-service process    | ~150–370ms
            | (consumes RTSP from any NVR)             |
────────────┼──────────────────────────────────────────┼─────────────────
Built-in    | Inside the NVR (Frigate)                 | ~50–150ms
            | SentiHome consumes pre-enriched results  |
────────────┼──────────────────────────────────────────┼─────────────────
Native      | Plugin inside NVR's process (Agent DVR)  | ~25–100ms
            | Direct frame buffer access (v2+)         |
────────────┼──────────────────────────────────────────┼─────────────────
Direct      | SentiHome internal (no NVR)              | ~25–100ms
            | Effectively native by definition         |
```

The pipeline stages described below are the **same** in every mode. What changes is **where** they run and how frames are passed in.

---

## Motion detection: the 24/7 gating function

Before any GPU-intensive enrichment runs, motion detection gates the pipeline. Without this gate, processing every frame on every camera 24/7 would burn massive compute on empty scenes.

### Why robust motion detection matters

Naive motion detection (pixel difference > threshold) triggers false positives constantly:
- Lighting changes (clouds, dawn/dusk, headlights)
- Wind moving trees, plants, flags
- Rain, snow, insects near camera
- Spider webs vibrating in IR illumination

Every false motion event = wasted enrichment compute + potentially wasted VLM call. Quality of motion detection determines whether the system is cheap and quiet or constantly noisy.

### Approach (v1)

Hybrid algorithm combining multiple signals:

```
Per frame (24/7, per camera):
  1. Background subtraction (MOG2)
     - Adaptive background model
     - Foreground mask
  2. Optical flow analysis
     - Distinguishes real motion from lighting/exposure shifts
  3. Object size filtering
     - Ignore changes < N pixels (filters wind, rain, insects)
  4. Temporal consistency
     - Motion must persist > 200ms to count
  5. On-camera AI corroboration (if available)
     - On-camera "person detected" + our motion signal = high confidence
     - Our motion alone with no on-camera AI = standard confidence
     - Disagreement = trigger anyway, flag for VLM to investigate

Output: motion_event(camera_id, ts, regions, confidence)
```

### Source flexibility

Motion can come from any of three sources, ranked by preference:

1. **Preprocessor's own algorithm** (most consistent across cameras)
2. **NVR's built-in motion detection** (Frigate, Synology, etc.)
3. **On-camera AI motion event** (camera-native, varies by brand)

When multiple sources are available, they corroborate; when one fires, it gates the pipeline. False-negative risk (one source misses, others fire) is mitigated by ORing the signals.

### Tunability

Per-camera motion sensitivity is exposed for tuning (and learned automatically over time per §10.5):

```
camera_config:
  motion_min_object_size_px: 800     # filters wind/rain
  motion_min_duration_ms: 200        # filters glitches
  motion_exclusion_zones: [...]      # ignore swaying tree
  motion_environmental_adjustments:
    rain_mode: lower sensitivity
    night_mode: tighter thresholds
```

---

## Dual role (event-driven)

Once motion gates the pipeline, the detector serves two roles:

1. **Trigger backup:** continuous lightweight RTSP subscription raises synthetic events when cam-native AI misses something (motion detected + objects identified ≠ on-camera AI opinion)
2. **Enrichment service:** every frame destined for VLM gets structured detection output appended before the VLM call

---

## Models

| Model | Purpose |
|-------|---------|
| YOLO / RT-DETR | Object detection — class, bbox, track_id |
| SCRFD / RetinaFace | Face detection |
| ArcFace / AdaFace | Face recognition — gallery match |
| OSNet (et al.) | Body re-ID embedding (in-session only) |
| Pose estimation | Keypoints — intent, gait, height-from-skeleton |
| Appearance attributes | Clothing colors, held object, hood up, bag |
| Plate OCR | Vehicles |
| Pet face recognition | Known pets — escape detection (S16) |
| Drowning / distress classifier | Pose + motion pattern, pool safety (see Attention modes) |
| Stillness classifier | Motionless person detection — any area |

---

## Frame sampling & detector-guided selection

Default: 8 frames per clip. Preferred: detector-guided selection — frames where the subject is most visible, central, and unoccluded, rather than uniform temporal sampling. Annotated and clean reference frames both sent to VLM.

**Three sampling strategies depending on context:**

| Context | Strategy |
|---------|----------|
| One-shot event | Fixed budget (8 frames), detector-guided selection |
| Sequence completion watch | Adaptive interval = elapsed_sequence_time / target_frames; multiple VLM calls across phases (see Sequence completion watch section) |
| AttentionMode (continuous) | Fixed fps commanded to fast detector; VLM called on anomaly flag or on ambient cadence |

The adaptive interval for sequence watches means the sampling naturally spreads out as a sequence runs longer — dense sampling at the start, sparser as time accumulates. This prevents budget waste on long uneventful windows while keeping resolution high during the action-dense early phase.

---

## Track ID persistence

Track IDs persist across all frames of a clip so cross-frame reasoning has stable subject references. Re-ID embeddings are computed per-track, not per-frame, and averaged over the best N frames.

---

## Quality gates

False-positive recognition (labeling a stranger as Sarah) is worse than no label.

- Face too small, oblique, blurred, or occluded → `face_present_unresolved`, not a guess
- Re-ID confidence below threshold → embed only, no identity claim
- Plate partial/unreadable → `plate_present_unresolved`
- Pet recognition below threshold → `animal_present_unresolved`

Tier thresholds (illustrative):
- Face: ≥ 0.55 → confirmed, 0.40–0.55 → tentative (`Sarah? (0.72)`), < 0.40 → unknown
- Plate: full read required for identity claim; partials logged but not matched

---

## Annotation rendering

- Thin outline boxes (2px), semi-transparent fills on faces
- Label format: `#2 Sarah ✓`, `#3 unknown ?`, `#4 Amazon ◷`
- Color by trust tier: green=resident, blue=known-visitor, yellow=unknown-benign, red=unknown-attention
- Confidence shown when below threshold
- Motion arrows on multi-frame montages
- Clean reference frames always sent alongside annotated frames — VLM uses both

---

## Crop-and-zoom

Tight high-res crops of unknown or attention-worthy subjects sent alongside full frames. Improves face and plate read rate for subjects that are small in the full frame.

---

## Throughput & GPU sharing with VLM

Fast detector and VLM compete for GPU. Options:
- Time-slice on a single GPU (detector gets low-latency priority; VLM batches)
- Dedicated detector GPU (lighter card) + VLM GPU
- Detector runs CPU-side for lightweight models (YOLO small) with GPU fallback for face recognition

Attention mode (see below) can temporarily increase detector frame rate for a specific camera, which must be accounted for in the GPU duty-cycle budget.

---

## Attention modes (vigilance)

### Concept

The normal pipeline is event-driven: something happens → process it. An **AttentionMode** is a sustained, heightened monitoring state triggered by presence detection in a high-stakes area. It runs continuously until the area clears, using specialized models at higher frame rates, and its outputs **bypass normal triage queues** entirely.

This is a resource allocation decision as much as a detection one: the fast detector is commanded into a higher-cadence mode for a specific camera while the mode is active.

### Why the VLM is not on the critical path here

For life-safety scenarios (drowning, fall, child alone in pool), the alert timeline is measured in seconds. A drowning can be fatal within 2–3 minutes of submersion. The detection path must be:

```
Pool camera (2–4fps continuous)
        │
        ▼
Specialized classifier   ← fast, runs every frame, no queue
(drowning/distress/stillness)
        │
   anomaly? ──yes──► IMMEDIATE alert → all household + emergency contacts
        │                   │
       no                   └──► VLM enrichment appended after alert fires
        ▼                         ("child, alone, motionless 45s, no adult")
30s ambient VLM check
("still swimming normally")
        │
        ▼
   log + mode stays active
```

The VLM enriches the alert narrative and provides context — it does not gate the alert.

### AttentionMode schema

```
AttentionMode:
  id, label: "pool_occupied"
  
  activation:
    trigger: person detected in pool_area
    conditions: any person (including children)
    
  deactivation:
    no person detected for 5 min
    or explicit user cancel
    
  monitoring:
    camera_fps: 2–4fps continuous  ← vs event-driven normally
    specialized_models:
      - drowning_detection          ← pose + motion pattern
      - stillness_timer             ← motionless in water > Ns
      - distress_pose               ← arms above water, head submerged
    vlm_cadence: every 30s ambient; immediate on anomaly flag
    
  alert:
    anomaly_sla: < 15s from onset
    output_class: urgent_alert
    bypass_queue: true
    targets: all_household + emergency_contacts
    
  context_enrichment:
    vlm_note: "swimming alone" | "children present" | "unsupervised" | "service worker"
```

### Known attention mode scenarios

| Area | Activation trigger | What to watch for | Specialized model | SLA |
|------|-------------------|-------------------|------------------|-----|
| Pool | Any person detected | Drowning, distress, motionless in water | Drowning detector, stillness timer | < 15s |
| Pool | Child detected, no adult co-present | Unsupervised swim session | Age estimation + adult absence | < 30s |
| Any area | Person motionless > N min | Medical emergency, fall | Stillness + pose | < 30s |
| Driveway | Child playing near road | Child proximity to moving vehicle | Vehicle + child proximity | < 10s |
| Stairs / bathroom | Elderly resident detected | Fall | Fall detection + pose | < 20s |

### Interaction with KnownActor and SituationalContext

The attention mode still activates regardless of who the person is — the monitoring decision is separate from the identity/access decision. Context modifies how anomalies are interpreted, not whether monitoring runs:

- Pool man visiting → AttentionMode activates → VLM context says "service worker, normal maintenance" → distress detection still runs at full sensitivity; behavioral anomaly threshold is higher for things like "approaching the gate vs. working at the pool"
- Halloween → kids may wander to pool area → AttentionMode activates → context says trick-or-treat is active but does not suppress pool safety monitoring
- TransientIntent: "kids having a swim party this afternoon" → system pre-activates pool AttentionMode rather than waiting for first detection

### Architecture placement

AttentionMode sits as a resource allocation layer between the event bus and the detection pipeline:

```
[Event bus]
     │
     ├──► [Triage worker]              ← normal event-driven path
     │
     └──► [Attention mode manager]
               │
               ├── tracks active AttentionModes per camera/area
               ├── commands fast detector: "pool_cam → 4fps + drowning model"
               ├── owns the continuous sampling loop for active modes
               └── outputs bypass triage → go direct to urgent queue
```

The fast detector must support being commanded into higher-cadence mode for a specific camera without affecting other cameras' normal event-driven behavior.

---

## Sequence completion watch

A lighter variant of AttentionMode for scenarios where the alert is triggered by an **absent behavior** rather than a detected anomaly. Instead of watching for something that happens, the system watches for something that doesn't happen within a time window.

Pattern:
```
Trigger event detected (e.g. dog squat)
        │
        ▼
Open sequence watch: observe camera for N seconds
        │
   completion action observed?
        │
   yes ──► log only, close watch
        │
   no (subject departs or window expires) ──► notify with clip
```

Differs from AttentionMode:
- Short-lived (60–120s vs. open-ended)
- Lower resource cost — no continuous specialized model, just sustained clip sampling
- Not life-safety — outputs are `notify` or `log`, never `urgent_alert`
- The "completion" definition is VLM-reasoned across multiple calls, not a specialized classifier

### Adaptive frame sampling and multi-call VLM for sequence watches

Frame selection interval is driven by the **observed duration of the sequence**, not a fixed budget. A sequence that has been running for 10 seconds needs different sampling than one that has been running for 90 seconds.

**Cadence phases:**

```
Phase 1 — Trigger confirmation (1 VLM call)
  Fast sampling: 3–4 frames around the trigger event
  Goal: confirm the trigger (did the dog actually squat/defecate?)
  If not confirmed → discard, no watch opened

Phase 2 — Active watch (N VLM calls, interval = f(elapsed_time))
  Sampling interval = elapsed_sequence_time / target_frame_count
  As the sequence stretches longer, frames spread further apart —
  no point sampling at 1fps for a 90s sequence when 1 frame per 10s captures the state transitions
  Each call asks: "has the completion action occurred yet?"
  If yes → log, close watch early

Phase 3 — Departure confirmation (1 VLM call)
  On subject exiting frame or area
  Final check: is the deposit still visible on the ground?
  This is the most reliable signal — presence/absence of the deposit
  is clearer than detecting the pickup action itself
```

**Why departure-time ground check is more reliable than watching the pickup action:**
- The pickup motion is small, fast, and angle-dependent
- The deposit's presence or absence on the ground after departure is a cleaner binary signal
- VLM reasoning: "subject has left frame — is there anything left on the ground where the dog squatted?"

**Total VLM calls:** 1 (confirm) + 1–3 (active watch, interval-sampled) + 1 (departure check) = 3–5 calls for a typical sequence. Budget is proportional to sequence length, not fixed.

Current scenarios using this pattern:
- S18 — dog walker doesn't pick up (squat detected → watch for pickup → no pickup = notify)

The same mechanism applies anywhere expected follow-through needs to be verified: delivery driver leaves package vs. takes it back, contractor closes gate after entering, visitor checks for mail vs. opens mailbox.

---

## Failure modes

- **Camera offline:** AttentionMode cannot run → notify household if mode was active ("pool camera lost signal while area was occupied")
- **Low light / night:** face recognition degrades; drowning detection degrades; note in enrichment output
- **Occlusion:** subject partially obscured → flag in enrichment, reduce identity confidence
- **GPU saturation:** AttentionMode specialized models get priority over background VLM tasks; never preempted by normal queue
- **Model crash:** watchdog restarts detector service; alert on restart if AttentionMode was active
