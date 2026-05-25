# 17 — Observability & Operations

**Purpose:** Comprehensive instrumentation, synthesis, and feedback loops so the system is tunable, debuggable, learnable, and trustworthy. Operations dashboard with AI-mediated reasoning for root cause analysis, impact prediction, and action recommendations.
**Status:** stable

---

## Principle: Observability is part of the product

Observability is not a post-hoc monitoring layer. It's integrated into every decision:
- Every detection decision cites evidence + confidence + rules fired
- Every cloud egress decision is audited and explainable
- Every optimization variant is tested before rollout
- Every failure triggers informed degradation, not silent loss
- User feedback trains the system; system learns to predict which feedback to surface

---

## Metrics taxonomy

### Intelligence metrics (how well the system understands)

**Rule performance:**
```
Per rule (per area, per camera, per time range):
  - Fire count (how often triggered)
  - Dismiss count (user dismissed)
  - Confirm count (user confirmed)
  - Dismiss rate (dismiss_count / (dismiss_count + confirm_count))
  - False positive estimate (confidence-weighted dismiss rate)
  - False negative estimate (user-reported missed detections)
  - True positive estimate (confirm + fire without dismiss)
  - Precision per rule per context
  - Recall per rule per context
  - Confidence distribution (histogram of rule's output confidences)
  - Temporal patterns (when rules fire most, least)
```

**Detection quality (fast detector):**
```
Per detector model (YOLO, face, pose, re-ID):
  - Inference latency: p50, p95, p99
  - Object detection rate (people, vehicles, animals per hour)
  - Face detection success rate (faces detected / expected from pixel area)
  - Re-ID embedding consistency (cosine similarity of same person, adjacent frames)
  - Body pose accuracy (proxy: OpenPose consensus with multi-angle views)
  - Quality gate pass rate (% of detections passing confidence thresholds)
  - Seasonal drift (accuracy variance quarter-over-quarter)
```

**Identity confidence:**
```
Per identified person / per camera / per day:
  - Face match confidence (histogram of 0.0-1.0)
  - Re-ID body match confidence
  - Cross-camera fusion confidence (multi-view boost)
  - Gait biometric confidence
  - Behavior pattern confidence (greeting, dress, social)
  - Composite identity confidence (fused signal)
  - Manual confirmation rate (user had to correct auto-identification)
  - Time-to-confidence (how many observations until confident)
  - Confidence variance across days (drift)
  - Retroactive re-evaluation impact (when user confirms identity, how many past detections re-scored)
```

**VLM reasoning quality:**
```
Per VLM call (per area, per camera, per rule):
  - Inference latency: p50, p95, p99 per backend (local vs cloud)
  - Confidence output distribution (what % return >0.8, 0.5-0.8, <0.5)
  - Agreement with deterministic rule baseline (when VLM disagrees with rule, is user feedback)
  - Explanation coherence (does VLM explanation match what actually happened)
  - Token usage per request (cost proxy for cloud)
  - Context token count per request (are we over-enriching or under-enriching)
```

**User feedback patterns (the ground truth signal):**
```
Per user / per rule / per area:
  - Dismissal count by reason (false alarm, duplicate, wrong rule, already handled)
  - Confirmation count (user agrees with alert)
  - Reported miss count (user reported event system missed)
  - Miss detail (event description, timestamp, severity)
  - Time-to-feedback (how fast does user dismiss/confirm)
  - Feedback variance by time (morning vs night feedback behavior)
  - Rule refinement requests (user manually edited rule after alert)
  - Per-rule trustworthiness (high-dismiss rules get lower weight)
```

**Optimization feedback:**
```
Per rule variant (from §10.5):
  - Phase duration (silent → shadow → gradual → full)
  - FP rate pre/post rollout
  - FN rate pre/post rollout
  - Dismissal rate change
  - Compute cost change
  - User preference (if rule was offered, did user adopt)
  - Rollback triggers fired
  - Cross-scenario validation: did variant break other rules
```

---

### Operational metrics (how well the system runs)

**Pipeline health:**
```
Per worker (triage, detector, VLM, reasoner, action dispatcher):
  - Queue depth (events waiting)
  - Processing latency: p50, p95, p99
  - Throughput (events/sec)
  - Error rate (% of events failed)
  - Worker availability (% of time not crashed)
  - Memory usage
  - CPU usage
  - Backpressure events (worker dropped events due to queue full)
```

