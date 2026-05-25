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

10. **NVR-optional architecture maturation** — When does the long-term vision (direct camera → HA → SentiHome, no NVR layer) become the default recommendation? Requires:
    - SentiHome's internal motion detection proven robust in production
    - SentiHome handling clip archival at scale
    - Direct RTSP adapter performance validated across camera brands
    - User research on whether HA users actually want to drop their existing NVRs
    - Impacts: §02, §03.5, marketing/positioning
    - Status: Documented as v3–v4 vision; revisit after v2 ships

11. **Native plugin priority and language** — When is Agent DVR native plugin worth the engineering investment, and what stack (Agent DVR uses .NET)?
    - Impacts: §03.5
    - Status: Deferred until v1 ships and we measure service-mode bottlenecks in real deployments

12. **SentiHome Plugin API open spec** — Should we publish a spec so third-party NVR vendors can implement native plugins?
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

---

## Decision log (chronological record)

| Date | Decision | Rationale | Affects | Status |
|------|----------|-----------|---------|--------|
| 2026-05-23 | **VMS layer: Agent DVR** | Windows-native RTSP/ONVIF support, webhook ingestion, local frame buffering | §02, §03, §08 | Implemented |
| 2026-05-23 | **Inference: local Ollama + cloud fallback** | Preserve privacy by default; cloud only when local GPU saturated | §04, §18 | Implemented |
| 2026-05-23 | **HA is single source of device truth** | Centralize state, avoid duplicating device logic; HA already integrates Z-Wave, Matter, Thread, MQTT | §02, §03, §07 | Implemented |
| 2026-05-24 | **Rule conflict resolution: scope specificity + severity hierarchy** | Most specific scope wins; if same scope, highest severity wins; hard conflicts surface to user | §10 | Implemented |
| 2026-05-24 | **Hybrid rule retrieval: SQL filter + ANN rank** | First-pass filter by scope/area; re-rank by embedding similarity to current context | §10 | Implemented |
| 2026-05-24 | **Feedback-driven optimization: autonomous variant testing** | System generates variants from user ground truth (miss/FP); tests on archived clips; rolls out safely | §10.5 | Implemented |
| 2026-05-24 | **Multi-camera identity fusion: overlapping views with complementary angles** | Strategic placement (doorbell face + side gait) enables stereo verification and multi-modal signals | §12.5 | Implemented |
| 2026-05-24 | **Temporal evidence accumulation: identity confidence compounds over time** | Day-1 tentative → Day-2 retroactive re-eval → Day-3 multi-camera fused | §12.5 | Implemented |
| 2026-05-24 | **Observability: AI synthesis layer for root cause + recommendations** | LLM-based reasoning layer correlates metrics, explains failures, recommends actions | §17 | Implemented |
| 2026-05-24 | **Hardware tier scaling: Tier 1 ($800), Tier 2 ($1500), Tier 3 ($2500+)** | Starter, comfortable, and premium configurations with clear upgrade paths | §18 | Implemented |
| 2026-05-24 | **Data governance: Class A-D + privacy tiers (local_only, cloud_eligible, cloud_any)** | Data classification at source, enforcement at router; no accidental cloud leakage | §16 | Implemented |
| 2026-05-24 | **Site coordinate frame: single frame + per-floor Z-awareness** | All cameras calibrated to shared origin; multi-floor support via Z-coordinates | §13-14 | Implemented |
| 2026-05-24 | **Failure modes: degrade, don't fail + safe defaults matrix** | 10 failure modes with degradation strategies and safe action matrix | §19 | Implemented |
| 2026-05-25 | **Memory model: five layers (working, session, episodic, identity, semantic)** | Explicit layering enables lifecycle management (TTL per layer) and query efficiency | §11 | Implemented |
| 2026-05-25 | **Load shedding: frame budget reduction → enrichment downshift → preemption** | Graceful capacity management during GPU saturation; priority-aware | §03 | Implemented |
| 2026-05-25 | **Multi-resident consent: most restrictive wins** | If one resident has stricter privacy, their preference blocks higher-risk data paths | §16 | Implemented |
| 2026-05-25 | **Right-to-forget flow: soft-delete + 7-day grace + secure erase** | User can request deletion of person; marked for deletion, grace period for recovery, then permanent | §16 | Implemented |
| 2026-05-25 | **Variant rollout safety: 7d silent → 7d shadow → 2w gradual → full replacement** | Phased rollout with instrumentation at each gate; rollback triggers on FP/FN degradation | §10.5 | Implemented |
| 2026-05-25 | **HA is device orchestration layer; SentiHome is rule engine** | Rules live in SentiHome (conversational creation), HA executes actions; HA automations are optional extensions | §02, §07, §10, §15 | Implemented |
| 2026-05-25 | **NVR is pluggable data source via NVR Adapter Layer (§03.5)** | Universal compatibility; v1 ships service mode; native/built-in modes are optimizations layered on top | §02, §03, §03.5, §07, §08 | Implemented |
| 2026-05-25 | **Service mode is the v1 default** | Ships universally with any RTSP/ONVIF source; no native plugins required for v1; works with Agent DVR, Blue Iris, Synology, QNAP, UniFi Protect, raw cameras | §03.5 | Implemented |
| 2026-05-25 | **Do not recommend a specific NVR to users** | Let users choose based on existing infrastructure; HA + cameras + SentiHome is a valid path; pushing an NVR creates friction and lock-in | §03.5 | Implemented |
| 2026-05-25 | **Long-term vision: NVR-optional architecture (v3–v4)** | As SentiHome matures, it absorbs NVR responsibilities (motion, archival, clips); direct camera → HA → SentiHome becomes the recommended path; NVR users remain supported but it's not the recommendation | §02, §03.5 | Future direction documented |
| 2026-05-25 | **Hardware sizing marked preliminary** | Estimates will be revised based on real-world household deployment data from the maintainer; mode-dependent variance is significant and unmeasured | §18 | Implemented (marked preliminary) |
| 2026-05-25 | **Robust hybrid motion detection (MOG2 + optical flow + size filter + temporal consistency + on-camera AI corroboration)** | Motion detection is the 24/7 gating function; false positives from lighting/wind/rain would burn compute and erode trust | §08 | Implemented |

