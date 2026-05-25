# SentiHome — Design Notes (brainstorm snapshot)

Snapshot of a brainstorm session. Not final architecture — a starting point for the next conversation. Read end-to-end before extending.

## Vision

A near-sentient home AI agent that monitors cameras and home devices, builds memory of people/vehicles/routines, and alerts household members to situations of interest — a door left unlocked at night, a stranger lingering in the backyard whose behavior matches a concerning pattern, a repeated late-night perimeter approach across nights.

Local-first by default (privacy, latency, no cloud video bill). Explainable decisions (every alert cites the rules that fired). Low false-positive cost is a first-class design constraint — one bad 3am alert per week kills adoption.

## Stack decisions made

- **VMS layer: Agent DVR** (Windows-native, RTSP/ONVIF, webhooks/REST, no Linux/Docker dependency).
- **All inference local**: VLM + reasoner LLM + embedder + fast detector run on local hardware.
- **Rules stored as embeddings in a vector DB**, retrieved per-event into the reasoner's prompt — interpretable, editable, scoped.
- **Hybrid detection**: camera-native AI/motion (ONVIF or vendor WebSocket) is the primary trigger; server-side fast detector runs continuously as a backup trigger AND as a real-time enrichment service.

## Architecture sketch

```
[cams/sensors] → [Agent DVR] → [event bus]
                                    ↓
                       [fast detector — continuous]
                       (objects, faces, recognition,
                        body re-ID, bbox annotation)
                                    ↓ (flagged events)
                       [VLM — per-event scene reasoning]
                                    ↓
                       [reasoner LLM + tool use]
                            ↕             ↕
            [memory: vector + SQL]   [action tools]
                                    ↓
                  [notification router → phones/voice/displays]
```

Two cadences:

- **Reactive (sub-second)**: fast detector classifies, decides if VLM pass is needed.
- **Deliberative (seconds)**: VLM produces structured scene JSON, reasoner LLM evaluates against retrieved rules and world state, picks action.

## Detection pipeline (per event)

1. Trigger source — cam-native (ONVIF/WebSocket), server-side fast detector synthetic event, or sensor (door/lock/window state change).
2. Slice clip: T-10s to T+N from Agent DVR's continuous recording (do not rely on cam-side pre-event buffer — vendor lottery).
3. Frame sampling for VLM: 8 frames default, **detector-guided selection** (frames where subject most visible/central) preferred over uniform.
4. Fast detector enriches each frame: object detections, face detection + recognition (gallery match → known_person_id or "unknown"), body re-ID embeddings, plate OCR, pose, appearance attributes.
5. Track IDs persist across all frames of a clip so cross-frame reasoning has stable references.
6. Annotation: render bboxes + labels on frames (Set-of-Mark style) — visual marks beat text coordinates for VLM grounding. Send both annotated and clean reference frames; downweight low-confidence recognition labels (`Sarah? (0.72)`) so the VLM doesn't over-trust them.
7. Crop-and-zoom: tight high-res crops of any unknown/attention-worthy subject sent alongside full frames.
8. Retrieval: build query from `{camera, area, time, scene summary, actors, world state}` → hybrid retrieval (filter by scope/temporal/conditions in SQL, then ANN-rank).
9. Reasoner LLM gets scene JSON + retrieved rules + world state → decision, rules-fired list, confidence, action, draft notification, `journey_open` flag, re-ID descriptors.

## Rule schema (vector DB)

```
id, text, embedding,
scope: { global | camera_id[] | area_id[] | zone_polygon | world_volume | journey | sensor | composite }
temporal: { time_of_day, day_of_week, season }
conditions: { who_home, alarm_armed, weather, ... }
severity: info | notice | alert | urgent
action: log | notify(targets, channel) | speak | light | lock | siren
authored_by: user | agent_proposed
confidence_required: 0.0–1.0
hit_count, last_fired, dismiss_count
```

Key principles:

- **Hybrid retrieval, not pure vector** — SQL filter on scope/temporal/conditions first, ANN-rank within.
- **Rule lifecycle** — `dismiss_count` lets agent decay noisy rules and surface them for cleanup.
- **Authoring sources**: natural-language from user (LLM normalizes to schema), agent-proposed from observed dismissal/alert patterns, default pack at setup.
- **Areas vs. cameras**: rules attach to semantic _areas_, not cameras, so cam swaps don't break rules and multi-cam areas work naturally.