**Resource utilization:**
```
Compute:
  - GPU utilization (% busy)
  - GPU memory (allocated vs available)
  - CPU utilization across cores
  - System memory (used vs total)
  - Thermal status (core temps, throttling)

Storage:
  - Local database size (SQL, vector DB)
  - Raw frame archive size
  - Episodic memory size
  - Cold storage size (archived old data)
  - Disk I/O (bytes/sec read/write)
  - Disk fill rate (when will disk be full)

Network:
  - Bandwidth to internet (mbps)
  - Bandwidth to cameras (mbps)
  - Bandwidth to HA (mbps)
  - Cloud upload rate (bytes/sec)
  - Cloud download rate (bytes/sec)
  - Latency to cloud (ms p50, p95)
  - DNS resolution time
```

**Component health:**
```
Each critical backend (per §04, §06):
  - Last successful contact (timestamp)
  - Consecutive failed requests
  - Circuit breaker state (open/half-open/closed)
  - Last error message
  - Error rate (% of requests failed)
  - Response time distribution
  - Degradation mode active (yes/no)

Camera streams:
  - Frame arrival latency
  - Dropped frame count
  - Stream quality (% of frames below quality threshold)
  - Reconnection count
  - Uptime (last N days)

Event bus:
  - Message loss count
  - Latency to publish message
  - Subscriber lag (how far behind are workers)
  - Topic backlog size
```

**Data governance:**
```
Cloud egress audit (per §16):
  - Bytes sent to cloud (per type: frames, detection JSON, episodic)
  - Cloud destinations (which APIs)
  - Scrubbing effectiveness (how much data was scrubbed before sending)
  - Retention compliance (data deleted per policy)
  - Privacy violation attempts (e.g., tried to send local_only data to cloud)

Data freshness:
  - Latest camera frame age (ms since last frame)
  - HA state staleness (ms since last sync)
  - Rule cache age (when was cache last updated)
  - Identity gallery cache age
```

**Cost tracking:**
```
Per month:
  - Cloud VLM API calls (count + cost)
  - Cloud storage (GB + cost)
  - Bandwidth out (GB + cost)
  - Est. total cloud cost
  - Cost per detection
  - Cost per alert
  - Cost per rule fire
  - Budget remaining (if user set cap)
```

---

## Dashboard: Three-level observability

### Level 1: Overview (operator/user)

Visible at a glance: system status, critical alerts, top recommendations.

```
╔═════════════════════════════════════════════════════════════════════════════╗
║ SentiHome Status: HEALTHY / ⚠ DEGRADED / 🔴 CRITICAL                       ║
║ Last sync: 45s ago                                                          ║
╠═════════════════════════════════════════════════════════════════════════════╣
║                                                                             ║
║ COMPONENTS:                                                                 ║
║  ✓ Cameras: 4/4 online (last frame 2s ago)                                 ║
║  ✓ Detector: running, 8ms avg latency                                      ║
║  ⚠ VLM: local GPU 78% utilization, queue depth 5                           ║
║  ✓ Home Assistant: online, state current                                    ║
║  ✓ Event Bus: healthy, no lag                                              ║
║  ✓ Storage: 340GB / 500GB (68%), will fill in 12 days                     ║
║                                                                             ║
║ ALERTS (last 24h):                                                          ║
║  • Dog escape rule: 3 fires, 2 dismissals (67% FP rate)                    ║
║  • Delivery detector: 1 miss on 2026-05-23 15:22 (user reported)           ║
║  • Interior camera: offline 45s on 2026-05-22 14:30 (recovered)            ║
║  • GPU saturation: 2 events, queue depth reached 12                        ║
║                                                                             ║
║ TOP RECOMMENDATIONS (AI-synthesized):                                       ║
║  1. [HIGH IMPACT] Dog escape rule overfitting — 67% false positives         ║
║     → Suggested action: Run feedback optimization on variant strategy       ║
║     → Expected FP reduction: 40-50%                                         ║
║     [Run optimization] [View rule] [Dismiss]                               ║
║                                                                             ║
║  2. [MEDIUM IMPACT] Storage will fill in 12 days                            ║
║     → Current: 68% utilized (340GB / 500GB)                                ║
║     → Suggested action: Enable cloud cold archive + delete old clips       ║
║     → Cost: $8/month, frees 120GB immediately                              ║
║     [Enable archive] [Delete old data] [Dismiss]                           ║
║                                                                             ║
║  3. [LOW IMPACT] GPU utilization trending up                               ║
║     → Current: 78% peak (up 12% from last week)                            ║
║     → Trend: +3% per day if unchanged                                      ║
║     → Suggested action: Reduce frame budget or offload to cloud VLM        ║
║     [View GPU metrics] [Configure frame budget] [Dismiss]                  ║
║                                                                             ║
║ CLOUD USAGE (last 30 days):                                                 ║
║  ✓ 1.2GB exterior frames → VLM (cost: $1.80)                               ║
║  ✓ 340MB detection JSON → backup (cost: $0.20)                             ║
║  ✗ 0B resident faces (privacy: local only)                                 ║
║  ✗ 0B interior frames (privacy: local only)                                ║
║                                                                             ║
║ [View detailed metrics] [AI Chat] [Settings]                               ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
```

