# 20 — Open Questions & Decision Log

**Purpose:** Things still undecided, and a chronological record of decisions as they land.
**Status:** living

## Open questions (remaining design decisions)

These questions are still in active discussion or awaiting implementation feedback. Once resolved, they move to the Decision Log with supporting section references.

### Immediate (next phase)

1. **Voice alerting surface** — Should TTS alerts include explanation of confidence/reasoning, or just event summary?
   - Impacts: §15 (alerting), §17 (observability UX)
   - Status: Under discussion (waiting for user preference data)

2. **Resident enrollment flow** — What's the smoothest way to add a household member (faces, voice, preferred name)?
   - Impacts: §12 (recognition), app UX
   - Status: Prototype exists, needs user testing

3. **Rule authoring for non-technical users** — How much template scaffolding vs. NL suggestion?
   - Impacts: §10 (rules), app UX
   - Status: Three approaches drafted, A/B test planned

4. **Cloud cost cap & overflow behavior** — If user sets monthly budget and VLM overage predicted, should we:
   - Auto-throttle to stay under budget? (risk: misses important events)
   - Ask user per-request? (UX friction)
   - Queue and replay after month resets? (unacceptable latency)
   - Impacts: §04 (router), §18 (cost)
   - Status: Awaiting feedback from early adopters

5. **Per-area confidence thresholds** — Should bedroom, bathroom have higher thresholds (privacy) than entry?
   - Impacts: §15 (alerting), §16 (privacy)
   - Status: Proposed, awaiting implementation

### Medium-term (design validation)

6. **Set-of-Mark prompt strategy for VLM** — Best approach to highlight bounding boxes?
   - Option A: Native bbox labels (depends on VLM variant)
   - Option B: Rendered annotations in image (preprocessing overhead)
   - Impacts: §09 (VLM prompt), §17 (performance)
   - Status: Variant testing pending (see §10.5)

7. **Multi-ground-plane calibration** — For homes with multiple floors, is single site frame sufficient or do we need per-floor?
   - Impacts: §13-14 (geometry), §12.5 (multi-camera fusion)
   - Status: Current design uses single frame; floor-aware Z-coordinates planned for future

8. **Anonymous visitor re-identification across days** — If unknown face matches itself 3 weeks later, auto-label as "recurring visitor"?
   - Impacts: §12 (identity), §16 (privacy)
   - Status: Algorithm designed, awaiting user consent model

9. **Pet re-identification improvements** — Can gait + coat pattern achieve same confidence as human faces?
   - Impacts: §12 (recognition), §10.5 (optimization)
   - Status: Collecting data, variant testing to start Q3 2026

### Long-term (research / future versions)

10. **NVR-optional architecture maturation** — When does the long-term vision (direct camera → HA → Kukii-Home, no NVR layer) become the default recommendation? Requires:
    - Kukii-Home's internal motion detection proven robust in production
    - Kukii-Home handling clip archival at scale
    - Direct RTSP adapter performance validated across camera brands
    - User research on whether HA users actually want to drop their existing NVRs
    - Impacts: §02, §03.5, marketing/positioning
    - Status: Documented as v3–v4 vision; revisit after v2 ships

11. **Native plugin priority and language** — When is Agent DVR native plugin worth the engineering investment, and what stack (Agent DVR uses .NET)?
    - Impacts: §03.5
    - Status: Deferred until v1 ships and we measure service-mode bottlenecks in real deployments

12. **Kukii-Home Plugin API open spec** — Should we publish a spec so third-party NVR vendors can implement native plugins?
    - Impacts: §03.5
    - Status: Deferred far-future; only relevant after we've shipped at least one native plugin and battle-tested it

13. **Service-mode resource optimizations** — Shared-memory frame pipes, GPU decode pass-through (NVDEC), substream-only processing
    - Impacts: §03.5, §08
    - Status: Deferred; only relevant if service mode shows measurable bottlenecks in production

14. **Performance tier surfacing in UX** — How to communicate "you're in service mode; native mode would be 2x better" without confusing users
    - Impacts: §17, app UX
    - Status: Deferred until tiers actually differ in shipping product