## Journey / multi-camera reasoning

A **Session** tracks a subject across cameras and time:

```
session_id, opened_at, last_seen_at, closed_at
subject_descriptor: { reid_embedding, appearance_text, face_embedding?,
                      vehicle_plate?, identity_resolution }
segments: [{ camera_id, area, t_start, t_end, clip_ref, vlm_scene_json,
             entry_direction, exit_direction, dwell_s, interactions }]
journey_score: { suspicion, intent_hypotheses }
status: open | closed | escalated
```

Correlation rules for appending a new event to a session:

- Re-ID cosine similarity ≥ threshold, AND
- **Spatial plausibility** — camera adjacency graph says transit was possible in Δt. Reject geometrically impossible matches even with high re-ID score.
- Recency window (~5 min).

Two-cadence reasoning:

- **Incremental**: each new segment updates `journey_score`; alert when journey-scoped rules cross threshold (e.g., "≥3 perimeter cams, no entry approach, <4 min" = casing pattern).
- **On close**: silence timeout or known egress → full VLM-over-stitched-montage pass for summary, filed in episodic memory.

Stitching = temporal montage, not concatenated video: 1–2 best frames per segment, captioned with `[cam | area | t+Δs | dwell | bbox]`, header tile with site map + inferred path including blind-spot transit gaps.

## Fast detector — dual role

1. **Trigger backup**: continuous RTSP subscription, lightweight YOLO/RT-DETR raises synthetic events when cam-native AI misses something.
2. **Enrichment service**: every frame destined for VLM gets:
   - Object detections (class + bbox + track_id)
   - Face detection + recognition (known_person_id | unknown, confidence, gaze, mask/obscured flag)
   - Body re-ID embedding
   - Pose keypoints (for intent/gait/height-from-skeleton fallback)
   - Appearance attributes (clothing colors, holding object, hood up, bag)
   - Plate OCR (vehicles)
   - Scene context (lighting, weather hint, occlusion %)

Quality gates matter — false-positive recognition (labeling stranger as Sarah) is worse than no label. Face too small/oblique/blurred → `face_present_unresolved`, not a guess.

Annotation style:

- Thin outline boxes (2px), semi-transparent face fills
- Label `#2 Sarah ✓`, `#3 unknown ?`, `#4 Amazon ◷`
- Color by trust tier: green=resident, blue=known-visitor, yellow=unknown-benign-pattern, red=unknown-attention
- Confidence shown when below threshold
- Motion arrows on multi-frame montages

## Recognition / identity

- Face: SCRFD/RetinaFace → ArcFace/AdaFace → cosine vs. gallery. Tier thresholds (e.g., ≥0.55 confident, 0.40–0.55 tentative, <0.40 unknown).
- Body re-ID: OSNet etc. **In-session only** — clothing-dependent, unreliable across days.
- **Cross-day identity is a composite signal**, not a re-ID match:
  - Face (when available) — dominant
  - Plate (when vehicle present)
  - Height estimate (clothing-invariant, ±3–5cm with calibrated cam)
  - Behavioral pattern (time, route, approach signature)
  - Cloth-changing re-ID models — tiebreaker only
- Output is a **probability**, not a track ID. UX must reflect uncertainty: _"someone matching Tuesday's late-night visitor — same approach route, similar height, no face this time"_ — not "the same stranger is back."
- Human-in-the-loop labeling is the durable identity layer for unnamed-but-recurring individuals.
- New-face memory bootstrapping: 3rd time same unknown embedding appears, agent prompts user to label.

## Camera calibration

**Intrinsics + extrinsics + ground plane** unlock:

- Real-world distances/speeds in prompts
- Height as clothing-invariant identity feature
- World-coordinate rule scopes (one rule covers all cams of an area)
- Trajectories on site map, re-queryable across cam changes
- World-volume zones (height-aware: raccoon at ground vs. person at chest height)

**Stereo on overlapping cam pairs** adds:

- z-coordinate without ground assumption (climbing, on roof, on deck)
- Near-deterministic cross-cam correlation in overlap zones (geometry beats re-ID)
- Occlusion fusion
- 3D anomaly primitives (object thrown over fence, person on roof)

Realistic precision: ground-plane ±10–20cm at 5–15m; stereo depth ±15–30cm; height ±3–5cm averaged. Plenty for "approached the door / climbed the fence" reasoning, not biometric-grade.