---

### Level 2: Detailed metrics (operator/developer)

Drill down on any metric: time series, distribution, correlation.

```
RULE PERFORMANCE DASHBOARD:

Selected rule: "dog_escape_backyard"
Time range: Last 30 days
Camera: "backyard"

Performance graph:
  Fires/day: ████████████████░░░░  16 avg (peak 24 on 5/20)
  Confirm %: ██░░░░░░░░░░░░░░░░░░  8% (2 confirmed of 25)
  Dismiss %: ███████████████░░░░░░  67% (17 dismissed)
  Unknown %: ██░░░░░░░░░░░░░░░░░░  25% (6 no user feedback)

Confidence distribution (histogram):
  0.9-1.0: ████░ 5 (all confirmed)
  0.8-0.9: ██░░░ 3 (2 confirmed, 1 dismissed)
  0.7-0.8: ████████░ 10 (2 confirmed, 8 dismissed)
  0.6-0.7: ██████████████░ 20 (0 confirmed, 15 dismissed)
  <0.6:    ███░ 7 (no rules fired, was filtered)

Dismissal reasons (from user feedback):
  "False alarm (dog just running)" ███████████░ 12
  "Already handled" ██░ 2
  "Wrong alert" ░ 1
  (no feedback given) ███░ 3

Optimization suggestions (AI analysis):
  • High false positive rate (67%) driven by 0.6-0.7 confidence band
  • Users dismiss ~90% of detections at this confidence level
  • Current rule sensitivity too high; consider raising threshold from 0.5 → 0.65
  • Or: improve detector pre-processing (edge detection for dog outline)
  • Or: multi-camera fusion (use side camera for gait biometric confirmation)

Historical comparison:
  Previous week (5/18-5/24): 67% FP rate
  Month avg: 65% FP rate
  3-month avg: 58% FP rate
  Trend: ↑ worsening (variant rollout on 5/20 made it worse)

[Run optimization] [Edit rule] [View variant history]
```

---

### Level 3: AI Chat interface (operator/user)

Natural language conversation with synthesis layer for diagnosis and recommendations.

```
USER: "Why is the dog escape rule firing so much?"

AI SYNTHESIS LAYER REASONING:
  1. Data gathering:
     - Rule: dog_escape_backyard
     - Metric lookup: 67% dismiss rate in last 30 days
     - Root cause analysis:
       a) Rule confidence threshold: 0.5 (too low)
       b) Detector: YOLO v5, not tuned for small dog forms
       c) Multi-camera fusion: disabled (only 1 camera covers backyard)
       d) Time-of-day correlation: peaks at 18:00-20:00 (peak activity hours)
     
  2. Confidence assessment:
     - High confidence in root cause: low threshold + detector weakness
     - Medium confidence in multi-camera hypothesis (no data yet)
     - Low confidence in seasonal factors (only 30 days data)
  
  3. Impact estimation:
     - Current impact: 17 false positives in 30 days = 0.57 FP/day
     - User burden: ~3-4 dismissals per week
     - System cost: wasted GPU cycles, false escalations to HA
  
  4. Recommendation generation (ranked by impact):
     a) Raise confidence threshold from 0.5 → 0.65
        - Estimated FP reduction: 40-50% (remove 0.6-0.7 band)
        - Risk: slight FN increase (may miss edge cases)
        - Cost: none
        - Time to implement: 5 minutes
     
     b) Run feedback optimization (variant testing on archived clips)
        - Test 18 variants (resolution, frame interval, preprocessing, multi-subject)
        - 7-day silent testing, then shadow, then gradual rollout
        - Estimated FP reduction: 50-70%
        - Risk: optimization could overfit to recent pattern
        - Cost: GPU + storage for 7 days test
        - Time to implement: 2 hours (system does this automatically)
     
     c) Add side camera to multi-camera fusion
        - Gait biometric confirmation would reduce FP to <20%
        - Risk: requires camera installation + calibration
        - Cost: camera hardware + setup time
        - Time to implement: 1 day
  
  5. Explanation & transparency:
     - Rule is learning from user feedback correctly (67% dismissals)
     - System is doing its job (detecting motion, alerting)
     - Rule needs tuning (threshold or detector improvement)
     - User trust preserved (honest about false positives)

RESPONSE TO USER:
  "Your dog escape rule is firing too much because the confidence 
   threshold (0.5) is catching a lot of marginal detections from 
   the backyard camera. YOLO detects motion, but without a second 
   angle to confirm the dog is actually escaping (vs just playing), 
   it guesses low.

   I recommend [HIGHEST IMPACT] running automated optimization right 
   now — I'll test 18 variants of preprocessing and frame selection 
   against the 25 archived clips from the last month, especially the 
   17 you dismissed. The test takes 7 days in the background, and if 
   a variant catches dogs escaping without false alarms, I'll gradually 
   roll it out over 2 weeks.

   Or [FASTER] raise the threshold from 0.5 to 0.65 right now. You'll 
   miss some edge cases, but you'll cut false alarms by ~40%.

   Or [LONG-TERM] if you have a second camera that can see the backyard 
   exit, I can fuse gait biometrics and cut false alarms to <20%.

   Which would you prefer? I can start optimization immediately."

USER: "Run the optimization, but let me know if it starts being too 
       conservative and missing real escapes."

AI: "Got it. I'm starting a 7-day silent test against your 25 clips. 
    I'll show you the top 5 variants and their metrics before rollout. 
    And I'll monitor: if FN (missed escapes) ever goes up, I'll pause 
    the rollout and alert you."

[View optimization progress] [View variant performance] [Pause]
```