15. **Anomaly detection (what's unusual for this home?)** — Should system learn "normal" activity patterns and flag outliers?
    - Impacts: §10 (rules), §17 (metrics)
    - Status: Architecture supports it; awaiting ML model

16. **Predictive alerts (pre-alert for high-risk scenarios)** — Should system warn "package likely to be stolen" before it is?
    - Impacts: §15 (alerting), §06 (reasoning)
    - Status: User consent + false-positive cost assessment needed

17. **Cross-home identity correlation** — If same person visits multiple homes in network, cross-home linking?
    - Impacts: §12 (identity), §16 (privacy)
    - Status: Deferred (privacy concerns)

18. **Voice ID + speaker diarization** — Recognize household members by voice?
    - Impacts: §12 (recognition), §16 (privacy)
    - Status: On roadmap; awaiting privacy framework

19. **Action reasoning transparency** — When system denies a rule action ("can't unlock, door sensor offline"), explain why to user?
    - Impacts: §06 (reasoning), §15 (actions)
    - Status: Designed but not yet implemented in UI

20. **Heterogeneous source fusion — per-source reliability + privacy-tier routing (Ring as the motivating case)** — How do we incorporate weaker / cloud / partial cameras (Ring, other cloud doorbells) as _contributing signals_ rather than excluding them, without letting their limitations contaminate confidence or privacy?
    - **Motivating insight:** a Ring camera can't do what the local RTSP cameras do (no continuous stream → no pre-event buffer, cloud latency, generic non-AI motion, footage leaves the home). But "catch some signal rather than ignore it entirely" is exactly the noisy-OR composite logic the identity fusion already uses (§12.5): a weak independent vote can only _raise_ confidence, never lower it. The same reframe that makes face + gait + CC-ReID complementary makes a weak camera a legitimate soft input. Ring's standout value is **not** recognition — it's:
      - a **spatial/temporal node in the multi-camera graph** (path reconstruction — see below);
      - a **high-precision intent signal** via the doorbell `_ding` event (someone _deliberately_ presented at the door), which the local cameras don't have and which needs zero CV. (`_ding` handling is already scaffolded in ha-agent's enricher/notifier/triage.)
    - **Two additive uses that barely depend on Ring's CV quality:**
      1. **Multi-camera path reconstruction.** Ring as one node in a cross-camera journey: even a low-quality "person present at front, T=0" observation, fused with driveway/side/pool sightings over time, helps recreate the path someone took across the property. This is the §12.5 multi-camera fusion + the §13 adjacency graph applied to a heterogeneous source — the node's _existence + timestamp + coarse class_ carry the signal, not its embedding quality. (Connects to S4 loitering / S5 repeated-perimeter / S17 repeated-approach.)
      2. **Absence / negative-sequence signals.** A person parks in the driveway (Reolink captures the arrival) but **never approaches the door** (no Ring `_ding`, no front-door camera presence) — the _non-arrival_ is itself a suspicious signal. This is the "expected completion didn't happen" pattern (cf. S18's sequence-completion watch, the `absence` trigger type in the rule taxonomy). Ring contributes here purely as a **negative observation** — its silence is the evidence — so cloud latency and weak CV are irrelevant to its value in this mode.
    - **Where the simple "Ring = one weak camera" analogy breaks (the actual design work):**
      - **Fusion is per-_modality_, not per-_source_, today.** `fusion.py` weights are keyed on `match_method` (face/gait/ccreid…), explicitly "trust of that _modality_, not this _camera_." There is **no per-source / per-camera reliability weight in code** — it's design-only. Doing this properly means adding a **source-reliability dimension** to fusion + the triage scorer so a detection on a cloud/weak source enters at a lower prior than the same detection on a local cam. This is reusable infrastructure ("how much do I trust _this sensor_"), not a Ring hack.
      - **Independence is load-bearing and partly false.** Noisy-OR only legitimately _boosts_ when votes are independent. Two cameras on the same approach at near-identical angles aren't independent; counting correlated agreement as independent votes manufactures false confidence. Path reconstruction (distinct vantage points / times) largely satisfies independence; same-scene corroboration does not.
      - **Privacy is a gate, not a weight.** `privacy_tier` is a coded concept in the event/VLM-request schemas (local_only / cloud_eligible / cloud_any per §16). Ring footage is cloud-sourced, so it must be **tagged at ingress and routed** (e.g. cannot flow to a local-only VLM path) — you cannot express "less trusted because cloud" by merely lowering an α.
    - **Scope:** three distinct pieces — (1) per-source reliability as a first-class fusion + triage input; (2) privacy-tier routing for cloud-sourced frames; (3) doorbell-`_ding` (and its _absence_) as named composite triggers fusing with co-located camera evidence. Out of scope for Validation Pass 1 (which stays on the two local cameras that exercise the full pipeline). Post-validation enhancement.
    - **Impacts:** §12.5 (multi-camera fusion), §13 (adjacency graph), §16 (privacy tiers), §10 (rule taxonomy: `absence` / `composite`), §03.5 (ha-camera adapter as the universal fallback path)
    - **Status:** Captured for post-validation; design only, no code. Generalizes beyond Ring to any heterogeneous/low-reliability source.

21. **Persistent-presence / state-transition memory — distinguishing "just arrived" from "been there for days"** — A parked car is the same pixels on day 1 and day 3; what differs is _history_. The system must reason over **state transitions** (appeared / departed / changed), not instantaneous presence — and only memory carries the prior state that makes a detection a _transition_ rather than a re-observation.
    - **Motivating insight (from live driveway testing):** the car parked in the driveway is a non-event when it's been there for days, but its _first arrival_ is a real event — identical detections, opposite significance. "The event is the state transition, not the state."
    - **What motion-gating already solves for free (the easy ~80%):**
      - Car _first parks_ → arrival motion → detection fires → **event.** ✓
      - Car _sits for days_ → no motion → detection never runs → **no event.** ✓ The steady-state non-event is suppressed at the detection layer with no memory needed.
    - **Where motion-gating fails and memory is the ONLY answer (the ~20%):**
      1. **Co-occurrence contamination.** A person walks by (motion fires) → the window runs → YOLO now detects _both_ the person _and_ the long-parked car. Without memory the car re-enters reasoning as if new; memory says "that vehicle's been here since Friday — background, focus on the person."
      2. **Departure / offset.** "The car that was parked for 3 days is _gone_" is a real event, but there is **no motion at an empty spot** to trigger it. Absence has no pixels — only memory ("expected present, no longer confirmed") produces the departed event.
      3. **Substitution.** A _different_ car takes the same spot. Identical at the "car in driveway" level; only identity (embedding / plate) + onset timestamp distinguishes "same car still here" from "new car — whose?".
      4. **Cold reads.** Reboot, a "what's in the driveway right now?" query, the overnight digest — no motion history to lean on; the present-state baseline must be _remembered_.
    - **The missing primitive.** No existing memory object covers this. `Event` nodes are instantaneous; `VisitLedger` tracks discrete arrive/leave _visits_ for recurring subjects, not a continuously-present object; `last_known_location` exists but only for `PetActor`. What's absent is a general **persistent-presence / scene-state layer**, e.g.

      ```
      PersistentPresence: entity(vehicle|object|person), area,
                          present_since, last_confirmed_at, identity_ref, state
      ```

      plus a triage rule that a raw detection becomes an `Event` only on a **transition** of that state (onset → "arrived"; offset → "departed"; substitution → "replaced"); continued presence is by definition a non-event.

    - **The honest hard part.** Onset is cheap (motion already gives it). **Offset and substitution are the hard ones** — they require _periodic re-confirmation_: a lightweight non-motion-driven sweep over the preprocessor's continuous buffer asking "is everything I believe is present still present, and still the same?" That recurring confirmation pass is the piece that does not exist yet (and has a real, if small, steady-state cost).
    - **Relationship to other items:** this is the **spatial twin** of #20's temporal absence/negative-sequence idea (parks-but-never-approaches) — same shape, where the signal is a _change in expected state_, not a positive detection. It is also the baseline #15 (anomaly detection) needs: "what is normally present here" can't be judged without a remembered present-state.
    - **Impacts:** §11 (memory model — new presence/scene-state layer), §03/§08 (triage: detection→event only on transition; the re-confirmation sweep), §10 (rule taxonomy: `absence`), §15 (anomaly/#15)
    - **Status:** Captured for post-validation; design only, no code. Surfaced during live two-camera bring-up.

---

## Decision log (chronological record)

| Date       | Decision                                                                                                                   | Rationale                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Affects                       | Status                           |
| ---------- | -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- | -------------------------------- |
| 2026-05-23 | **VMS layer: Agent DVR**                                                                                                   | Windows-native RTSP/ONVIF support, webhook ingestion, local frame buffering                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | §02, §03, §08                 | Implemented                      |
| 2026-05-23 | **Inference: local Ollama + cloud fallback**                                                                               | Preserve privacy by default; cloud only when local GPU saturated                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | §04, §18                      | Implemented                      |
| 2026-05-23 | **HA is single source of device truth**                                                                                    | Centralize state, avoid duplicating device logic; HA already integrates Z-Wave, Matter, Thread, MQTT                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | §02, §03, §07                 | Implemented                      |
| 2026-05-24 | **Rule conflict resolution: scope specificity + severity hierarchy**                                                       | Most specific scope wins; if same scope, highest severity wins; hard conflicts surface to user                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | §10                           | Implemented                      |
| 2026-05-24 | **Hybrid rule retrieval: SQL filter + ANN rank**                                                                           | First-pass filter by scope/area; re-rank by embedding similarity to current context                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | §10                           | Implemented                      |
| 2026-05-24 | **Feedback-driven optimization: autonomous variant testing**                                                               | System generates variants from user ground truth (miss/FP); tests on archived clips; rolls out safely                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | §10.5                         | Implemented                      |
| 2026-05-24 | **Multi-camera identity fusion: overlapping views with complementary angles**                                              | Strategic placement (doorbell face + side gait) enables stereo verification and multi-modal signals                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | §12.5                         | Implemented                      |
| 2026-05-24 | **Temporal evidence accumulation: identity confidence compounds over time**                                                | Day-1 tentative → Day-2 retroactive re-eval → Day-3 multi-camera fused                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | §12.5                         | Implemented                      |
| 2026-05-24 | **Observability: AI synthesis layer for root cause + recommendations**                                                     | LLM-based reasoning layer correlates metrics, explains failures, recommends actions                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | §17                           | Implemented                      |
| 2026-05-24 | **Hardware tier scaling: Tier 1 ($800), Tier 2 ($1500), Tier 3 ($2500+)**                                                  | Starter, comfortable, and premium configurations with clear upgrade paths                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | §18                           | Implemented                      |
| 2026-05-24 | **Data governance: Class A-D + privacy tiers (local_only, cloud_eligible, cloud_any)**                                     | Data classification at source, enforcement at router; no accidental cloud leakage                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | §16                           | Implemented                      |
| 2026-05-24 | **Site coordinate frame: single frame + per-floor Z-awareness**                                                            | All cameras calibrated to shared origin; multi-floor support via Z-coordinates                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | §13-14                        | Implemented                      |
| 2026-05-24 | **Failure modes: degrade, don't fail + safe defaults matrix**                                                              | 10 failure modes with degradation strategies and safe action matrix                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | §19                           | Implemented                      |
| 2026-05-25 | **Memory model: five layers (working, session, episodic, identity, semantic)**                                             | Explicit layering enables lifecycle management (TTL per layer) and query efficiency                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | §11                           | Implemented                      |
| 2026-05-25 | **Load shedding: frame budget reduction → enrichment downshift → preemption**                                              | Graceful capacity management during GPU saturation; priority-aware                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | §03                           | Implemented                      |
| 2026-05-25 | **Multi-resident consent: most restrictive wins**                                                                          | If one resident has stricter privacy, their preference blocks higher-risk data paths                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | §16                           | Implemented                      |
| 2026-05-25 | **Right-to-forget flow: soft-delete + 7-day grace + secure erase**                                                         | User can request deletion of person; marked for deletion, grace period for recovery, then permanent                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | §16                           | Implemented                      |
| 2026-05-25 | **Variant rollout safety: 7d silent → 7d shadow → 2w gradual → full replacement**                                          | Phased rollout with instrumentation at each gate; rollback triggers on FP/FN degradation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | §10.5                         | Implemented                      |
| 2026-05-25 | **HA is device orchestration layer; Kukii-Home is rule engine**                                                            | Rules live in Kukii-Home (conversational creation), HA executes actions; HA automations are optional extensions                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | §02, §07, §10, §15            | Implemented                      |
| 2026-05-25 | **NVR is pluggable data source via NVR Adapter Layer (§03.5)**                                                             | Universal compatibility; v1 ships service mode; native/built-in modes are optimizations layered on top                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | §02, §03, §03.5, §07, §08     | Implemented                      |
| 2026-05-25 | **Service mode is the v1 default**                                                                                         | Ships universally with any RTSP/ONVIF source; no native plugins required for v1; works with Agent DVR, Blue Iris, Synology, QNAP, UniFi Protect, raw cameras                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | §03.5                         | Implemented                      |
| 2026-05-25 | **Do not recommend a specific NVR to users**                                                                               | Let users choose based on existing infrastructure; HA + cameras + Kukii-Home is a valid path; pushing an NVR creates friction and lock-in                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | §03.5                         | Implemented                      |
| 2026-05-25 | **Long-term vision: NVR-optional architecture (v3–v4)**                                                                    | As Kukii-Home matures, it absorbs NVR responsibilities (motion, archival, clips); direct camera → HA → Kukii-Home becomes the recommended path; NVR users remain supported but it's not the recommendation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | §02, §03.5                    | Future direction documented      |
| 2026-05-25 | **Hardware sizing marked preliminary**                                                                                     | Estimates will be revised based on real-world household deployment data from the maintainer; mode-dependent variance is significant and unmeasured                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | §18                           | Implemented (marked preliminary) |
| 2026-05-25 | **Robust hybrid motion detection (MOG2 + optical flow + size filter + temporal consistency + on-camera AI corroboration)** | Motion detection is the 24/7 gating function; false positives from lighting/wind/rain would burn compute and erode trust                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | §08                           | Implemented                      |
| 2026-06-01 | **Never trade video quality / detection capability for compute; capture at maximum fidelity, downsample at analysis**      | Capture is lossy-downward-only — any fps/resolution can be derived from a max-fidelity capture, but detail discarded at capture is gone forever. Degrading capture to fit current hardware would bake that hardware's limits into permanent reference data. Compute shortfalls are a production-hardware problem (4090 + NVDEC), not a corpus-quality one. Surfaced when an 8K driveway capture ran at ~0.34fps on CPU and missed a walking person — the response is the right hardware, never lower-quality capture.                                                                                                                                                                                                                    | §08, §18, §10.5 (eval corpus) | Adopted                          |
| 2026-06-01 | **Saturate all available hardware — drive every parallelism axis concurrently; idle silicon is the waste, not compute**    | Active-utilization corollary of the line above. The pipeline has three independent parallelism axes — frame-level (CPU cores: decode/encode/slice/merge, GIL-releasing), branch-level (face/body/gait via the router + ResourcePool), and tile-level (GPU-batched detection on 4K). On the target 24-core Threadripper + 4090, all three run at once, with the decode→queue→workers boundary overlapping CPU prep and GPU inference so neither idles. Quality scales up to fill the hardware (more tiles, higher imgsz, larger models) rather than work being trimmed to fit. Tiled detection is the new tile-axis; worker pools + ResourcePool sizes become core-count-driven and simulator-tuned (10.11.3b) against measured hardware. | §10.11, §10.12, §08           | Adopted (design in 10.12)        |

---

## Resolved questions (archived, see section reference for details)

| Original Q                                    | Solution                                                                           | Section    | Resolution date  |
| --------------------------------------------- | ---------------------------------------------------------------------------------- | ---------- | ---------------- |
| Site coordinate frame — origin, axes          | Single shared frame; cameras calibrated relative to site origin; multi-floor via Z | §13-14     | 2026-05-24       |
| Single vs multi ground plane                  | Single frame with per-floor Z-awareness; multi-level future                        | §14        | 2026-05-24       |
| Which cam pairs justify stereo                | Complementary angles (face + gait); entry/exit points Priority 1                   | §12.5      | 2026-05-24       |
| VLM cost per event under load                 | Hardware tier analysis + cloud cost tracking in observability                      | §18, §17   | 2026-05-24       |
| Rule conflict resolution algorithm            | Scope specificity + severity hierarchy + hard conflicts to user                    | §10        | 2026-05-24       |
| Input pixel budget per VLM prompt             | Adaptive: 1-8 frames based on enrichment tier + context quality                    | §09        | 2026-05-24       |
| Cold start — default pack vs pure-LLM         | Hybrid: seed with default rules, LLM suggests refinements, user approves           | §10        | 2026-05-24       |
| Failure mode coverage                         | All 10 modes documented with degradation strategies and safe defaults              | §19        | 2026-05-24       |
| Resident enrollment UX                        | Face crop + optional voice + name; gallery promotion on labeling                   | §12        | 2026-05-24       |
| Multi-resident preference conflicts           | Most restrictive consent wins; surface to user if blocking data path               | §16        | 2026-05-25       |
| Memory retention for non-household people     | 30-day local TTL for unknowns; 30-day cloud (optional); never cross-linked         | §16        | 2026-05-25       |
| Trust model for first-encounter unknown faces | Tentative claim if confident, no claim if uncertain; user feedback improves future | §12        | 2026-05-24       |
| Annotation rendering pipeline                 | Via OpenCV preprocessing; tunable per camera; variant testing in §10.5             | §08, §10.5 | 2026-05-24       |
| Set-of-Mark vs native labels                  | Both options viable; variant testing determines per-model best approach            | §10.5      | TBD (testing Q3) |

---

## Notes for next review (2026-06-01)

**Completed in this cycle:**

- ✓ Sections 1-19 drafted and stable
- ✓ New sections 10.5 (feedback-driven optimization) and 12.5 (dynamic identity refinement) completed
- ✓ Multi-camera fusion and stereo calibration details finalized
- ✓ AI observability synthesis layer designed
- ✓ Hardware sizing from starter to enterprise
- ✓ Failure modes and degradation strategies complete
- ✓ **Architecture clarification: HA is device orchestration; Kukii-Home is rule engine**
  - Rules created conversationally in Kukii-Home, not as HA automations
  - HA provides world state context and executes actions
  - HA automations are optional user extensions, not primary mechanism
  - Clean separation: Kukii-Home intelligence + HA device control
- ✓ **NVR Adapter Layer added (§03.5)**
  - NVR is pluggable data source, not core dependency
  - Service mode is v1 default (universal RTSP/ONVIF compatibility)
  - Native and built-in modes are future optimizations
  - Long-term vision: NVR-optional architecture (v3–v4)
  - Hardware sizing marked preliminary pending real-world data
  - Robust hybrid motion detection algorithm defined

**Pending implementation validation:**

- Variant testing rollout (need 2-3 weeks of field data)
- Multi-resident consent UI (need user testing)
- Voice alerting surface (need speech samples)

**Suggested next phase:**

1. Implementation sprint on §10.5 (feedback-driven optimization)
2. Field testing with 3-5 beta users
3. Collect feedback on rule authoring UX (§10) and observability dashboard (§17)
4. Validate hardware sizing predictions (§18) against actual deployments
5. Plan for seasonal learning (multi-quarter data collection)
