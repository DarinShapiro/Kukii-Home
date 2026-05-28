# 11 — Memory Model

**Purpose:** What the system remembers, where, for how long, and how the pieces relate. Memory is the spine of the system — almost every scenario reads from or writes to it in a non-trivial way.
**Status:** drafting
**Last updated 2026-05-27 (Epic 10 design propagation)**

---

## Status (2026-05-27): Neo4j hybrid graph + vector substrate (Epic 10)

Epic 10 (`planning/epics/10-identity-recognition.md`) locks in **Neo4j 5.x as the single hybrid graph + vector store** for all memory layers below. This replaces the "SQL + vector DB" split that was sketched in this doc. Postgres demotes to OLTP-only (HA config, alert log, audit log, structured outputs); Qdrant retires.

Key consequences for sections below:

- **Five memory layers are preserved as a conceptual framing**, but they live as node-type taxonomies on a single graph (`Event`, `KnownActor`, `KnownVehicle`, `KnownPet`, `VLMDecision`, `Policy`, `UserFeedback`, `QualityIssue`, …). Embeddings are node properties indexed by Neo4j's native vector index (5.13+).
- **Edges carry the memory dynamics.** Reinforcement and decay live on edge weights (Mnemosyne reverse-sigmoid decay with floor `d=0.05`, sigmoidal habituation `Δ_max=0.2`, `t_crit=3600s`). The previously-planned "retention scoring" collapses to tuning the edge-weight reinforcement + decay functions. See Epic 10 §"Edge-weight dynamics" and §"Compression" for normative functional forms.
- **Session memory dissolves.** Epic 10 removes the session object outright ("memory IS the substrate — no session object, no lifecycle to manage"). The `Session memory` section below is **superseded**; cross-camera correlation now expresses itself as `CORRELATES_WITH` edges between `Event` nodes.
- **Episodic / identity / semantic memory** sections below are largely accurate as concepts; the storage backing changes from "SQL + vector DB" to Neo4j nodes + edges. The five-layer framing is augmented by **three tiers** (Tier 1 Events/Visits, Tier 2 Canonical Incident Paths via FSM, Tier 3 Authored Policies + Emergent Rules) with REINFORCE-on-reward-gap inter-tier edge updates.
- **Authored dismissal policies** (VLM-emitted, content-based, tag-set-scoped, TTL'd, sanity-check re-invocations) live as `Policy` nodes — see Epic 10 §"Authoring dismissal policies".
- **RAG composes** persistent Hebbian weights with per-query weights: `w_final = w_persistent · w_query`, traversed via Dynamic Weighted PageRank (MemORAI). Implementable as a single Cypher query against Neo4j 5.x vector indexes.

The schema in Epic 10 (Node taxonomy + Edge taxonomy tables) is canonical for storage; the layer descriptions below remain useful as a reasoning model.

---

## Memory layers overview

Five distinct conceptual layers, each with a different lifetime, purpose, and reading shape. All five back onto the same Neo4j graph (per Epic 10 status note above); the "Store" lines below describe the **pre-Epic 10** plan and are kept for context — actual storage is Neo4j 5.x with native vector indexes.

```
┌─ Working memory ──────────────────────────────────────────┐
│  What's in the agent's context for one reasoning run.     │
│  Assembled fresh per event from all layers below.         │
│  Lifetime: one agent run. Store: in-prompt only.          │
└───────────────────────────────────────────────────────────┘

┌─ Session memory ── (superseded — see Epic 10) ────────────┐
│  Pre-Epic-10: in-flight journey object built              │
│  incrementally as new segments arrive.                    │
│  Epic 10: session object eliminated; cross-camera         │
│  correlation expressed as CORRELATES_WITH edges between   │
│  Event nodes; memory itself is the persistent substrate.  │
└───────────────────────────────────────────────────────────┘

┌─ Episodic memory ─────────────────────────────────────────┐
│  Filed records of closed sessions and notable events.     │
│  The curated, queryable history of what happened.         │
│  Lifetime: weeks–indefinite (policy-governed).            │
│  Store: Neo4j nodes (Event, VLMDecision, Alert, …) with   │
│  embedding properties + vector index for semantic recall. │
└───────────────────────────────────────────────────────────┘

┌─ Identity memory ─────────────────────────────────────────┐
│  Who is known: residents, visitors, service workers,      │
│  pets, vehicles. Access profiles and learned behavior.    │
│  Lifetime: indefinite for household; policy for others.   │
│  Store: Neo4j nodes (KnownActor, KnownVehicle, KnownPet)  │
│  with embedding properties + vector index.                │
└───────────────────────────────────────────────────────────┘

┌─ Semantic memory ─────────────────────────────────────────┐
│  Rules, situational contexts, transient intents,          │
│  home layout knowledge. Forward-looking and normative.    │
│  Lifetime: indefinite (rules); bounded (contexts/intents).│
│  Store: Neo4j nodes (Policy, SituationalContext,          │
│  TransientIntent, Area) + relationships.                  │
└───────────────────────────────────────────────────────────┘
```

---

## Working memory

Assembled fresh for every agent reasoning run. The goal is to give the reasoner exactly the context it needs without blowing the prompt budget.

**Assembly stack (in priority/injection order):**

```
1. Active TransientIntents        ← user just asked for something specific
2. Active SituationalContexts     ← current world state reframes normal
3. Retrieved rules                ← top-K hybrid retrieval (see §10)
4. Subject identity candidates    ← top 2–3 with confidence + access profile
5. Relevant episodic memories     ← at most 2–3 similar past sessions, summarized
6. Current session state          ← structured JSON, not raw frames
7. World state snapshot           ← from HA: who's home, alarm, devices, time
```

Contexts and intents go before rules because they are the frame through which rules are interpreted, not just additional rules.

**Budget discipline:** each slot has a token budget. Episodic recall is the most dangerous for bloat — summaries only, never raw session transcripts. Session state is compressed to structured JSON before injection.

---

## Session memory

> **Status (2026-05-27): Superseded by Epic 10.** The Session object no longer exists as a first-class entity. Cross-camera correlation, which the Session object previously held in memory, now expresses itself as `CORRELATES_WITH` edges between `Event` nodes in the Neo4j graph (`strength`, `reason: same_actor | temporal_adjacency`). "Session-scoped reasoning" (journey scoring, segment correlation) is replaced by template-driven Cypher queries at triage time over recent events on this camera + adjacent cameras within a temporal window. The structure documented below is retained as historical context; the **re-ID + spatial-plausibility + recency correlation rules remain accurate** — they're just applied at query time over `Event` nodes rather than mutating an in-flight session.

Tracks a subject (or group) across cameras and time while the session is open. Defined in `design_notes.md`; repeated here for completeness.

```
Session:
  session_id, opened_at, last_seen_at, closed_at
  subject_descriptor: {
    reid_embedding, appearance_text,
    face_embedding?, vehicle_plate?,
    identity_resolution            ← see Identity resolution record below
  }
  segments: [{
    camera_id, area, t_start, t_end, clip_ref,
    vlm_scene_json, entry_direction, exit_direction,
    dwell_s, interactions
  }]
  journey_score: { suspicion, intent_hypotheses }
  attention_mode_active: bool     ← whether vigilance mode was triggered
  status: open | closed | escalated
```

**Correlation rules for appending a new segment:**

- Re-ID cosine similarity ≥ threshold, AND
- Spatial plausibility — adjacency graph confirms transit was possible in Δt, AND
- Recency window (~5 min)

Reject geometrically impossible matches even with high re-ID score.

**Two reasoning cadences:**

- _Incremental:_ each segment updates `journey_score`; alert when journey-scoped rules cross threshold
- _On close:_ silence timeout or known egress → full episodic write (see below)

---

## Episodic memory

The curated, queryable record of what has happened. Not a raw event log — that's a separate append-only store (time-series DB or object store). Episodic is the _significant_ subset.

### What triggers an episodic write

- Session closes with any rule having fired
- Session closes with `journey_score.suspicion` above threshold
- Reasoner explicitly sets `worth_remembering: true` (novelty signal)
- Scheduled report generation (daily digest pulls from episodic)
- Any `urgent_alert` or `notify` output, regardless of session state

Routine events (mail carrier at mailbox, resident arriving home, package drop) go to the raw event log only unless something unusual occurred.

### Episodic record schema

```
EpisodicRecord:
  id, created_at, session_id?
  summary_text               ← 2–4 sentence VLM/reasoner narrative
  summary_embedding          ← for semantic recall ("find similar past events")
  structured: {
    areas_visited: [],
    subjects: [{ identity_resolution, role, behavior_summary }],
    rules_fired: [],
    anomalies: [],
    outcome: logged | notified | alerted | actioned
  }
  temporal: { start, end, time_of_day, day_of_week, season }
  clips_ref: []              ← object store references
  novelty_score: 0.0–1.0    ← how different from recent similar events
  retention_class: household_member | known_visitor | unknown | pet | vehicle
```

### Two query paths into episodic memory

- **SQL (structured):** "How many times has a vehicle matching this plate appeared at the front between 22:00–02:00 in the last 30 days?" Fast, explainable, used for pattern detection and report generation.
- **Vector (semantic):** "Find sessions similar to this one" — ANN search over `summary_embedding`. Used for cross-day pattern detection (S5, S17) where identity is uncertain and behavioral similarity is the signal.

Both are used together. SQL filters the candidate set; vector ranks within it.

---

## Identity memory

### Gallery entry (base layer)

Raw biometric/recognition data only. Linked upward to KnownActor.

```
GalleryEntry:
  id, label (nullable), type: face | reid | plate | pet_face
  embeddings: [{ vector, model_version, captured_at }]
  capture_refs: []           ← source clips/frames
  confidence_tier: confirmed | tentative | candidate
  household_member: bool
  linked_actor_id: →KnownActor (nullable)
```

### KnownActor (semantic layer over gallery)

Carries meaning: who this person is, what relationship they have, where and when they're expected.

```
KnownActor:
  id, label: "Carlos (pool service)" | "mail carrier" | "Amazon driver"
  gallery_refs: [→GalleryEntry]
  relationship: household | regular_visitor | service | delivery | unknown_recurring

  access_profile: [{
    areas_allowed: [driveway, side_yard, backyard, pool_area],
    areas_flagged: [garage_interior, front_door_entry],
    time_windows: { days: [Thu], hours: "08:00–17:00" },
    seasonal: { months: [Apr–Oct] },
    expected_pattern: "transit front→side→back, dwell at pool 30–90min"
    notes: "bi-weekly pool service"
  }]

  visit_ledger: →VisitLedger   ← see below
  behavioral_profile: →BehavioralProfile
```

The access profile is injected into the reasoner's working memory alongside the scene. The VLM reasons about whether observed behavior fits — explicit `still_suspicious` lists are not needed; the VLM persona handles that.

**Context stacking over access profiles:**

- SituationalContext can temporarily expand an actor's allowed areas without modifying the standing profile
- A TransientIntent ("pool guy coming today, unscheduled") overrides the scheduled time window for that visit
- When the context/intent expires, the standing access profile applies again

### Identity resolution record

Identity is a probability, not a key. Every memory object that references a subject carries an explicit resolution record rather than assuming certainty.

```
IdentityResolution:
  resolved_id: "carlos" | null
  candidate_ids: [{ actor_id, confidence }]
  method: face | reid | plate | composite | behavioral
  resolution_confidence: 0.0–1.0
  asserted_by: model | user_label
```

Queries can filter by `resolved_id = X AND resolution_confidence > 0.7`, or do fuzzy recall using embedding similarity when identity is uncertain.

### BehavioralProfile (learned over time)

Machine-learned from episodic memory. Represents what the system has actually observed vs. what the access profile says.

```
BehavioralProfile:
  actor_id
  observed_arrival_window: { p10: "09:20", p90: "10:45" }  ← learned from 20 visits
  observed_dwell_minutes: { p10: 38, p90: 72 }
  observed_areas: { area_id: visit_count }
  vehicle_seen: [plate?, description?]
  last_N_visits: [{ date, duration, anomaly_flags }]
```

Deviations from the behavioral profile are a signal even within a valid access window. "Carlos is here 3 hours earlier than ever before" is worth noting even if Thursday 10am is technically allowed.

### Pet gallery

Pets are a distinct actor type with their own gallery (face/coat recognition) and access profile. Critical for S16 (escaped dog).

```
PetActor:
  id, name: "Rex", species: dog, breed: "Labrador"
  gallery_refs: [→GalleryEntry (pet_face)]
  home_areas: [backyard, interior]    ← where the pet should be
  alert_if_detected_in: [front_yard, street, side_yard_unaccompanied]
  last_known_location: { area, confirmed_at }    ← updated from detection
  accompanied_by: null | →KnownActor (resident walking dog = OK)
```

`last_known_location` is updated every time the pet is detected. Combined with HA gate/door sensors, a front-yard detection with no gate-open event = high-confidence escape alert.

---

## Semantic memory

### Rules

Covered in depth in `10-rule-schema-and-retrieval.md`. Rules live here in the semantic memory layer — they are normative ("this is what should happen") rather than historical.

### Situational Context

Temporal world knowledge that reshapes how all reasoning is done during an active window. Not a rule — a frame for interpreting rules and scenes.

```
SituationalContext:
  id, label: "Halloween trick-or-treat"
  source: user_asserted | calendar_derived | agent_proposed | learned
  active_window: { start, end }
    or { recurring: "Oct-31 17:00–21:00" }
  scope: { areas[], trigger_types[] }

  behavioral_expectations: [
    "groups of unknown children in costumes approaching front door is normal",
    "repeated door approaches by different groups throughout the evening is expected",
    "unknown faces at the front door are not individually suspicious tonight"
  ]

  learned_from: { episode_ids[], user_confirmations[] }
  recurrence: { annual: true, key_date: "Oct-31" }
  confidence: 0.0–1.0
```

**What it does NOT contain:** a `still_suspicious` list. The VLM persona reasons about anomalies within the stated context — prescribing exceptions would duplicate the model's judgment in a more brittle form.

**Learning lifecycle:**

- Year 1: user asserts context mid-event → system creates it, applies for rest of window
- Episodic memory records the context alongside all events that evening
- Year 2: calendar (HA) says Halloween → system finds matching episodic record → proposes context proactively in the morning
- Year 3+: context fires automatically, source graduates to `learned`, confidence increases

**Calendar priming:** HA calendar events are scanned N hours ahead. Known recurring contexts are proposed before they're needed, not after the first alert fires.

**Dismissal clustering:** if N alerts of the same type are dismissed with the same explanation within a short window, that's a context emergence signal — system proposes a SituationalContext rather than continuing to surface individual dismissals.

### Transient Intent

User-expressed, forward-looking, self-pruning watches. Created conversationally; lighter weight than rules.

```
TransientIntent:
  id, created_at, created_by
  natural_language: "notify me when Bob's car stops in front or parks in the driveway"
  structured: {
    trigger: presence,
    subject: { type: vehicle, identity_ref: "bob", plate: "ABC123" | null },
    areas: [front_street, driveway],
    condition: dwell > 30s
  }
  output: notify(user_id, channel, message_template)
  expires_at: created_at + default_ttl    ← 24h default, inferred from language
  fire_once: true
  fired_at: null | timestamp
  status: active | fired | expired | cancelled
```

**TTL inference from natural language:**

| Phrasing                         | Inferred TTL           |
| -------------------------------- | ---------------------- |
| "when Bob arrives"               | fire-once, 24h ceiling |
| "today" / no qualifier, daytime  | end of day             |
| "this week"                      | 7 days                 |
| "if the Amazon truck comes"      | fire-once, end of day  |
| "for the next 2 hours"           | explicit 2h            |
| "keep watching until I say stop" | until cancelled        |

**Confirmation:** when created, the system confirms what it understood and when it will expire — _"Got it — I'll watch for Bob's car in the driveway or out front. I'll stop watching tomorrow morning unless you tell me sooner."_

**Triage priority boost:** events matching an active TransientIntent jump to `vlm.normal` or `vlm.urgent` even if they would otherwise be `vlm.background`.

**Identity resolution at creation:** if the referenced subject ("Bob's car") is not in the gallery, the system says so: _"I don't have a plate for Bob on file — I'll flag any vehicle that stops out front. Want to add his plate now?"_

**Expiry handling:**

- Fire-once: expires immediately on first match, no further noise
- Expired unfired: surfaces in daily digest ("Watched for Bob's car — never arrived") or active notification if the original intent was high-priority
- Inverse intents ("don't bother me for the next hour") are also supported — suppression with an explicit expiry

---

## Visit ledger

A lightweight running tally per subject per area, separate from and cheaper than full episodic records. Designed for scenarios like S17 (repeated unanswered knocking) where the pattern spans days or weeks.

```
VisitLedger:
  subject_ref: →KnownActor | identity_resolution (for unknowns)
  area: front_door
  visits: [{
    ts, session_id,
    outcome: unanswered | answered | unknown,
    dismissed_by_user: bool,
    dismiss_note: "not interested"
  }]
  escalation_state: {
    level: 0–3,
    last_escalated_at,
    suppress_until    ← set when user dismisses with "not interested"
  }
```

**Escalation logic uses two dimensions:**

- _Frequency:_ 3 unanswered visits in one day = different severity than 3 across 3 weeks
- _Persistence:_ visits spread over weeks signal something different than a burst

A single user dismiss with context ("not interested, don't alert again") sets `suppress_until` rather than just dropping the count.

---

## Stores

> **Status (2026-05-27): Restructured by Epic 10.** Neo4j 5.x absorbs both the prior "Vector DB" and the memory-side of the prior "SQL" rows: embeddings live as properties on graph nodes with native vector indexes, and the structured/relational queries become Cypher traversals. Postgres is retained for OLTP-only (HA config snapshots, alert log, audit log, structured outputs). The "Vector DB" row's Qdrant footprint retires. The other three rows (object store, time-series log, in-memory cache) remain accurate.

| Store                                | What lives here                                                                                                                                                                                                                                                                        | Why                                                                     |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Neo4j 5.x (graph + native vector)    | Everything memory-shaped: KnownActor / KnownVehicle / KnownPet (with face / vehicle / DINOv2 embedding properties); Event, VLMDecision, Alert, Policy, UserFeedback, QualityIssue, KnobAdjustment nodes; rule embeddings; all relationship edges including CITED, INFLUENCED, YIELDED. | Hybrid graph + ANN in one Cypher query; edge weights = Hebbian dynamics |
| Postgres (OLTP only)                 | HA config snapshots, alert log, audit log, structured outputs, system metadata. **No memory data.**                                                                                                                                                                                    | Familiar transactional store for non-graph operational data             |
| Object store                         | Raw clips, annotated frames, montages, session stitches                                                                                                                                                                                                                                | Blob storage, cheap, content-addressed                                  |
| Preprocessor ring buffer (in-memory) | Last ~60s per camera of pre-analyzed frames + sidecar JSON                                                                                                                                                                                                                             | Sub-50ms response to `GET /window` from triage                          |
| Preprocessor disk archive            | Last ~10min per camera (originals + sidecars)                                                                                                                                                                                                                                          | Warm window for triage on motion events                                 |
| Time-series / append-only log        | Raw event stream (every trigger, every detection); HA event mirror                                                                                                                                                                                                                     | High-write, queryable by time range, retention-managed separately       |
| In-memory cache                      | Active SituationalContexts, active TransientIntents, active Policies (hot lookups)                                                                                                                                                                                                     | Sub-ms triage policy match                                              |

---

## Retention policy

Covered in depth in `16-privacy-and-governance.md`. Summary:

| Data class                              | Default retention                                    |
| --------------------------------------- | ---------------------------------------------------- |
| Household member embeddings             | Indefinite                                           |
| Known visitor / actor                   | Indefinite while relationship active; user-deletable |
| Unknown face embeddings                 | 30 days; promoted to indefinite if labeled           |
| Raw clips (all)                         | 14 days rolling (configurable)                       |
| Episodic records (structured + summary) | 1 year (configurable)                                |
| Raw event log                           | 90 days                                              |
| Transient intents (expired/fired)       | 30 days (for digest and audit)                       |
| Visit ledgers                           | Tied to subject retention class                      |

---

## Backup & disaster recovery

- Neo4j and Postgres: nightly snapshot to local NAS; weekly off-site (encrypted). Neo4j's `neo4j-admin database dump` is the canonical mechanism; embeddings are graph-node properties and travel with the dump.
- Object store: clips are re-creatable from Agent DVR (the VMS) continuous recording within retention window; annotated frames are derived — lower backup priority
- On restore: Neo4j dumps restore in a single command. Embeddings are not re-computable without source frames, so frame backup matters for embeddings older than the Agent DVR retention window.