---

## AI Synthesis Layer (inference at query time)

The synthesis layer is an LLM-based reasoning service that runs when the operator queries the dashboard or when anomalies are detected.

### Inputs to synthesis

```python
synthesis_context = {
  "requested_query": "Why is the dog escape rule firing so much?",
  "time_range": "last_30_days",
  "metric_snapshot": {
    "rule_name": "dog_escape_backyard",
    "fire_count": 25,
    "dismiss_count": 17,
    "confirm_count": 2,
    "unknown_count": 6,
    "confidence_distribution": {...},
    "detector_model": "yolo_v5",
    "detector_latency_p95": 8,  # ms
    "detector_quality_gate_pass": 0.78,
    "camera_coverage": "single_backyard_camera",
    "multi_camera_fusion": False,
    "historical_trend": {
      "30d_avg": 0.65,
      "7d_avg": 0.67,
      "trend": "worsening"
    }
  },
  "user_feedback": [
    {"timestamp": "2026-05-23T15:22Z", "action": "dismiss", 
     "reason": "false alarm", "confidence": 0.72},
    {"timestamp": "2026-05-22T18:11Z", "action": "confirm", 
     "confidence": 0.91},
    # ... 23 more feedback entries
  ],
  "system_state": {
    "gpu_utilization": 0.78,
    "storage_available_days": 12,
    "internet_latency": "normal",
    "cloud_budget_remaining": 0.65
  },
  "similar_rules_performance": {
    "delivery_detection": 0.12,  # FP rate
    "person_at_door": 0.08,
    "vehicle_on_driveway": 0.22
  }
}
```

### Synthesis reasoning flow

```
1. CONTEXT ASSEMBLY
   - Parse query intent: root cause diagnosis
   - Gather relevant metrics: rule performance, detector quality, coverage
   - Historical context: trend, comparison to similar rules
   - System constraints: GPU, storage, cost

2. HYPOTHESIS GENERATION
   - Hypothesis 1: Threshold too low (evidence: 67% FP in 0.6-0.7 band)
   - Hypothesis 2: Detector overfitting (evidence: similar FP rate on other animals)
   - Hypothesis 3: Single camera limitation (evidence: no gait biometric)
   - Hypothesis 4: Time-of-day pattern (evidence: peaks at 18:00-20:00)
   - Rank by confidence: H1 (high), H3 (medium), H2 (medium), H4 (low)

3. IMPACT ESTIMATION
   - Current state cost: 0.57 FP/day × 0.5 min/dismissal = 4.75 min/week user burden
   - Impact on system: false escalations to HA, wasted GPU (5% of 78% = 3.9%)
   - Risk of inaction: FP rate trending up; user trust declining

4. RECOMMENDATION GENERATION
   - Rank by: impact × feasibility × implementation cost
   - Top 3 recommendations (with explanations + caveats)
   - Include rollout safety profile for each

5. EXPLANATION & TRANSPARENCY
   - Cite specific evidence for each conclusion
   - Acknowledge uncertainty (e.g., "medium confidence" on multi-camera)
   - Explain what user feedback improved accuracy of analysis
   - Show which metrics are most important to the diagnosis

6. CONFIDENCE REPORTING
   - High confidence (>0.8): diagnosis is reliable, act on recommendation
   - Medium confidence (0.5-0.8): diagnosis is reasonable, but validate with user
   - Low confidence (<0.5): insufficient data, collect more before deciding
```