---

## Resolved questions (archived, see section reference for details)

| Original Q | Solution | Section | Resolution date |
|-----------|----------|---------|-----------------|
| Site coordinate frame — origin, axes | Single shared frame; cameras calibrated relative to site origin; multi-floor via Z | §13-14 | 2026-05-24 |
| Single vs multi ground plane | Single frame with per-floor Z-awareness; multi-level future | §14 | 2026-05-24 |
| Which cam pairs justify stereo | Complementary angles (face + gait); entry/exit points Priority 1 | §12.5 | 2026-05-24 |
| VLM cost per event under load | Hardware tier analysis + cloud cost tracking in observability | §18, §17 | 2026-05-24 |
| Rule conflict resolution algorithm | Scope specificity + severity hierarchy + hard conflicts to user | §10 | 2026-05-24 |
| Input pixel budget per VLM prompt | Adaptive: 1-8 frames based on enrichment tier + context quality | §09 | 2026-05-24 |
| Cold start — default pack vs pure-LLM | Hybrid: seed with default rules, LLM suggests refinements, user approves | §10 | 2026-05-24 |
| Failure mode coverage | All 10 modes documented with degradation strategies and safe defaults | §19 | 2026-05-24 |
| Resident enrollment UX | Face crop + optional voice + name; gallery promotion on labeling | §12 | 2026-05-24 |
| Multi-resident preference conflicts | Most restrictive consent wins; surface to user if blocking data path | §16 | 2026-05-25 |
| Memory retention for non-household people | 30-day local TTL for unknowns; 30-day cloud (optional); never cross-linked | §16 | 2026-05-25 |
| Trust model for first-encounter unknown faces | Tentative claim if confident, no claim if uncertain; user feedback improves future | §12 | 2026-05-24 |
| Annotation rendering pipeline | Via OpenCV preprocessing; tunable per camera; variant testing in §10.5 | §08, §10.5 | 2026-05-24 |
| Set-of-Mark vs native labels | Both options viable; variant testing determines per-model best approach | §10.5 | TBD (testing Q3) |

---

## Notes for next review (2026-06-01)

**Completed in this cycle:**
- ✓ Sections 1-19 drafted and stable
- ✓ New sections 10.5 (feedback-driven optimization) and 12.5 (dynamic identity refinement) completed
- ✓ Multi-camera fusion and stereo calibration details finalized
- ✓ AI observability synthesis layer designed
- ✓ Hardware sizing from starter to enterprise
- ✓ Failure modes and degradation strategies complete
- ✓ **Architecture clarification: HA is device orchestration; SentiHome is rule engine**
  - Rules created conversationally in SentiHome, not as HA automations
  - HA provides world state context and executes actions
  - HA automations are optional user extensions, not primary mechanism
  - Clean separation: SentiHome intelligence + HA device control
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