Calibration UX options (best → worst effort):

1. Phone-AR walk — phone broadcasts ARKit/ARCore pose, cams capture it as fiducial, bundle-adjust everything.
2. Landmark tagging on floor plan + cam images, solve PnP.
3. Resident-walk auto-calibration — known-height residents walked over a week, solve from foot-tracks.

Drift detection is essential: fixed landmarks per cam, schedule reprojection check, flag drifted cams. Resident heights are a continuous canary — sudden change in measured height = cam drifted.

Stereo sync: RTSP frames not synced; match nearest by timestamp + per-cam latency offset, discard triangulations where time gap > ~50ms for fast subjects.

## PTZ cameras

Continuous-parameter calibration is fragile. The practical pattern:

- **Calibrated presets** — treat each preset as a virtual fixed cam, calibrate per preset. PTZ becomes "N fixed cams sharing one device."
- **PTZ pair stereo** — only over shared presets, never active-tracking.
- **Best ROI pattern**: fixed wide-FOV cam (continuous detection + geometry) + PTZ (slews to preset for face/plate detail capture on flagged events). Fixed cam owns geometry; PTZ contributes appearance only. Sidesteps PTZ calibration almost entirely.
- Frames during PTZ slewing or active tracking flagged `geometry_unreliable`.

## Alerting & action policy

- Confidence tiers: silent log → in-app notification → phone push → ring everyone → siren+lights.
- Quiet-hours aware (same event more alertable at 2am than 2pm).
- Who-to-wake routing: light-sleeper preference, who's home, who responded last time.
- Conversational confirmation for ambiguous events: _"I see someone at the back fence, doesn't match anyone I know, been there 90 seconds. Should I turn the floods on?"_
- Autonomous defaults: lights/notifications yes; locks/sirens require policy pre-approval or human confirmation.
- Every alert cites the rules that fired → directly editable by the user.

## Open design questions

Still on the table:

1. **Site coordinate frame** — where's origin, what are axes? Affects floor-plan UI and future map overlays.
2. **Single ground plane vs. multi-plane** — yards have decks, steps, slopes. Auto-learn from resident foot-tracks over time.
3. **Which cam pairs justify stereo calibration** — tag pairs with sufficient overlap + baseline in metadata.
4. **Cold start** — pre-rules period: default rule pack, or pure-LLM mode that proposes rules from first 2 weeks of observation?
5. **VLM cost per event** — even flagged-only pipeline hits hundreds of calls/day on 6–10 cams. Batch frames, cache scene descriptions per tracked subject, GPU duty cycle tolerance?
6. **Rule conflict resolution** — severity-wins, most-specific-scope-wins, or LLM-arbitrated?
7. **Annotation rendering pipeline** — server-side OpenCV vs. overlay-as-data structure reusable for UI.
8. **Input pixel budget per VLM prompt** — fix early (e.g., 768–1024 longest side, 8 frames + 1 crop = 9 images per call).
9. **Set-of-Mark vs. native labels** — A/B on chosen VLM (Qwen2.5-VL vs. InternVL behave differently).
10. **Resident enrollment UX** — walk-up cam, drop photos, or "label this person from yesterday's clip"? (Last is most natural.)
11. **Multi-resident preference conflicts** — whose rules win?
12. **Memory retention & privacy** — how long to keep clips/embeddings of non-household people?
13. **Voice surface** — does it talk back? Through what?
14. **Failure modes** — LLM down, network out, cam goes dark mid-event.
15. **Trust model for first-encounter unknown faces** — silent observe, ask household to label, or treat suspicious by default?

## Suggested next chapters

- **Site coordinate model + calibration UX** — foundational, gates world-coordinate rules and journey 3D paths.
- **Rule schema deep-dive** — concrete YAML/JSON shape, retrieval algorithm, scope semantics, conflict resolution.
- **VLM prompt assembly contract** — exact shape of per-event prompt and journey-close prompt; what's image, what's text, image budget.
- **Fast detector pipeline** — model choices, throughput targets, annotation rendering, recognition gallery management.
- **Camera + area + zone data model** — site coordinate frame, adjacency graph, role tags, world-volume zones.
- **Hardware sizing** — VLM/LLM model selection vs. expected event rate vs. GPU budget.

Pick a thread and continue.