### Synthesis outputs

```
synthesis_output = {
  "diagnosis": {
    "root_cause": "Low confidence threshold capturing marginal detections",
    "confidence": 0.87,
    "supporting_evidence": [
      "67% of alerts at 0.6-0.7 confidence are dismissed",
      "YOLO v5 without gait confirmation = high false positive rate",
      "Compared to delivery_detection (12% FP), this rule is 5.6x worse"
    ],
    "severity": "medium"  # impacts user experience, not safety
  },
  
  "recommendations": [
    {
      "rank": 1,
      "action": "Run feedback optimization (variant testing)",
      "estimated_improvement": {
        "fp_reduction": 0.55,  # 55% reduction
        "fn_increase": 0.05   # 5% more misses (acceptable)
      },
      "cost": {
        "time": "2 hours automation, 7 days silent test",
        "gpu": "2% of capacity for 7 days",
        "storage": "500MB temp"
      },
      "rollout_safety": "7d silent → 7d shadow → 2w gradual",
      "rollback_triggers": ["FN > 0.1", "FP < 0.15"],
      "explanation": "I'll test 18 variants of preprocessing, frame selection, 
                     and detector tuning against your 25 archived clips. 
                     Variants that eliminate false alarms without missing 
                     real escapes get gradually rolled out."
    },
    {
      "rank": 2,
      "action": "Raise confidence threshold: 0.5 → 0.65",
      "estimated_improvement": {
        "fp_reduction": 0.40,
        "fn_increase": 0.08
      },
      "cost": {
        "time": "5 minutes",
        "gpu": "no change",
        "storage": "no change"
      },
      "rollout_safety": "immediate",
      "rollback_triggers": ["user_reports FN", "rule_dismissal still > 0.50"],
      "explanation": "Remove the 0.6-0.7 confidence band. Cuts false alarms 
                     by ~40%, but may miss edge cases."
    },
    {
      "rank": 3,
      "action": "Add multi-camera gait fusion (if 2nd camera available)",
      "estimated_improvement": {
        "fp_reduction": 0.80,
        "fn_increase": 0.01
      },
      "cost": {
        "time": "1 day (setup + calibration)",
        "hardware": "possibly none (if side camera already exists)"
      },
      "rollout_safety": "requires manual calibration first",
      "explanation": "If you have another camera that sees the backyard exit, 
                     gait biometrics confirm the dog is really leaving."
    }
  ],
  
  "monitoring": {
    "watch_metrics": ["dismiss_rate", "fp_on_similar_rules", "gpu_utilization"],
    "alert_if": "dismiss_rate stays > 0.50 for 7 more days",
    "review_in": "7 days (after optimization completes)"
  }
}
```

---

## Action execution & feedback loops

### Safe auto-remediation policies

Some actions are safe enough to execute automatically; others require approval or human confirmation.

```
AUTO-ALLOWED (no approval needed):
  ✓ Raise alert threshold (reduces false positives, won't break safety)
  ✓ Adjust frame budget (reduces compute, acceptable trade-off)
  ✓ Enable cloud archive (Privacy tier allows it, cost is opt-in)
  ✓ Start silent variant testing (background, no change to user experience)
  ✓ Log a diagnostic (purely observational)

APPROVAL-REQUIRED (ask user first):
  ~ Run shadow variant test (changes behavior silently; tell user)
  ~ Gradual rollout (user chose it, but we ask for confirmation if impact changes)
  ~ Auto-delete old archived data (irreversible)
  ~ Adjust device action pre-approvals (affects locks, sirens)
  ~ Enable cloud VLM fallback during GPU saturation (privacy tier change)

HARD-BLOCKED (never execute):
  ✗ Unlock a door without explicit user request + visual confirmation
  ✗ Activate siren without explicit user request
  ✗ Send resident face to cloud (privacy tier = local_only)
  ✗ Disable a safety rule without user confirmation
  ✗ Delete variant that was working better (keep history)
```

### Variant rollout with safety gates

When a feedback optimization recommends a variant, system rolls out in phases with instrumentation at each gate.

