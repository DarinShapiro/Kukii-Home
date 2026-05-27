# Epic 10: Identity & Recognition

**Architecture refs:** §11 (memory model), §12 (recognition & identity), §12.5 (dynamic identity refinement), §04 (model router & inference), §08 (detection pipeline), §09 (VLM prompt contract), §10.5 (feedback-driven rule optimization), §17 (observability), §19 (failure modes)
**Components:** services/preprocessor (new), services/recognition (shared types), services/ha-agent, services/dispatcher (new or extended)
**Priority:** P1
**Blocked by:** Epic 04 (event bus minimal), Epic 06 (memory storage scaffold); ALSO requires the inference box rebuild (Agent DVR's host)

---

## Status & scope note

This epic was substantially redesigned during the v0.3.x add-on bring-up, in a multi-turn design dialogue. The decisions captured below supersede the prior thin issue-stub version of this doc. **Where this conflicts with existing `docs/architecture/` files (most notably §11, §12, §12.5, §09, §17, §19), this doc is authoritative; the canonical architecture docs should be updated to match.** A separate task tracks that propagation.

The scope expanded beyond "identity recognition" into a coherent design for the entire recognition → memory → VLM-reasoning → dispatcher path, because identity quality, memory dynamics, and VLM behavior are intertwined and can't be designed in isolation. Issue-list at the bottom reflects the broader scope.

---

## Mental model

Four layers, each with a single clear responsibility:

```
┌─ Cameras (HA + Agent DVR + RTSP fallback) ──────────────────┐
│  Source of frames + native HA motion/AI events.            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─ Preprocessor (continuous service, on inference box) ──────┐
│  Sources frames 24/7 from NVR / cameras.                   │
│  Runs internal pipeline: object detection, face            │
│  detect+embed, vehicle+ReID, plate OCR, pet ID.            │
│  Buffers ~60s hot in memory + ~10min on disk with          │
│  per-frame JSON sidecars of detections.                    │
│  Single external contract: GET /window?camera&from&to →    │
│  pre-analyzed frames + structured detection metadata.      │
│  Internal pipeline is opaque to consumers.                 │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─ Triage + Memory (in ha-agent, backed by Neo4j) ───────────┐
│  Motion event → check active dismissal policies → if no    │
│  short-circuit, assemble VLM context (template queries +   │
│  RAG over graph) → invoke VLM.                             │
│  Memory IS the substrate — no session object, no           │
│  lifecycle to manage. Stateless calls reading + writing    │
│  the same persistent graph.                                │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─ VLM (read-only reasoner; via VLM router) ─────────────────┐
│  Receives assembled context + frames + rules.              │
│  Emits structured output: findings, tier, authored         │
│  policies, recommendations, citations.                     │
│  May emit request_additional_context (multi-call           │
│  iteration); may emit upstream_quality_issues (triggers    │
│  preprocessor tuning).                                     │
│  NEVER calls HA services directly.                         │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─ Dispatcher (policy engine; in ha-agent) ──────────────────┐
│  Maps VLM recommendations → HA service calls based on      │
│  policy (confidence thresholds, time-of-day, redundancy,   │
│  user trust level, AttentionMode flags).                   │
│  Owns the audit trail of what was actually done.           │
└─────────────────────────────────────────────────────────────┘
```

Two parallel feedback loops close the system:

- **User FP/FN signals** → graph edge weights → memory gets sharper over time
- **VLM-reported quality issues** → preprocessor tuning knobs → input quality adapts per camera/condition

Plus a third orthogonal loop:

- **Eval corpus** seeded from production failures → prompt engineering + snapshot regression testing

---

## Memory substrate: Neo4j 5.x (graph + vector hybrid)

The single most consequential decision. Replaces both Postgres-as-memory-store and Qdrant.

**Why graph:** almost everything VLM reasoning needs is relationship-shaped — cross-camera correlation, actor co-occurrence, access profiles, audit influence chains. Multi-hop traversals are first-class in Cypher and painful in SQL.

**Why vector AS WELL:** face / vehicle / scene / behavioral embeddings need similarity search. Neo4j 5.13+ has native vector indexes — embeddings live as properties on graph nodes, queryable in the same Cypher traversal.

**Why edges-as-primary-primitive:** edge weights ARE the memory dynamics. Reinforced by usage (citations from VLM decisions), decayed by disuse (periodic job), pruned below threshold. Hebbian-style — what fires together wires together. The pruning task that previously needed a separate retention-scoring system collapses to "tune the edge-weight reinforcement + decay functions."

Postgres is demoted to OLTP-only (HA config snapshots, alert log, audit log, structured outputs). Qdrant retires.

### Node taxonomy (initial; will grow)

| Node | Properties |
|---|---|
| `Camera` | id, friendly_name, area, has_ir, supports_ptz, …|
| `Area` | id, name, attention_mode (bool), normal_hours, … |
| `KnownActor` | id, name, role, access_profile, face_embeddings (vec), enrollment_quality, … |
| `KnownVehicle` | id, name, owner_actor_id, plate, vehicle_embedding (vec), … |
| `KnownPet` | id, name, owner_actor_id, dinov2_embedding (vec), species, … |
| `Event` | id, ts, source (motion/ha_event/timer/external), preprocessor_window_id, tag_set, … |
| `VLMDecision` | id, ts, backend, model_version, tier, findings_summary, latency_ms, … |
| `Policy` | id, kind (dismissal/transient_intent), scope, match_condition, ttl, created_by_vlm_call, rationale, … |
| `UserFeedback` | id, ts, kind (good_catch/false_alarm/escalation_FN), reason, … |
| `QualityIssue` | id, kind, severity, affected_capability, observed_in_frames, … |
| `KnobAdjustment` | id, ts, knob, old_value, new_value, applied_to_camera_id, … |
| `Alert` | id, ts, headline, tier, recommendations_executed, … |

### Edge taxonomy

| Edge | Direction | Properties |
|---|---|---|
| `OCCURRED_AT` | Event → Camera | confidence |
| `INVOLVES` | Event → KnownActor / KnownVehicle / KnownPet | confidence, match_method |
| `IN_AREA` | Camera → Area | |
| `ACCESSES` | KnownActor → Area | allowed (bool), time_constraint |
| `FREQUENTLY_WITH` | KnownActor → KnownPet / KnownActor | strength, last_seen |
| `CORRELATES_WITH` | Event → Event | strength, reason (same_actor / temporal_adjacency) |
| `CITED` | VLMDecision → Memory node (any) | rank (1st cited, 2nd cited, …) |
| `INFLUENCED` | Memory node → VLMDecision | weight (computed by dispatcher; not VLM-supplied) |
| `YIELDED` | VLMDecision → Alert | |
| `CORRECTED_BY` | Alert → UserFeedback | |
| `REPORTED_ISSUE` | VLMDecision → QualityIssue | |
| `TRIGGERED_TUNE` | QualityIssue → KnobAdjustment | |
| `APPLIED_TO` | KnobAdjustment → Camera | |
| `EFFECTIVENESS_OBSERVED` | KnobAdjustment → QualityMetric | delta, ts |

**Critical: audit log from day one.** Even the simplest milestone (preprocessor stub + mock VLM) must write CITED + INFLUENCED + YIELDED edges. The retention math only becomes useful with months of data; we can't start collecting late.

### Tooling

- **Neo4j Community edition** initially. Home scale never hits its ceiling.
- **Neo4j Bloom** for visualizing memory state over time — critical for debugging "why did the VLM make that decision?" Hover an edge, see weights, follow paths.
- Sidecar container on inference box. ~2 GB RAM at home scale; comfortable.

---

## Preprocessor service (continuous, time-window API)

A self-contained service running 24/7 on the inference box. Internal pipeline is opaque to consumers.

### External contract

One primary endpoint:

```
GET /window?camera=<id>&from=<ts>&to=<ts>[&representative=<n>]
  → 200 [
      { ts, jpeg_url, detections: [...], identities: [...], plate?, embeddings_ref },
      ...
    ]
```

Optional `representative=N` returns N strategically-picked frames (earliest detection, T0, peak detection count, latest) rather than the full window.

Supporting endpoints:

```
GET /capabilities                  → list of cameras + what the preprocessor can extract per camera
POST /tune                         → apply a KnobAdjustment (called by dispatcher's preprocessor tuner)
GET /health                        → service health, model load status, buffer state
```

### Source flexibility

The preprocessor consumes frames from one or more backend kinds:

| Kind | When |
|---|---|
| `agent_dvr_native` | Pull frames + native detections from Agent DVR via its API. Avoids double-processing where AD's built-in detection is adequate. |
| `agent_dvr_passthrough` | Pull frames from Agent DVR but run our own pipeline (when we need richer detection than AD provides) |
| `rtsp_source` | Direct RTSP capture. **Degenerate fallback** for users without an NVR — labeled limited. The user has Agent DVR; this path exists for completeness. |
| `frigate_native` | Future, for other users. Not on this user's roadmap. |

### Internal pipeline (opaque to router/consumers)

```
frame in → object detector (YOLO11x)
         → per-bbox dispatch:
              ├ face → SCRFD detect+align → ArcFace R100 embed → KnownActor match
              ├ vehicle → DINOv2 embed → KnownVehicle match → fastALPR plate detect+OCR
              ├ animal (dog/cat) → DINOv2 embed → KnownPet match
              └ everything else → just class + bbox + confidence
         → write frame + sidecar JSON to ring buffer
         → emit "new frame ready" to subscribers (future: NATS topic)
```

The pipeline is reorderable + swappable without touching consumer contracts. Adding a new stage (e.g. hand/mask detection) is internal.

### Buffer & retention

- **Hot (in-memory):** last ~60 s per camera. Available for sub-50ms response.
- **Warm (on disk):** last ~10 min per camera, original JPEGs + sidecar JSON. ~40 MB per camera at 5 fps.
- **Cold:** none. Anything older than 10 min is preprocessor-evicted; the *interesting* bits are already in episodic memory (graph nodes referencing the frames at the time they mattered).

### Knobs (exposed via POST /tune for preprocessor tuner)

| Layer | Knobs |
|---|---|
| Frame preprocessing | brightness, contrast, CLAHE (on/off + clipLimit), denoise strength, white balance, sharpening |
| Detection | per-class confidence thresholds, NMS IoU, model variant (general vs low-light specialist) |
| Pipeline rate | FPS sampled per camera, frame-skip ratio |
| Identity matching | face/vehicle/pet match thresholds per camera, reference embedding set (day vs night) |
| Camera-driven | IR-cut mode, exposure target, FPS (push to camera API where supported — Reolink/Dahua/Agent DVR) |

---

## VLM reasoning layer

Stateless calls. Memory IS the session. Read-only contract.

### Triage flow per motion event

```
1. Motion event arrives (HA event, timer tick, external observer)
2. Always: write lightweight Event node to memory (timeline complete)
3. Query active dismissal policies for this scope (camera + area + time)
   matching this event's tag set
   ├ Match found → done. Log dismissal. Audit edge: (Event)-[DISMISSED_BY]->(Policy)
   ├ Escalation policy (TransientIntent) match → invoke VLM at elevated tier
   └ No match → invoke VLM at normal tier
4. Triage assembles context (see below)
5. VLM invoked via VLM router
6. Structured response persisted: VLMDecision node + CITED edges + recommendations
7. Dispatcher acts on recommendations (if any)
8. Optionally: VLM emits request_additional_context → re-invoke (max 3 iterations)
9. Optionally: VLM emits upstream_quality_issues → preprocessor tuner adjusts knobs
```

### Context assembly (template + RAG, tiered)

Pre-assembled by triage; VLM receives complete packaged context — no tool round-trips for retrieval.

**Template-driven** (every call, structured Cypher queries, sub-ms):

1. Active policies (dismissals + TransientIntents) for this scope
2. Camera + area context (friendly name, attention_mode flag, normal patterns)
3. Recent events on same camera (last 30 min)
4. Cross-camera correlation (recent events on adjacent cameras within 5 min)
5. Identity context for any KnownActor / KnownVehicle / KnownPet matches in preprocessor output
6. Household state (residents home, SituationalContext, active TransientIntents)

**RAG** (vector + graph hybrid, top-K configurable):

7. Similar past situations (vector search over Event nodes' scene embeddings, filtered by camera/time/actor relevance)

**Tiered budget per call:**

| Tier | Memory budget |
|---|---|
| tier_0 sanity check | Minimal — current event + matched KnownActors only (~2K tokens) |
| tier_1 normal | Template categories 1-6 + top-3 RAG (~8K tokens) |
| tier_2 escalation | Full template + top-10 RAG + recent rule firings + extended actor history (~24K tokens) |

### Structured output contract

```
VLMResponse {
  findings:           { scene_description, identities_confirmed, behaviors_observed, … },
  tier:               null | "tier_0" | "tier_1" | "tier_2" | "tier_3",
  authored_policies:  [
    {
      kind:            "dismissal" | "transient_intent",
      scope:           { camera, area?, actor_id? },
      match_condition: <preprocessor tag pattern>,
      ttl_seconds:     int,
      rationale:       "two known dogs in fenced yard, no novel context",
    }
  ],
  recommendations:    [
    { action_class, target_entity, urgency, confidence, rationale }
  ],
  citations:          [ "evt_7f2c", "actor_alice", "policy_xyz" ],   # IDs only
  upstream_quality_issues: [
    { issue, affected_capability, severity, observed_in_frames }
  ],
  request_additional_context: null | { what, args, why },
  audit:              { context_size, model, latency_ms, … }
}
```

Notes:

- **citations are IDs only.** VLM does NOT assign weights. Dispatcher computes weights from tier + outcome_quality + citation_dilution + position + finality, using signals VLM can't see. Avoids the VLM-self-assessment calibration problem; makes gameability vanish.
- **No `executed_actions` field.** Execution is the dispatcher's record, not the VLM's.
- **Hallucination detection** = citation_id existence check against the graph. Invalid IDs → response trust downgraded, backend reliability score decremented.
- `request_additional_context` is the multi-call iteration mechanism. NOT a tool the VLM calls mid-reasoning — an explicit structured output the triage layer interprets and re-invokes with augmented context. Each call is atomic + auditable.

### Authoring dismissal policies (the throttle)

Throttle isn't time-based; it's content-based and VLM-authored. When VLM concludes "no alert here," it can author a policy: "while preprocessor tag set ⊆ {dog, cat, animal} on this camera, dismiss without VLM call." Future events matching that policy short-circuit before the VLM is invoked.

- **Granularity (A3):** VLM picks per policy. Tag-class for broad cases (`{dog}` = any dog); specific identity for sensitive ones (`{person: Alice}`).
- **Sanity check (B2):** every active policy gets sanity-check re-invocations at T+TTL/4, T/2, 3T/4. VLM can refine, revoke, or silent-pass.
- **AttentionMode amplifier (B3):** AttentionMode-flagged areas use a much shorter sanity-check interval (e.g. 60 s) regardless of policy TTL.

---

## Dispatcher: the policy engine

The only component that calls HA services. Maps VLM `recommendations` → HA actions based on:

- Confidence threshold per action class (announce > 0.6, lock door > 0.95, …)
- Time-of-day rules (never auto-lock 6am–10pm)
- Redundancy checks (require two consecutive recommendations before auto-acting)
- User trust level (new install = recommend-only; trusted install = auto-act on common patterns)
- AttentionMode flag (life-safety fast paths)

Even for AttentionMode tier_3 (fall detected): VLM emits recommendations, dispatcher executes announce+push IMMEDIATELY (reversible, low-risk) but stages emergency contact with a visible cancellation countdown. Separation of probabilistic reasoning from deterministic action.

Owns the audit trail: every HA service call is logged with chain `Event → VLMDecision → recommendation → dispatcher rule → action`.

---

## Three feedback loops

### Loop 1: User FP/FN → memory edge weights

Five signal sources:

| Signal | Capture |
|---|---|
| Explicit alert feedback | Per-alert strip on Recent alerts + HA Companion notification actions (✓ / ✗ / ⤴) |
| Behavioral | Rapid dismiss = FP signal; mute-camera = systemic FP |
| Outcome | Recommendation acted-on vs ignored; later HA event reveals miss |
| Post-hoc review | Daily/weekly digest UI: grade summarized clusters |
| Cross-validation conflict | AttentionMode flagged + user cancelled |

All write `(Alert)-[CORRECTED_BY]->(UserFeedback)` edges. Dispatcher walks `(VLMDecision)-[CITED]->(Memory)` and adjusts INFLUENCED edge weights based on user verdict:

- `good_catch` → cited edges strengthen
- `false_alarm` → cited edges weaken on this outcome class
- `escalation_FN` → dismissal policy TTL reduced or match narrowed
- post-hoc FN → policy that short-circuited the VLM gets penalized

Two resolution paths:

- **Auto-resolve**: recurring patterns get automatic adjustments (3+ FPs with same citation set → narrow the dismissal policy match)
- **Human-resolve**: novel classes, conflicting signals, hallucinated reasoning → surfaces to dev loop dashboard

### Loop 2: VLM upstream quality issues → preprocessor tuning

When VLM can't complete an assessment because the input is degraded (low light, motion blur, occlusion, out of focus, color cast), it emits `upstream_quality_issues`. **VLM does NOT propose specific knobs** — leaky abstraction. A preprocessor tuner maps (issue + camera context + historical effectiveness) → KnobAdjustment.

Each adjustment captured as `(VLMDecision)-[REPORTED_ISSUE]->(QualityIssue)-[TRIGGERED_TUNE]->(KnobAdjustment)-[APPLIED_TO]->(Camera)`, with `[EFFECTIVENESS_OBSERVED]` tracking. Same Hebbian dynamics as memory edges.

Auto-resolve: known issue + proven mapping. Human-resolve: novel issue or repeated failures → dev loop dashboard.

**Cross-loop interactions** can occur: e.g. preprocessor tuning didn't help enough + user FN on same incident = dismissal threshold ALSO needs to tighten. The dev loop dashboard surfaces these for joint resolution.

### Loop 3: Eval corpus growth ← bad responses

When production VLM responses fail validation, hallucinate citations, or get corrected by user feedback, the (context, response, correction) tuple is added to the eval corpus at `services/vlm-router/tests/eval_corpus/`. The corpus drives:

- Snapshot regression across backends on every prompt change
- Cross-backend diff (Ollama vs cloud — surface where they disagree)
- "Replay this alert" in Web UI (re-run with current or experimental prompts)
- The VLM debugger surface for manual prompt iteration

**The eval corpus is the long-term IP** — months of accumulated regression cases documenting "behavior SentiHome must preserve."

---

## Failure mode posture

Prompt engineering + dev loop is the primary defense. **Do NOT build retry chains, fallback model cascades, or runtime smoothing of bad outputs** — those mask root causes.

Minimal runtime safeguards only:

1. **Pydantic response validation** rejects malformed output; failed response → added to eval corpus, dev loop iterates.
2. **VLM router backend failover** when a backend is unreachable (service availability isn't prompt-engineerable).
3. **Iteration cap = 3 re-invocations per trigger** to bound runaway `request_additional_context` loops.

Everything else lives in the dev loop. The eval corpus is the long-term defense.

---

## Model picks

All models run on the inference box (NVIDIA 4090 + high-core-count host). **Never on the HA Yellow** — Yellow stays orchestration-only.

| Task | Model | Notes |
|---|---|---|
| Object detection | **YOLO11x** (Ultralytics) | 53.6 mAP COCO; covers person + dog + cat + cars + trucks + bicycles + motorcycles. 50+ fps on 4090. |
| Face detect + align | **RetinaFace-R50** (InsightFace `buffalo_l` bundle) | Better recall on partial / non-frontal faces than mobile-tier alternatives. |
| Face recognition | **ArcFace ResNet100** (InsightFace `buffalo_l`) | 512-dim embeddings. ~99.83% LFW; gap from MobileFaceNet shows up in real conditions (lighting, hats, angles). |
| Vehicle ReID | **DINOv2 base** (Meta) + cosine match in Neo4j vector index | General-purpose visual features that just work for "is this the same vehicle." No vehicle-specific training needed. |
| License plate | **fastALPR** (YOLOv8 plate detector + lightweight OCR) | Maintained, GPU-accelerated, supports US + EU + intl. Fallback: PaddleOCR plate model. |
| Pet ID (per-pet, beyond dog/cat class) | **DINOv2 embeddings** + KnownPet centroid match | Skips needing a fine-tuned per-pet model; tradeoff is accuracy vs. a custom fine-tune. Adequate for v1. |

All ONNX-exportable; all run cleanly on CUDA via ONNXRuntime-GPU or PyTorch. InsightFace + Ultralytics + Meta's DINOv2 are all well-maintained.

---

## Phasing

Phases below assume the inference box is being rebuilt (Agent DVR's host is currently offline). **Phases 10.0 and 10.1 are doable now on Yellow alone with a mock preprocessor backend.** Real models wait for the inference box.

| Phase | Where it runs | Blocked by inference box? |
|---|---|---|
| **10.0 — this planning doc** | none (writing) | No (done) |
| **10.1 — protocol + types + mock preprocessor backend + producer wiring** | Yellow only. Mock backend returns deterministic dummy detections. Wires the full alert path end-to-end with empty memory + stub VLM responses. | **No** |
| **10.2 — Neo4j stand-up + memory schema + audit edges** | Yellow can talk to Neo4j on any host; ideally on inference box, but acceptable on Yellow as a placeholder. Schema setup + Cypher migrations. Begin writing audit edges (CITED, INFLUENCED) even with stub VLM. | **No** (Neo4j Community can run anywhere) |
| **10.3 — preprocessor service scaffold + YOLO11x object detection** | Inference box. Continuous service with /window endpoint, in-memory ring buffer, YOLO11x stage only. | **Yes** |
| **10.4 — InsightFace face pipeline + KnownActor enrollment UI** | Inference box. Adds face stage to preprocessor; new enrollment surface in Web UI: "label this unknown face as ..." | **Yes** |
| **10.5 — vehicle (DINOv2) + plate (fastALPR) + pet ID** | Inference box. Three more pipeline stages. | **Yes** |
| **10.6 — VLM router + first real VLM backend (local Ollama Qwen2.5-VL)** | Inference box hosts Ollama. Replaces stub VLM. First real triage assembly + dismissal policy authoring. | **Yes** |
| **10.7 — Dispatcher policy engine + recommendations execution** | ha-agent (Yellow). Pure code; doesn't need inference box once 10.6 lands. | **No (but blocked by 10.6)** |
| **10.8 — Feedback loop 1: user FP/FN UI + edge weight updates** | ha-agent + Neo4j. Per-alert feedback UI, HA Companion action buttons, dispatcher walks citations on feedback. | **No (but blocked by 10.6, 10.7)** |
| **10.9 — Feedback loop 2: preprocessor tuner + KnobAdjustment graph** | ha-agent (tuner) + preprocessor (apply tune endpoint). | **Yes** |
| **10.10 — Eval corpus + dev loop dashboard** | ha-agent + Web UI. VLM debugger surface, replay tool, cross-backend diff. | **Yes** |
| **10.11 — agent_dvr_native preprocessor backend** | When AD is back: read AD's native detections to avoid double-processing where adequate. | **Yes** + Agent DVR running |

---

## Open questions / deferred

- **AttentionMode** — full design deferred. Architectural seat reserved: trigger interface accepts non-motion sources (timer tick, external observer); area memory carries `attention_mode` flag. Detailed design happens when we approach the pool-cam/life-safety scenarios.
- **Conversational setup wizard** — per earlier user direction, deferred to a separate later epic.
- **Pruning + memory compression** — principle locked (edge-weight reinforcement + decay; Hebbian dynamics). Detailed tuning of decay functions, compression heuristics, and pruning thresholds deferred until we have months of audit data.
- **Agent framework decision (CrewAI vs LangGraph vs custom async)** — not needed for the current design. All "agent loop" behavior is replaced by multi-call iteration via structured output. Revisit only if requirements push us back toward in-call tool use.
- **Per-pet ID accuracy** — DINOv2 embeddings are the v1 mechanism. If accuracy is inadequate, future epic to fine-tune a per-pet model.
- **Cross-household memory sharing** (e.g. neighborhood watch patterns) — out of scope.

---

## Cross-references requiring update

This design overrides or extends the following canonical docs in `docs/architecture/`. Update tasks tracked separately:

- **§04 (Model router & inference)** — add `recognition_router` is OUT; replaced by preprocessor service contract. `vlm_router` unchanged in principle, gains backend reliability tracking driven by hallucination signals.
- **§08 (Detection pipeline)** — replace with the preprocessor service design above. Move continuous-pipeline + buffering responsibilities here.
- **§09 (VLM prompt contract)** — replace with the structured I/O schema above (findings, tier, authored_policies, recommendations, citations, upstream_quality_issues, request_additional_context).
- **§10.5 (Feedback-driven rule optimization)** — extend with the three feedback loops + dev loop dashboard.
- **§11 (Memory model)** — major rewrite: storage backing changes from "SQL + vector DB" to Neo4j hybrid; five memory layers preserved but reframed around graph nodes/edges; new edge taxonomy.
- **§12 (Recognition & identity)** — face pipeline section largely correct; integrate the preprocessor-service framing + DINOv2 for vehicle/pet.
- **§12.5 (Dynamic identity refinement)** — extend with edge-weight dynamics + Hebbian reinforcement framing.
- **§17 (Observability)** — add audit-log-from-day-one requirement + dev loop dashboard + trust metrics per camera + per VLM backend.
- **§19 (Failure modes)** — replace with the minimal-runtime-safeguards + dev-loop-as-primary-defense posture.

---

## Issues

Replacing the prior issue stub. Issues are grouped by phase; labels include `epic:identity-recognition`, `component:*`, `priority:p1|p2`.

### Phase 10.0 — planning
- **doc: this planning doc + cross-ref update list** ✓ (this commit)

### Phase 10.1 — protocol + mock backend
- **feat(types): services/recognition shared types package** (Detection, Identity, Match, Embedding, Frame, WindowResponse, VLMResponse, Policy, CitationList, …)
- **feat(preprocessor): mock backend service skeleton + /window endpoint with deterministic mock detections**
- **feat(ha-agent): preprocessor_client + window query call wired into the alert path on each motion event**
- **feat(ha-agent): stub VLM response generation (deterministic) wiring full alert path end-to-end with mocks**

### Phase 10.2 — Neo4j + audit edges
- **infra: Neo4j Community sidecar Docker compose snippet + bootstrap migrations (schema, indexes, vector index)**
- **feat(memory): Cypher-driven graph client in shared package + Neo4j connection in topology config**
- **feat(memory): node + edge schema migrations (the taxonomies above)**
- **feat(memory): audit-edge writers (CITED, INFLUENCED, YIELDED) — invoked from triage even with stub VLM**
- **test: schema + audit-edge integration tests**

### Phase 10.3 — preprocessor + YOLO11x
- **infra: preprocessor Dockerfile (nvidia/cuda base; ONNX-Runtime-GPU; Ultralytics)**
- **feat(preprocessor): RTSP source backend (degenerate fallback)**
- **feat(preprocessor): YOLO11x object detection pipeline stage**
- **feat(preprocessor): in-memory ring buffer + disk archive with JSON sidecars**
- **feat(preprocessor): /window endpoint returns real preprocessor output**
- **test: per-camera latency + throughput baselines on a 4090 host**

### Phase 10.4 — face + KnownActor + enrollment UI
- **feat(preprocessor): SCRFD detect+align + ArcFace R100 embed pipeline stages**
- **feat(memory): KnownActor enrollment via Web UI (label unknown faces from recent alerts)**
- **feat(memory): face embedding vector index + match-on-embed in preprocessor**
- **feat(ha-agent): enrollment surface (UI)**
- **test: face match quality across mock + real frames**

### Phase 10.5 — vehicle + plate + pet
- **feat(preprocessor): DINOv2 embed stage for vehicle + pet crops**
- **feat(preprocessor): fastALPR plate detect+OCR stage**
- **feat(memory): KnownVehicle + KnownPet enrollment surfaces**
- **feat(memory): per-class match thresholds (vehicle vs pet vs face)**

### Phase 10.6 — VLM router + first real VLM
- **infra: Ollama sidecar on inference box hosting Qwen2.5-VL 7B**
- **feat(vlm-router): backend kind + capability advertisement + reliability tracking**
- **feat(ha-agent): triage assembles full context (template + RAG top-K)**
- **feat(ha-agent): VLM invocation with structured response parsing**
- **feat(ha-agent): dismissal-policy authoring + storage + match-check**
- **feat(ha-agent): multi-call iteration support (request_additional_context)**
- **feat(ha-agent): citation parsing + INFLUENCED edge writes**

### Phase 10.7 — dispatcher
- **feat(dispatcher): policy engine for recommendation → action mapping**
- **feat(dispatcher): per-action-class confidence thresholds + time-of-day rules**
- **feat(dispatcher): user trust level + AttentionMode flag awareness**
- **feat(dispatcher): action audit trail**

### Phase 10.8 — feedback loop 1
- **feat(ha-agent): per-alert FP/FN feedback strip on Recent alerts**
- **feat(notify): HA Companion notification action buttons (✓ / ✗ / ⤴)**
- **feat(ha-agent): "Review activity" time-range UI for post-hoc FN tagging**
- **feat(dispatcher): citation walk + INFLUENCED weight update on feedback**
- **feat(dispatcher): trust metrics per camera + per VLM backend**

### Phase 10.9 — feedback loop 2
- **feat(ha-agent): preprocessor tuner — maps QualityIssue → KnobAdjustment**
- **feat(preprocessor): /tune endpoint applies KnobAdjustment to camera profile**
- **feat(preprocessor): per-camera adaptive profile + effectiveness tracking**
- **feat(memory): KnobAdjustment + EFFECTIVENESS_OBSERVED edge writers**

### Phase 10.10 — eval corpus + dev loop dashboard
- **infra: services/vlm-router/tests/eval_corpus/ as JSONL**
- **feat(vlm-router): snapshot regression runner across backends**
- **feat(ha-agent): "Replay this alert" with current/experimental prompts**
- **feat(ha-agent): VLM debugger Web UI surface**
- **feat(ha-agent): dev loop dashboard — unresolved feedback queue + cross-loop conflict surfacing**
- **feat(vlm-router): audit-driven corpus growth (failed responses + user-corrected → auto-add)**

### Phase 10.11 — Agent DVR native backend
- **feat(preprocessor): agent_dvr_native backend kind (pull AD's native detections)**
- **feat(preprocessor): per-camera routing (some cams via AD-native, others via passthrough)**
- **doc: Agent DVR setup guide for SentiHome integration**

---

## Definition of done

The epic is "done" when, on a normal household day:

- Every camera motion event writes a lightweight Event node to memory within 200 ms of HA's `last_changed`
- Each VLM-invoked event has structured findings + tier + citations + (optional) authored policies, persisted with full audit chain
- "Boring" patterns (e.g. known dogs in backyard) dismiss after the first VLM call, costing ~0 VLM calls per subsequent occurrence
- User FP/FN feedback (one tap from a push notification) measurably adjusts memory edge weights within the next reasoning cycle
- A VLM-reported upstream quality issue (e.g. "low light, couldn't ID face") triggers a preprocessor knob adjustment, and the next event under similar conditions shows improved quality (tracked in graph)
- The dev loop dashboard shows the queue of unresolved cases needing human attention, growing slowly (system is auto-resolving most things)
- Trust metrics per camera trend toward stable values (FP rate flattens within weeks)

The system is observably learning from its mistakes and getting cheaper to run over time, without sacrificing recall on the events that matter.