```
PHASE 1: SILENT (7 days)
  - New variant runs in parallel with current rule
  - No user visible change (current rule still fires)
  - Metrics collected on both: TP, FP, FN, latency, cost
  - Rollback trigger: if FN >> current rule (>3σ), stop silently
  - User is told: "System is testing a new rule variant in the background"
  - Output: "Variant A: 58% FP, 2% FN | Current: 67% FP, 3% FN"

PHASE 2: SHADOW (7 days)
  - New variant fires instead of current rule (but marked "shadow")
  - Alert shows both: "Shadow suggests this, current rule suggests that"
  - User sees: "System is previewing a new rule; your feedback helps validate it"
  - User feedback: confirm if shadow alert matches reality
  - Rollback trigger: if user feedback disagrees with shadow (>50% dismissals)
  - Output: "Variant A: 59% FP, 2% FN (user confirms 95%)"

PHASE 3: GRADUAL (10-14 days)
  - Variant replaces current rule, starting at 10% traffic
  - 10% → 25% → 50% → 100% over 2 weeks (ramp if safe)
  - Metrics monitored per cohort: is variant still working at scale?
  - Rollback trigger: FP ↑ >20%, FN ↑ >10%, user dismissals spike
  - User is told: "New rule is rolling out gradually; will be fully live in 2 weeks"
  - Output: "Variant A at 25%: 58% FP, 2% FN (stable)"

PHASE 4: FULL REPLACEMENT (permanent)
  - Variant is now the rule; previous rule archived
  - Continue monitoring: FP, FN, user feedback
  - Seasonal learning enabled (see below)
  - Rollback possible if metrics degrade

Example timeline for dog_escape variant:
  2026-05-23 14:30 — User reports miss, optimization starts
  2026-05-23 15:00 — Silent phase begins (Variant A + 17 others tested)
  2026-05-30 15:00 — Variant A wins (58% FP vs 67%), shadow phase begins
  2026-06-06 15:00 — User feedback agrees (95%), gradual rollout starts
  2026-06-13 15:00 — Variant A at 50%, metrics stable
  2026-06-20 15:00 — Variant A at 100%, previous rule archived
  2026-06-20 onwards — Seasonal learning monitors FP/FN across weeks
```

---

## Feedback loops: Learning & adaptation

### Real-time feedback (seconds to hours)

**User dismissal/confirmation:**
- User dismisses alert → dismiss_count ↑, feedback recorded
- System recalculates rule precision estimate
- If dismiss rate exceeds threshold (>0.50), surface recommendation to user
- Recommendation: adjust threshold, run optimization, or review rule logic

**Pattern detection (within hours):**
- Clustering dismissals: are they all at same time? same location? same confidence band?
- If cluster found, suggest rule refinement
- Example: "Last 4 dog escapes (all dismissed) were between 18:00-20:00. Your dog is playful at that hour, not escaping. Should I silence the rule then?"

### Medium-term feedback (days to weeks)

**Optimization feedback (from §10.5):**
- User provides ground truth (this was a miss, this was a false alarm)
- System generates variants and tests against archived clips
- Variant performance tracked: FP, FN, cost per variant
- Best variant ranked; recommended for rollout
- Rollout safety gates ensure no regressions

**Rule evolution (crowdsourced from dismissals):**
- Cluster dismissal reasons: "False alarm", "Already handled", "Wrong alert"
- If "False alarm" cluster grows, suggest rule threshold adjustment
- If "Already handled" cluster grows, suggest rule scope narrowing (e.g., only fire once per day)
- User approves refinement or dismisses suggestion

### Long-term feedback (months)

**Seasonal learning:**
- Compare metrics across quarters: Q1 (winter), Q2 (spring), Q3 (summer), Q4 (fall)
- Does "person at door" fire more in summer (deliveries peak)?
- Does "dog escape" fire more in spring/fall (mild weather)?
- System learns: adjust rule sensitivity seasonally
- Example: raise dog_escape threshold in spring, lower in winter

**Cross-year drift detection:**
- Compare this year's metrics to last year's
- If FN trending up, detector may need retraining
- If FP trending up, rule may need adjustment
- If latency trending up, system resources may need upgrade

**User preference learning:**
- Which recommendations does user accept?
- Which rules does user never dismiss (high trust)?
- Which areas does user configure most (high engagement)?
- System learns: weight recommendations accordingly, focus observability on high-interest areas

---

## Audience-specific views

### For the homeowner (user-facing)

**Simple, actionable, trustworthy:**

```
STATUS: System is healthy, watching over you.

RECENT ACTIVITY:
  Today (6 events):
    ✓ 14:22 — Person at front door (mail carrier, 0.92 confidence)
    ✓ 13:45 — Dog in backyard (Buddy, 0.98 confidence)
    ? 08:30 — Unknown person in garage (low light, 0.41 confidence, no alert)
    
RULES WORKING WELL (high trust):
  ✓ "Delivery detection" — 3 confirmed, 0 false alarms this month
  ✓ "Known resident arrivals" — 28 confirmed, 0 misses this month
  ✓ "Interior motion after hours" — 12 confirmed (all were me), 0 false alarms

RULES NEED HELP (low trust):
  ? "Dog escape" — firing too much (67% false alarms)
    → System suggests: Run automated optimization (will test 18 variants)
    → Or: Raise alert threshold to reduce alarms
    [Run optimization] [Adjust manually] [I'll deal with it]

CLOUD DATA USAGE:
  You've used 1.2GB cloud storage this month (~$1.80).
  - Porch camera: 800MB (backed up for reference)
  - Detections: 400MB (analysis)
  - Data will be deleted on 2026-06-25 (per your 30-day retention setting)
  [View audit] [Adjust retention]

PRIVACY:
  ✓ Your face has never left home (local-only)
  ✓ Interior cameras have never gone to cloud (local-only)
  ✓ Exterior frames are scrubbed of unknowns before backup
```

### For the operator / system admin

**Detailed, diagnostic, actionable:**

```
HEALTH DASHBOARD:
  [Timeline view of all events]
  [Metric graphs: GPU, storage, latency]
  [Component status: cameras, HA, bus, VLM, etc.]
  [Cloud spend: $/day trending, forecast, budget]

RULES ANALYTICS:
  [Precision/recall per rule per area]
  [Confidence distribution heatmaps]
  [User feedback patterns (dismissal reasons)]
  [Variant testing history + rollout status]

OPTIMIZATION QUEUE:
  [Pending feedback optimizations]
  [Active variant tests (phase + % complete)]
  [Rollout in progress (phase, cohort %)]
  [Completed optimizations (impact summary)]

DIAGNOSTICS:
  [Full event log with traces]
  [Error logs with stack traces]
  [Performance bottleneck analysis]
  [Disk usage breakdown]
  [Network requests (latency, errors)]
  [Database query slowlog]

AI CHAT:
  "Why is GPU usage trending up?"
  "What's the ROI of adding a second camera?"
  "Is my current rule precision acceptable?"
  "How much would optimization reduce false alarms?"
```

### For the developer

**Technical, extensible, introspectable:**

```
TRACING & INSTRUMENTATION:
  [Per-event trace: ingress → triage → detector → VLM → reasoner → action]
  [Timing breakdown: queue latency, processing time, router decisions]
  [Dropped events: why were they dropped]
  [Failed requests: error messages, retry policy]

METRIC EXPORT:
  [Prometheus endpoint: /metrics]
  [Structured logs: JSON, queryable by trace_id]
  [Time series database export: InfluxDB, Grafana]

DEBUGGING:
  [Replay tooling: re-run an event against new rules/prompts]
  [Variant testing: create synthetic variants, test on archived clips]
  [Model profiling: inference latency per layer]
  [Memory profiling: peak memory, leaks]

CONFIGURATION:
  [Rule schema: validate, preview before deploy]
  [Model registry: available models, metrics, licenses]
  [Feature flags: enable/disable experimental features]
  [Alert thresholds: tune per component]
```

---

## Integration with other components

### Alerts & thresholds

```
Threshold examples (tunable):

Rule performance (triggers recommendation):
  - FP rate > 0.50 for 7+ days → "Rule needs refinement"
  - FN count >= 3 in 30 days → "Consider optimization"
  - Precision < 0.75 → "Rule is below target accuracy"

Operational health (triggers alert):
  - GPU utilization > 0.85 for 5+ minutes → Warning
  - GPU utilization > 0.95 → Critical (load shedding activates)
  - Queue depth > 100 → Warning
  - VLM latency p95 > 5s → Warning
  - Camera offline > 30s → Warning
  - Storage > 90% full → Critical
  - Cloud cost trending > budget → Warning

Feedback (triggers learning):
  - Dismissal rate > 0.50 → Suggest optimization
  - N feedback entries >= 10 → Sufficient signal, run analysis
  - Same dismissal reason cluster >= 3 → Pattern detected
```

### Observability → Rule authoring feedback

```
When user opens "edit rule" dialog, pre-populate with diagnostics:

Current performance (last 30 days):
  Precision: 0.33 (67% false positives)
  Recall: 0.25 (75% false negatives)
  
Suggested improvements:
  • Raise threshold: 0.5 → 0.65 (est. +40% precision)
  • Add spatial constraint: only fire in backyard, not front porch
  • Add temporal constraint: disable 18:00-20:00 (peak false alarms)
  • Add exclusion: don't fire if motion is < 30px (shadows, wind)

[Edit rule] [Run optimization] [View variant testing]
```

### Observability → Failure modes & degradation (per §19)

```
When component fails, observability informs degradation:

Example: VLM offline
  - Observability detects: VLM process exited
  - Failure mode F7 triggered (per §19)
  - Degradation: fall back to detector-only rules
  - Observability output: "VLM unavailable; alerts will use rule matching only"
  - User sees: honest alert ("rule match, no VLM confidence")
  - Operator sees: "VLM latency offline; circuit breaker open; 
                   detector-only rules engaged; expect higher FP rate"
```

---

## System design requirements

### Storage

```
Metrics time series storage:
  - InfluxDB or similar (high-cardinality, time-optimized)
  - Retention: 90 days high-resolution (1min), then 1y aggregated (1h)
  - Size estimate: ~1MB/day per camera (detection + VLM metrics)
  - For 4 cameras: ~120MB/month, ~1.5GB/year

Audit logs (immutable):
  - Append-only, stored in local database
  - Every rule fire, every dismissal, every cloud egress
  - Retention: per §16 (indefinite for rules)
  - Size estimate: ~100KB/day, ~30MB/year

Variant test artifacts:
  - Archived clips: stored locally (or cold storage)
  - Variant results: metrics per variant per clip
  - Retention: per variant lifecycle (7d silent → shadow → rollout → archive)
  - Size estimate: ~5GB during active optimization, cleans up after
```

### Compute

```
Synthesis layer (AI reasoning):
  - LLM inference (local or cloud)
  - Latency requirement: <5s for recommendation generation
  - Frequency: on-demand (when user queries) or periodic (daily anomaly detection)
  - Cost: if cloud LLM, ~$0.01 per query

Metric aggregation:
  - Background job: every 1min, aggregate metrics from workers
  - Every 1h, compute derived metrics (trends, correlations)
  - CPU: minimal (<5% of one core)

Tracing:
  - Per-event trace collection: distributed tracing (OpenTelemetry style)
  - Sampling: 100% for errors, 10% for normal events
  - Overhead: <2% latency impact
```

### Presentation

```
Dashboard (web or mobile app):
  - Real-time updates: WebSocket from server
  - Graphs: render metrics client-side (Plotly, Recharts)
  - Data refresh: overview every 30s, detailed every 5s
  - Caching: browser caches past 7 days of metrics locally

AI Chat:
  - Conversational UI (like ChatGPT)
  - Context: current metrics, recent events, user feedback history
  - Response: structured recommendation object (not just free text)
  - Integration: chat queries trigger synthesis layer directly
```

---

## Example: Feedback loop in action

**Day 1 (2026-05-23):**
- User reports: "My dog escaped at 15:22 but system didn't alert"
- System logs feedback: `missed_detection(timestamp=15:22, area=backyard, gravity=high)`
- Observability records: rule didn't fire (confidence below threshold)

**Day 2 (2026-05-24):**
- System initiates optimization (§10.5)
- Generates 18 variants: different thresholds, preprocessing, frame intervals
- Starts silent testing on 25 archived clips from this area + rule
- Variant A tests: raise threshold slightly, add gait biometric check
- Variant B tests: improve detector with edge detection preprocessing
- ... (17 more variants)

**Day 3-9 (2026-05-25 to 2026-05-31):**
- Silent phase: both current rule and variants run in parallel
- Variants A & B outperform current rule on archived clips
- Synthesis layer monitors metrics: FP, FN, latency, cost per variant
- User continues normal operation (sees no change)

**Day 10 (2026-06-01):**
- Shadow phase begins: Variant A now fires alerts (marked "shadow")
- User sees: "System preview: this new variant would have caught your dog escape"
- User feedback: "Yes, this variant looks better"
- Synthesis output: "Variant A: 58% FP (down from 67%), 2% FN (down from 3%)"

**Day 11-17 (2026-06-02 to 2026-06-08):**
- Gradual rollout: Variant A at 10% → 25% → 50% traffic
- Metrics stable across cohorts: no unexpected regressions
- Observability dashboard shows: "Variant A rolling out, 58% FP (target met)"

**Day 18 (2026-06-09):**
- Variant A at 100%: now the default rule
- User feedback continues: "Much better, fewer false alarms"
- System archives old rule + variant testing results
- Seasonal learning enabled: monitor FP/FN across months

**Ongoing:**
- System continues learning: if FP increases next spring (peak dog activity), seasonal adjustment kicks in
- User trust increased: system caught the miss, improved automatically, never repeated it
