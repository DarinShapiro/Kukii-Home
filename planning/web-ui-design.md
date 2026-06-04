# Kukii-Home — Product Web UI Design Spec

**Status:** Living design document, grows by ratified section. Anchors the
add-on's web UI (ingress panel) and the implicit coordination with the HA
Companion / Lovelace mobile surfaces. Implementation tracking lives in epics
and the commit history; this doc captures **principles + IA + screen shapes**
so the UI converges instead of accreting.

**Refs:** `planning/epics/10-identity-recognition.md` (the system the UI sits
on top of), `services/preprocessor` (owns the store + frames + recognizer),
`services/ha-agent` (owns the add-on Web UI + alert flow),
`frontend/operator-dashboard` (greenfield React dashboard, not yet wired).

**How to read this:** the doc is organized as **ratified parts**, each one a
screen or subsystem we've designed end-to-end. Earlier parts ratified first;
later parts hang off the same principles. The cross-cutting principles
(§7, §7.5, §7.6) and shared vocabulary (capability matrix, external-dependency
triple) apply across all parts unless a part says otherwise.

| Part | Surface | Status |
|---|---|---|
| **I** | Identity & Review (Inbox, track detail, candidate confirm, merge/split) | Ratified + built (§1–9) |
| **II** | Per-camera detail + Authorized actions whitelist | Ratified + built (§10–16) |
| **III** | Home page (Needs Attention + Activity + Trace + audit chain) | Ratified + built (§17–24) |
| **IV** | Activity depth & filters | Ratified + built (placeholder §below) |
| **V** | Areas | Ratified + built (Iter 2.C) |
| **VI** | Intent — Preferences + Rules | Ratified + built (Iter 2.A + Task 9) |
| **VII** | Policies (dismissals + transient intents) | Ratified + built (Iter 2.D) |
| **VIII** | Diagnostics + dev loop | Ratified + built (Iter 2.E roll-up; audit-edge browser deferred) |
| **IX** | **Memory architecture — guidance vs evidence, unified /memory + /identities + /system** | **Ratified, not built** (§25–32) |
| **X** | **Conversational dispatcher — drawer + LLM placement + audit thread** | **Ratified, not built** (§33–41) |
| n/a | HA Companion / Lovelace surfaces | partial (§6 below, push only) |

---

# PART I — Identity & Review

## 1. The reframe this design rests on

Build #292 made every person the cameras see get embedded and persisted
**whether or not anyone is enrolled** (`track_embeddings` table; one 512-d
L2-normalized vector per tracked person-detection). Proven end-to-end on real
footage: a pool event yielded 30 body embeddings across 5 tracks; enrolling
"Alice" from one track's stored vectors and running `resolve_event` named her
across all her frames **with zero re-inference**.

So the central user act is **not** "capture a new person" — it's **"label a
track the system already stored,"** and the payoff (`resolve_event`) back-fills
every past *and* future appearance automatically. The UI is, at its heart, a
humane front-end to `resolve_event`.

Two consequences drive everything below:

1. **The store is the enrollment substrate.** Enrollment = select stored
   track(s) → average their embeddings → template. No separate capture step.
2. **Correction is first-class, not an edge case.** The same demo showed two
   *different* people (tracks active simultaneously) scoring **0.96 cosine** —
   single-modality body-ID over-merges. The UI must let users *split* and
   *merge*, and must warn before a merge that would fuse two co-active tracks.

---

## 1.5 Modalities in MVP scope — **gait + pet are IN**

Decision (2026-06-03): gait and pet are **MVP deliverables**, not later
extensions. That decision reaches *below* the UI — into the always-embed layer.
The **pipeline embed capability for all three MVP modalities is now built**
(M0); what remains is the gait Stage-2 worker (E4) and the UI. This section is
the honest accounting.

| modality | pipeline | embed built | embed shape | resolves to | MVP |
|---|---|---|---|---|---|
| body | BodyIdPipeline | ✅ | per-frame | person (`KnownActor`) | ✅ |
| **pet** | PetPipeline | ✅ | **per-frame** (DINOv2 crop) | **pet (`KnownPet`)** | ✅ |
| **gait** | GaitPipeline | ✅ | **per-track sequence** | person (`KnownActor`) | ✅ (worker E4 pending) |
| **face** | FacePipeline | ✅ | **per-frame** (head-region SCRFD+ArcFace) | person (`KnownActor`) | ✅ |
| body_shape | CCReIDPipeline | ❌ | per-frame | person | later |

**Face embed (added 2026-06-03):** `FacePipeline.embed()` — the durable anchor.
Detection scoped to the head region (top of the person box, native res, as in
`run`); the face inherits the person's `track_id`; the **largest** face in the
region wins when several land in it. Face only flows where a face is actually
visible (frontal-ish, not top-down) — absent faces just mean that track has no
face row that frame; body still embeds. Resolves via the `face` slice at the
ArcFace 0.5 threshold; folds into the live cache (`face_embedding`) like the
others. Wired in code (preprocessor) — needs no add-on change, since the
Review UI is modality-agnostic.

**Two wrinkles that aren't "copy body-ID":**

- **Gait is temporal.** It produces **one descriptor per track**, from a frame
  *sequence* (walking dynamics over ≥`min_frames`), not one vector per frame.
  So: a new `embed_sequence()` (the no-match analogue of `run_sequence`), a
  `TemporalEmbeddingPipeline` protocol, and worker code that builds per-track
  sequences and calls it once per track. In the store a gait track is **one
  `track_embeddings` row**, not N — which `resolve_event` already handles. Gait
  only fires on tracks with enough frames, so many short tracks get no gait.
- **Pets are a different subject type.** Body/face/gait name *people*; pet ID
  names a *pet* (`KnownPet`: species, owner). PetPipeline triggers on
  `{dog, cat}`, so the worker must embed animal tracks too (today it only
  collects person tracks), and the data model + UI need a **parallel pet lane**
  — a "This is Rex" flow distinct from "This is Alice."

**Relative cost (set expectations):** pet embed was cheap — same per-frame
pattern as body, just DINOv2 on dog/cat crops + a `KnownPet` target — and it's
done. Gait's *embed capability* is also done (a thin empty-corpus reuse of
`run_sequence`). **Gait's remaining cost is operational, not modelling:** the
Stage-2 worker (sequence-building + `gait_pending` queue, §8) and the *runtime*
segmentation compute, which the deferred-cascade design keeps off the hot path
and proportional to face/body failure. Sequencing in §8 front-loads pet and
treats the gait worker as its own milestone.

---

## 2. End-to-end flow (the spine)

```
                        ┌─────────────────────────────────────────────┐
   CAPTURE / ENRICH     │ events/<cam>/<eid>/frame_*.jpg  + event.json │  (on disk, inference box)
   (built)              │ DetectionStore: events, detections,          │
                        │                 track_embeddings  ◄── #292   │
                        └───────────────┬─────────────────────────────┘
                                        │
              resolve_event(store, eid, corpus)   ◄── built (in-memory return today)
                                        │
                        ┌───────────────▼─────────────────────────────┐
   RESOLVE              │ resolutions: (eid, track, subject, conf,     │  ◄── NEW (persist the
   (persist = NEW)      │              modality, method, ts, verdict)  │       loop's output)
                        └───────────────┬─────────────────────────────┘
                                        │
                ┌───────────────────────┼───────────────────────┐
                ▼                       ▼                       ▼
        ┌───────────────┐      ┌────────────────┐      ┌────────────────┐
   UI   │ Review/Inbox  │      │ People + Person│      │ Event/Track    │
        │ (label queue) │      │ timeline       │      │ explorer       │
        └──────┬────────┘      └───────┬────────┘      └────────────────┘
               │ label / confirm / split / merge
               ▼
        ┌──────────────────────────────────────────┐
   ENROLL│ subjects: id, kind, name, templates,     │  ◄── NEW (today ActorCache is
   (NEW) │          provenance(tracks), updated_ts  │       in-memory only — no home)
        └──────┬───────────────────────────────────┘
               │ publish ActorEnrollmentEvent (NATS, canonical)  ◄── existing path
               ▼
        corpus refresh ──► re-run resolve_event over affected scope ──► loop closes
```

**Live arm (the always-embed payoff):** when a *new* event finishes
enrichment, resolve it against the current corpus immediately → the alert push
can carry a tentative identity ("Looks like Alice"). Same `resolve_event`, no
new machinery.

---

## 3. Data layer

### 3.1 Already built
- `events`, `detections` (carry `frame_name` + normalized `bbox` + `track_id`
  — enough to crop a thumbnail), `track_embeddings` (`event_id, camera_id,
  track_id, frame_ts, modality, match_method, dim, embedding`).
- Frames on disk addressable; preprocessor already serves
  `GET /frames/{camera}/{ts}.jpg`.

### 3.2 New tables (proposed — same local-first SQLite store)

```
subjects                           -- people AND pets (KnownActor / KnownPet)
  subject_id      TEXT PK
  kind            TEXT             -- person | pet
  display_name    TEXT
  species         TEXT             -- pet only: dog | cat   (NULL for person)
  owner_id        TEXT             -- pet only: owning person subject_id
  created_ts      REAL
  updated_ts      REAL
  active          INTEGER          -- soft-delete / deactivate

subject_templates                  -- modality-agnostic by construction
  subject_id      TEXT
  modality        TEXT             -- body | pet | gait | face | body_shape
  dim             INTEGER
  embedding       BLOB             -- averaged + L2-normalized template
  source_track_n  INTEGER          -- how many tracks contributed (provenance)
  updated_ts      REAL
  PRIMARY KEY (subject_id, modality)

template_provenance                -- which stored tracks built a template (undo-able)
  subject_id, modality, event_id, track_id, frame_count, added_ts

resolutions                        -- persisted output of resolve_event
  id              INTEGER PK
  event_id, camera_id, track_id, frame_ts
  modality, match_method
  subject_id      TEXT             -- resolved identity (person OR pet)
  confidence      REAL
  verdict         TEXT             -- auto | confirmed | rejected | reassigned
  resolved_ts     REAL
  UNIQUE(event_id, track_id, frame_ts, modality)
```

**One `subjects` table, not separate person/pet tables.** `resolve_event` /
`ActorMatch` are already identity-generic (a `subject_id` string); a `kind`
column keeps people and pets on the same machinery while the UI routes them to
different lanes. Maps cleanly onto Epic 10's `KnownActor` / `KnownPet` nodes
when memory/Neo4j becomes canonical (§3.4).

**Modality granularity differs by source, on purpose.** Body/pet/face write
**one resolution row per frame** a track was embedded on; **gait writes one row
per track** (its embedding is one per-track descriptor). The UI collapses
per-frame rows for display (§9) — so body and gait already look the same to the
timeline (an appearance with a confidence), the difference is only in storage.

**Why persist resolutions (not just return them):** the timeline, the review
queue, and corrections all need a stable, queryable, *correctable* record.
`resolve_event` stays pure; a thin `persist_resolutions()` writes its output.

### 3.3 Derived concepts (computed, not stored)
- **Track summary** = group `track_embeddings`/`detections` by `(event_id,
  track_id)`: frame count, time span, peak-confidence frame (→ thumbnail),
  modalities present, current resolution (if any).
- **Unresolved track** = a track with embeddings but no `confirmed`/`auto`
  resolution above the unknown threshold.
- **Co-active tracks** = tracks whose time spans overlap → merge-guard input.

### 3.4 Open decision — canonical home for actor state
`ActorCache` is in-memory and rebuilds from NATS `ActorEnrollmentEvent`; the
canonical owner per Epic 10 is the **memory service / Neo4j `KnownActor`** (and
`KnownPet` for animals).
- **MVP (recommended):** persist `subjects`/`subject_templates` in the
  preprocessor SQLite store (co-located with embeddings + frames + recognizer;
  local-first; zero new infra). Still publish `ActorEnrollmentEvent` so the rest
  of the system learns the subject. **`ActorEnrollmentEvent` already carries
  `pet_dinov2_centroid` *and* `gait_embedding`, and the corpus already projects
  `pet` + `gait` slices** — so the enrollment + resolve path for both modalities
  needs *no new contract*; a pet is simply an `actor_id` whose only template is a
  pet centroid. The one addition is the `kind` discriminator so the UI can route
  it to the pet lane.
- **Promotion path:** make Neo4j `KnownActor`/`KnownPet` canonical once the
  memory service lands; the SQLite tables become a cache/projection. The API
  contract below is written so this swap doesn't touch the UI.

---

## 4. Service / API layer

**Placement:** extend the **preprocessor FastAPI app** (it already owns
`/frames`, `/frame_window`, `/status`, `/actors/enroll`). New surface under
`/identity/*`. The operator-dashboard calls it over LAN; ha-agent/dispatcher
may call the resolve + feedback endpoints too.

> Decoupling note: HA-side code must not import preprocessor internals (enforced
> by `test_no_ha_side_imports`). These are **HTTP** endpoints + shared Pydantic
> contracts in `kukiihome_shared.preprocessor`, so the boundary holds.

| Method & path | Purpose | Returns |
|---|---|---|
| `GET /identity/tracks` | Track queue. Filters: `status=unresolved\|review\|resolved`, **`kind=person\|pet`**, `camera`, `from`, `to`, `confidence_band`, `limit`. | `[TrackSummary]` |
| `GET /identity/tracks/{event_id}/{track_id}` | Track detail: per-frame dets, **modalities present (body/gait for people; pet for animals)**, current resolution, **top-k candidate subjects by cosine**, co-active tracks. | `TrackDetail` |
| `GET /identity/tracks/{event_id}/{track_id}/thumb.jpg` | Representative crop (peak-conf frame, cropped by bbox). `?frame=ts` for a specific one. | image/jpeg |
| `GET /identity/subjects` | Enrolled people + pets + modality coverage + appearance counts. Filter `kind`. | `[SubjectSummary]` |
| `POST /identity/subjects` | Create `{kind: person\|pet, display_name, species?, owner_id?}`. | `SubjectSummary` |
| `POST /identity/subjects/{id}/enroll` | Build/extend a template: `{modality, sources:[{event_id, track_id, frames?}]}`. Averages selected embeddings, renormalizes, writes template + provenance, publishes enrollment event, kicks a resolve sweep. **Gait sources are whole tracks** (the descriptor is per-track; `frames` is ignored for `modality=gait`). | `{template, resolved_summary}` |
| `POST /identity/resolve` | Run + persist resolve over `{scope: event_id \| {camera,from,to} \| "all"}`, optionally `{modalities}`. Idempotent. | `{matched, by_subject}` |
| `GET /identity/subjects/{id}/timeline` | Appearances for the person/pet view: `from`,`to`,`camera?`. | `[Appearance]` |
| `POST /identity/resolutions/{id}/feedback` | `{verdict: confirm\|reject\|reassign, subject_id?}`. Updates verdict, optionally re-curates template, writes feedback signal. | `Resolution` |
| `POST /identity/subjects/merge` | `{from_id, into_id}` — merge two labels (recompute templates per modality, repoint resolutions). Rejects cross-kind merges (person↔pet). | `SubjectSummary` |
| `POST /identity/tracks/{event_id}/{track_id}/reassign` | Split: move a track off a subject → new/other subject or back to unknown. | `TrackDetail` |

**New shared contracts** (`kukiihome_shared.preprocessor`): `TrackSummary`
(carries `kind` + per-modality embedding counts), `TrackDetail`,
`CandidateMatch`, `SubjectSummary` (carries `kind`, `species`, modality
coverage), `Appearance`, `Resolution`. `TrackEmbedding` (built) stays internal
to the store.

**Confidence bands** (single source of truth, server-side, tunable per camera
like other knobs):
- `≥ 0.85` → **auto** (resolution written, still correctable)
- `0.60–0.85` → **review** (lands in the Inbox for one-tap confirm)
- `< 0.60` → **unresolved** (shows as Unknown; never a silent match)

---

## 5. Screens (operator-dashboard, React — wireframe level)

### 5.1 Review / Inbox  — *home; the labeling engine*

The daily driver: unknown tracks to name + medium-confidence guesses to
confirm. Card grid, newest first.

```
┌ Review ──────────────────────────  [kind: all ▾][camera ▾][today ▾][band ▾] ┐
│                                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐                     │
│  │  [crop]  │  │  [crop]  │  │  [crop]  │  │  [crop]  │                      │
│  │👤pool14:31│ │👤pool14:31│ │🐕yard09:02│ │👤yard22:10│                    │
│  │ 19f body  │ │  8f body  │ │  6f pet   │ │ 12f body+ │                    │
│  │    +gait  │ │           │ │           │ │     gait  │                    │
│  │ ? Alice   │ │ Unknown   │ │ ? Rex     │ │ ? Bob     │                    │
│  │  0.78     │ │           │ │  0.74     │ │  0.71     │                     │
│  │ [✓][✗][⋯] │ │ [Label]   │ │ [✓][✗][⋯] │ │ [✓][✗][⋯] │                    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘                     │
│                                                                             │
│  ⚠ 2 person tracks here were active at the same time — review before merge. │
└───────────────────────────────────────────────────────────────────────────┘
```

- **Kind glyph + filter:** `👤` person, `🐕`/`🐈` pet. One queue, filterable;
  people and pets share the card/label machinery, only the label target differs.
- **Modality line** (`19f body +gait`, `6f pet`) shows what evidence backs the
  guess — body vs gait for people, pet for animals. Makes the multi-modal MVP
  legible at a glance.
- **Review cards** (0.60–0.85): tentative name + confidence + one-tap
  `✓ confirm` / `✗ reject` / `⋯ someone else`.
- **Unknown cards** (<0.60 or no candidate): `Label` → label dialog.
- **Co-active warning** surfaces the false-merge lesson at the queue level
  (person tracks only — two simultaneous animals are also two pets).

### 5.2 Track detail (drawer)

```
┌ Track 👤 · pool · 14:31:05–14:31:58 · 19 frames ────────────[ × ]┐
│  ◀ [f][f][f][f][f][f][f][f][f][f][f] ▶   (frame strip, scrub)      │
│                                                                    │
│  Modalities:  body ✓ (19f)   gait ✓ (1 seq · 19f ≥ 15 min)  face — │
│                                                                    │
│  Candidates (cosine, per modality):                                │
│    Alice   body 0.78 · gait 0.82   ███████░░   [Confirm]           │
│    Bob     body 0.55 · gait —      ████░░░░░                       │
│    + Label as someone new…                                        │
│                                                                    │
│  ⚠ Track 2 (14:31:10–14:31:42) overlaps this one — different       │
│     person likely. Don't merge.                                    │
│                                                                    │
│  [ Not a person ]            [ Confirm Alice ]  [ Label new ]      │
└────────────────────────────────────────────────────────────────────┘
```

Per-modality candidate columns make the multi-signal MVP concrete: when body
is ambiguous (the pool 0.96 case), gait can be the tie-breaker — and the user
sees *which* signal agreed. **Gait shows its gate** (`19f ≥ 15 min`); a short
track reads `gait — (8f < 15 min)` so it's clear why gait is absent, not
broken. A **pet** track detail is the same screen with `Modalities: pet ✓ (6f)`
and a `Not an animal` action — same machinery, pet target.

### 5.3 Label dialog

The dialog **keys off the track's kind** — person and pet variants:

```
 PERSON track                          PET track (🐕/🐈)
┌ Label this person ──────────[×]┐   ┌ Label this pet ────────────[×]┐
│ ○ Existing:  [ search… ▾ ]     │   │ ○ Existing:  [ search… ▾ ]     │
│ ● New:       [ Alice_______ ]  │   │ ● New:       [ Rex_________ ]  │
│                                │   │   Species: ◉ dog ○ cat         │
│ Enroll modalities (this track):│   │   Owner:   [ Alice ▾ ]         │
│   ☑ body (19f)  ☑ gait (1 seq) │   │                                │
│                                │   │ Enroll modalities:             │
│ Use frames: ◉ all  ○ pick best │   │   ☑ pet (6f)                   │
│                                │   │                                │
│ ⓘ ~14 tracks / 6 events may    │   │ ⓘ ~3 tracks / 2 events may     │
│   (re)resolve.                 │   │   (re)resolve.                 │
│              [Cancel] [Save]   │   │              [Cancel] [Save]   │
└────────────────────────────────┘   └────────────────────────────────┘
```

- **Multi-modality enrol in one action:** a person track carrying both body
  and gait enrolls *both* templates at once (checkboxes default to whatever the
  track has). Frame curation (optional, default all) guards against one bad crop
  poisoning the **per-frame** body average; it's greyed for **gait** (one
  per-track descriptor — nothing to curate).
- **Pet adds species + owner**, writing a `kind=pet` subject; everything
  downstream (resolve, timeline, corrections) is identical to a person.

### 5.4 People & Pets (subjects list)

```
┌ Directory ────────────────────[ People | Pets ]──────────[ + Add ] ┐
│  ┌─────────────┐ ┌─────────────┐ │ ┌─────────────┐ ┌─────────────┐ │
│  │👤 Alice     │ │👤 Bob       │ │ │🐕 Rex       │ │🐈 Mittens   │ │
│  │ body✓ gait✓ │ │ body✓ gait— │ │ │ pet✓        │ │ pet✓        │ │
│  │ face—       │ │ face—       │ │ │ owner Alice │ │ owner Bob   │ │
│  │ 142 seen    │ │ 31 seen     │ │ │ 88 seen     │ │ 12 seen     │ │
│  │ last 14:31  │ │ last 2d ago │ │ │ last 09:02  │ │ last 3d ago │ │
│  └─────────────┘ └─────────────┘ │ └─────────────┘ └─────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

People and pets in one directory, tab-split. **Modality coverage badges**
(`body✓ gait✓ face—` for people, `pet✓` for animals) make the multi-modal MVP
legible per subject — and show at a glance who'd benefit from a face/gait
enrol later. Pet cards carry **species + owner**.

### 5.5 Subject timeline — the payoff (person *or* pet)

```
┌ Alice ──────────────────── body✓ gait✓ face— · 142 appearances ┐
│  [ rename ] [ merge with… ] [ re-resolve ] [ deactivate ]       │
│                                                                 │
│  Cameras: ▣ pool  ▣ front_door  ▢ yard                          │
│  ───────────────────────────────────────────────────────────   │
│  Mon ── pool 07:12 ▮ᵇ  door 08:01 ▮▮ᵍ   pool 14:31 ▮▮▮ᵇᵍ        │
│  Tue ── door 08:03 ▮ᵇ                                           │
│  Wed ── pool 14:30 ▮▮ᵇ  yard 22:10 ▮ᵍ (0.64 ⚠ low)             │
│         click a mark → crop + event + confidence + [reassign]   │
└─────────────────────────────────────────────────────────────────┘
```

Marks carry a **modality superscript** (`ᵇ`body `ᵍ`gait `ᵖ`pet `ᶠ`face) so a
gait-only night sighting (face/body failed in the dark) is visibly *why* Alice
was placed there. Low-confidence appearances flagged; clicking a mark exposes
the crop + `reassign` (split) — corrections happen *in context*. **Pets use the
identical screen** — "everywhere Rex has been," cross-camera — the only
difference is `pet✓` coverage and a pet thumbnail.

### 5.6 Event / track explorer (trust + debug)

Per event: frames, every track, what each resolved to and **why** (cosine vs
threshold), enrichment lag. The "why did it decide that" surface the
architecture's observability section requires. Pairs with the existing
`query_detections.py` CLI.

---

## 6. HA-side surfaces (ha-cards / Companion push)

The fast path — correction without opening the dashboard.

- **Alert notification** carries the annotated frame + tentative ID, with
  actions: `[✓ Yes]` `[✗ No]` `[Someone else →]`. `✓/✗` hit
  `/identity/resolutions/{id}/feedback`; "Someone else" deep-links to the label
  dialog. Works the same for **pets** ("Rex in the backyard? ✓/✗") and for
  **gait-only** IDs at night ("Looks like Alice *by gait* — face wasn't
  visible"), where surfacing the modality sets honest confidence expectations.
  This is the one-tap FP/FN signal Epic 10.8 wants, sourced from the resolution
  instead of a bare alert.
- **Lovelace cards:** "Unknown people/pets today: N — Review" (deep-link to
  Inbox); a "Recently seen" strip mixing people + pets (avatar + last-seen, tap
  → subject timeline).

Division of labor: **HA = confirm/deny in the moment; dashboard = label,
curate, merge/split, investigate.**

---

## 7. Cross-cutting principles

- **Modality-agnostic everywhere.** A track may resolve via body today, face or
  gait tomorrow. Screens show *which* modality/method; the data model already
  carries it. Never hard-code "body" into UX or contracts.
- **Confidence-band routing** (§4) keeps the worst case (the 0.6–0.85 zone where
  the false-merge lived) in a human review lane rather than auto-applied.
- **Merge-guard.** Before any merge/confirm that would fuse tracks with
  *overlapping time spans*, warn hard — two co-active tracks are two people.
- **Provenance + reversibility.** Every template records the tracks that built
  it; every enroll/confirm/merge/split is undo-able. No silent, unexplainable
  state.
- **Local-first / privacy.** Crops, embeddings, templates never leave the
  inference box. Labeling is sensitive PII; the spec assumes on-LAN only, in
  line with the project's privacy posture.
- **Idempotent resolve.** Re-running `/identity/resolve` is always safe; the UI
  can re-resolve liberally after any template change.

---

## 7.5 Recognition decision model — small known gallery (design direction)

Captured 2026-06-03. The household changes the recognition math: the **known
set is tiny** (≈5–10 residents + a few regulars), even though the set of people
who *could* appear is open. Most recognition tuning targets open-world galleries
(is this one of millions); ours should exploit the closed-ish gallery.

**Principle: propose against the small known set *aggressively*, commit
*conservatively*.**

- **Rank + margin, not an absolute threshold.** Against 5 candidates the useful
  question isn't "is cosine ≥ 0.5" but "is the top candidate clearly ahead of
  the second?" A soft, distant face scoring 0.45→Alice / 0.18→everyone-else is a
  confident Alice by *margin*, though a fixed threshold would reject it. Resolve
  should emit the **top-K of the known set with similarities + the margin**, and
  decide on (floor × margin), with a *lower* floor than a big-gallery system
  could use.
- **Compounds with fusion + context priors.** Three individually-weak signals
  (body/face/gait) agreeing on the *same* one of five is strong — coincidental
  agreement on the wrong person out of five is unlikely. Time-of-day, camera,
  access profiles, and co-occurrence shrink the *effective* gallery further:
  P(identity | embedding, time, camera, recent), not P(identity | embedding).
- **Open-set guardrail (the safety boundary).** The gallery is small but not
  closed — couriers, strangers, an actual intruder. "Nearest of five" would
  force a stranger into "Alice," and since this recognizer can **short-circuit
  the VLM** for known-person-at-known-time, that means *auto-dismissing an
  intruder as a resident* — the dangerous FP. So a lone weak embedding may
  *propose* "tentatively Alice" (→ review band, VLM/human stays in the loop) but
  must **never silently promote an unknown to a confident known**. Low floor to
  propose; margin + corroboration (fusion / context / human) to commit.

**What it changes when built:**
- `resolve` returns **ranked candidates + margin**, not a single thresholded
  match; verdict = f(floor, margin, fusion).
- The **Review UI** shows ranked candidates even when auto-resolve abstains —
  "most likely Alice (0.45), then Bob (0.18) — confirm?" → one-tap labeling
  against a short list instead of typing. Pairs naturally with the track-detail
  view (§5.2): *here's the track animated, and here's who we think it is.*
- **Per-gallery calibration:** tighten the margin when two enrolled subjects
  look alike (measure inter-subject similarity at enroll); loosen it for a
  visually distinct household.

**Status:** the *ranking* half is **built (0.6.0)** — `IdentityStore.candidates`
ranks the enrolled set by best-across-modality cosine + margin, surfaced as
one-tap "Confirm" on the track-detail page; enrollment now *accumulates*
(frame-count-weighted centroid) so each confirm strengthens the template. The
*commit* half — open-set floor + margin gating in auto-resolve, per-gallery
calibration — is still a direction (resolve still uses fixed per-modality
thresholds).

---

## 7.6 Tracking & fragmentation — empirical finding (2026-06-03)

Tracker splinters (the 1-frame top-of-head tracks that clutter the review
queue) were investigated end-to-end on the real pool event. **Result: the
tracker is not the bottleneck — detection density (frame rate) is.**

Three tracker configs gave **byte-identical** track structure (5 tracks:
19/8/1/1/1):
1. **Motion-only** (Ultralytics BoT-SORT default).
2. **ReID, `auto` features** (detector backbone — weak, untrained for ReID).
3. **ReID, OSNet** (a *trained* person-ReID encoder, wired via
   `pipelines/osnet_reid.py` so the tracker and identity layer share a model) —
   even with the proximity gate fully open and appearance permissive.

Why nothing moved: the fragments are **isolated single-frame detections** at the
event boundaries (subject entering/leaving), with a 2–3s gap of *no detection*
before the main track forms. A tracker can only associate detections that
exist; there's nothing temporally adjacent to link them to. (Opening the
proximity gate to force appearance-only association also risks merging
*co-present look-alikes* — t1/t2 sat at 0.96 OSNet cosine — so the gate is a
feature, not a bug.)

**Conclusion + actions:**
- **Frame rate is the structural fix — and the decoupling is now built.**
  Tracking-fps and VLM-fps are separated: capture dense (lower
  `rtsp_capture_interval_seconds`, e.g. 0.2-0.5s) so detections are continuous
  and IoU association just works; the event recorder + enrich worker stay dense;
  and the `/frame_window` RPC thins to a keyframe budget (`vlm_window_max_frames`
  via `keyframes.select_keyframes` — evenly spaced, first+last preserved,
  detections kept whole) so dense capture never blows up VLM cost. Both default
  off (1 fps, no cap) — the operator enables the pair. The offline event
  recordings are ~1 fps; the live continuous pipeline runs denser, so this
  fragmentation is largely an artifact of the offline low-fps path.
- **Quality declutter** (§ in 0.6.x) is the right *current* defense — these
  boundary singletons are low-value noise.
- **OSNet ReID encoder is built + proven-to-engage** (`osnet_reid.py`,
  `--reid-model`, `botsort_osnet.yaml`) — opt-in, off by default. No payoff on
  sparse footage; the right tool for **dense footage + mid-track occlusions**
  later.

---

## 8. Build state & sequencing (for when we leave design)

**Built (Build #292):** `track_embeddings` + `EmbeddingRow`, `TrackEmbedding`
contract, `BodyIdPipeline.embed()`, `collect_embeddings`, `resolve_event`,
worker `--embed` (now with per-event tracking). Proven on real footage.

Because gait + pet are MVP (§1.5), the work splits into an **embed layer** (new,
below the UI — without it the UI has nothing to show for those modalities) and
the **UI layer**.

**Embed layer (pipeline capability — now built):**
- **E1 · pet embed** ✅ — `PetPipeline.embed()`, per-frame DINOv2 on dog/cat
  crops with empty corpus (direct analogue of `BodyIdPipeline.embed`). *Built.*
- **E2 · worker collects animals** ✅ — worker now hands **all tracked dets**
  (person + dog + cat) to `collect_embeddings`; each pipeline self-filters by
  `triggers_on`, so pet tracks embed + persist from the worker. *Built.*
- **E3 · gait temporal embed** ✅ — `TemporalEmbeddingPipeline` protocol +
  `GaitPipeline.embed_sequence()` (no-match analogue of `run_sequence`) +
  `collect_track_embeddings()`. *Built* (the pipeline capability; the worker
  that *drives* it is E4).
- **E4 · gait Stage-2 worker** ✅ — the worker now builds each person track's
  frame sequence across an event and runs the temporal pipeline(s) over it
  (`collect_track_embeddings`), persisting one gait row/track. Gated by config
  (no gait pipeline → no-op) + the pipeline's min-frames floor. *Built.* The
  capture-quality `gait_pending` gate (only gait what face/body missed) remains
  a live-path optimization; the offline worker gaits every track clearing
  min-frames.
- **E5 · thresholds** — confirm/tune `DEFAULT_RESOLVE_THRESHOLDS` for `pet` +
  `gait` on real footage (entries already exist).

### Gait processing model — two-stage cascade (resolved)

Gait is temporal (needs the whole track's frame sequence) and expensive
(per-frame segmentation + a 4096-d descriptor). So it does **not** run inline
with per-frame enrichment. Instead:

- **Stage 1 (cheap, every event):** detect + body + pet (+ face later), per
  frame. The existing `--embed` pass. For each **person** track it records
  whether the cheaper modalities *captured cleanly* — i.e. was a usable face /
  body embedding produced — and flags the track `gait_pending` if not.
- **Stage 2 (expensive, deferred, conditional):** a separate worker drains the
  `gait_pending` queue — builds each flagged track's frame sequence, calls
  `collect_track_embeddings`, persists one gait row/track. Off the alert's
  critical path; own cadence/device; batchable.

Two properties make this safe and cheap:

1. **The gate is on *capture*, not *match*.** "Inconclusive" = face/body
   couldn't even produce a usable vector for this track (turned away, occluded,
   distant) — a **corpus-independent** signal, so it works with zero actors
   enrolled (the always-embed case). This keys gait spend to image conditions,
   exactly where gait earns its keep, and avoids running it on every track.
2. **Defer the *compute*, never the *trace*.** Stage-2 may lag, but only within
   the **durable event store's retention** (the frames the worker reads) — not
   the 10-min live buffer. Once the gait embedding is persisted, *resolution*
   against any future enrollment is free forever. The trap to avoid: "compute
   gait whenever we eventually need it" — if that arrives after frames age out,
   it's gone. Gate the spend; don't assume infinite time to spend it.

Implementation mirrors the existing pending pattern: today `enriched_ts IS
NULL` = "needs Stage-1"; add a per-track `gait_pending` flag Stage-1 sets and
the gait worker clears, with the same `--lag`-style observability (a second
queue). This **de-risks** gait: M3 stops being "make gait fast enough for the
live path" and becomes "drain a background queue before frames expire" — a far
more forgiving target.

**UI layer (this spec's screens/data/API):**
1. `persist_resolutions()` + `resolutions`/`subjects`/`subject_templates` tables.
2. `/identity/*` API on the preprocessor app + shared contracts (subject `kind`).
3. Track-thumbnail cropping endpoint (frame_name + bbox → JPEG).
4. operator-dashboard screens (§5), Inbox first.
5. ha-cards push actions + Lovelace cards (§6).
6. Live-arm: auto-resolve on event-enriched + tentative-ID in the alert.

**Milestones (honest ordering — pet rides early, gait is its own milestone):**

| # | Milestone | Contains |
|---|---|---|
| M0 | **Embed layer** ✅ | E1 pet embed, E2 worker collects animals, E3 gait temporal-embed capability — *built + tested* |
| M1 | Foundation | UI-1, UI-2, UI-3 (persist + API + thumbnails), body+pet |
| M2 | **Inbox, people + pets** | UI-4 Inbox + label dialog → the demo, clickable, for **body *and* pet** |
| M3 | **Gait** | E4 Stage-2 worker + `gait_pending` queue + E5 tuning + gait in track-detail/timeline |
| M4 | Subject timeline | person + pet payoff view |
| M5 | Merge / split | correction + merge-guard |
| M6 | HA push | one-tap confirm/deny (incl. pet + gait-only) |
| M7 | Live-arm | auto-resolve new events + tentative-ID in alerts |

**Reality check:** the *embed capability* for body, pet, and gait is now all
built (M0) — pet rides the body pattern exactly; gait's `embed_sequence` is a
thin empty-corpus reuse. What's left for gait (M3) is the **Stage-2 worker**
(sequence-building + `gait_pending` queue) and **runtime compute tuning**, not
new modelling. Pet reaches the Inbox cheaply at M2 alongside body; gait stays
its own milestone — but now bounded by "drain a deferred queue," not "fit gait
on the hot path."

---

## 9. Open questions (carry into the next session)
- **Subject home:** commit to SQLite-MVP now (`subjects`/`subject_templates`)
  and promote to Neo4j `KnownActor`/`KnownPet` later, or wait for the memory
  service and build straight onto the graph? (Recommend the former — keeps the
  loop shippable.)
- **Track-level vs frame-level resolutions:** store per-frame (faithful, heavy)
  or collapse to one resolution per (event, track) with a frame count + conf
  distribution? (Lean: collapse for the UI, keep per-frame for debug. Note gait
  is *already* one-per-track, so collapsing makes body/gait uniform.)
- **Gait economics — RESOLVED (see §8 "Gait processing model"):** gait runs as
  a **deferred, conditional Stage-2 worker**, gated on *capture* failure of the
  cheaper modalities, bounded by event-store retention. This is the answer to
  "is gait affordable" — its cost is now proportional to how often face/body
  fail, and it never touches the alert's critical path.
- **Gait min-frames gate (remaining sub-knob):** what's the floor (the
  pipeline's `min_frames`) and is it per-camera? Sets how *often* gait
  contributes once Stage-2 picks a track — too high and gait rarely fires; too
  low and the descriptor is noise. Surface the gate in the UI (§5.2) either way.
- **Stage-1 "capture-clean" predicate:** the exact rule that flags a person
  track `gait_pending` — e.g. *no face embedding produced* AND *body crop quality
  below X*. Needs pinning on real footage (and it's where a quick compute bench
  feeds in: measure Stage-2 cost per flagged track to size the queue drain).
- **Pet owner linkage:** is `owner_id` required at enrol or optional/after-the
  -fact? (Lean optional — naming the pet shouldn't block on knowing the owner.)
- **Cross-kind safety:** enforce person↔pet separation in merge + resolve (a pet
  centroid can't match a person template — different modality — but the `kind`
  guard makes it explicit and prevents UI mistakes).
- **Auto-enroll suggestion:** should the Inbox proactively cluster unknown
  tracks ("these 4 unknowns look like the same new subject — name them once")?
  Powerful; with gait + pet in the MVP the clustering is multi-modal, so defer
  until those signals are tuned (E5) before trusting auto-clusters.
- **Threshold ownership:** per-camera bands via the existing `/tune` knob path,
  or a dedicated identity-settings screen?
```

## 7.7 Action taxonomy — agent vs HA, four classes (design direction)

Ratified 2026-06-04. The architectural principle behind every action surface
in the product. Resolves the *"why does ha-agent emit events to HA but also
call HA services directly?"* question in one frame.

> **The agent perceives, reasons, and reports. HA acts on top of structured
> outcome events. But the agent ALSO takes direct actions for its own
> perception (transient adjustments to see better) and for protective
> responses tightly coupled to the reasoning context (lock the door when it
> reasons "intrusion") — both within a per-camera whitelist + policy.**

There are **four** action classes, distinguished by *who decides*, *what
mechanism executes*, and *what lifecycle*:

| # | Class | Decided by | Executed via | Lifecycle |
|---|---|---|---|---|
| **1** | **Reasoning state** | The agent's own internal pipeline | ha-agent internal DB writes | Persistent, never leaves the agent |
| **2** | **Perception actions** | The VLM, mid-reasoning, in its structured output (`perception_requests`) | Direct HA service call + camera API, executed by the dispatcher | **Temporary** — applied, perceived-with, **reverted** after assessment |
| **3** | **Protective / responsive actions** | The VLM's `recommendations`, gated by dispatcher policy | Direct HA service call (or camera API), executed by the dispatcher | **Persistent** — does NOT auto-revert; the user owns the undo |
| **4** | **Outcome notifications** | The reasoned alert itself | `kukiihome_alert` event + small status entities; HA automations branch on severity | Discrete event; HA owns delivery |

Classes 2 and 3 both make direct HA service calls but differ on **lifecycle**
(2 reverts, 3 persists), **purpose** (2 improves perception, 3 mitigates the
assessed situation), **whitelist scope** (2 is narrow — camera-adjacent
lights/PTZ; 3 is user-configured per-camera — locks, sirens, floods, …), and
**policy gating** (2 has none; 3 has Epic 10's full policy — per-action-class
confidence threshold, time-of-day rules, user-trust level, redundancy
checks).

### The intruder example, walked

Backyard cam spots an unknown person climbing over the fence:

1. **Class 1** — VLM call uses internal reasoning state: AttentionMode flag on
   backyard, KnownActor list (no match for this person), prior policies, RAG
   over past similar incidents.
2. **Class 2** — VLM emits `perception_requests: [floods on red, re-look at
   +2 s]`. Dispatcher executes via direct service call; reverts the flood
   adjustment after the assessment finalizes.
3. **Class 3** — VLM emits `recommendations: [lock back door, activate yard
   siren 15 s, floods on red sustained]`. Dispatcher applies policy
   (confidence ≥ 0.9 for lock, ≥ 0.85 for siren, etc.); executes the
   whitelisted ones; *does not* revert.
4. **Class 4** — Dispatcher emits a `kukiihome_alert` event with
   `severity=critical`, `scene_description=…`, and crucially
   `actions_taken: [lock.back_door, switch.yard_siren, light.backyard_floods]`
   so HA automations can render *"the agent already locked the door + sounded
   the siren"* in the notification body.

The same incident exercises all four classes. Class 1 is invisible to HA;
class 2 is transient and revert-tracked; class 3 is the agent acting on the
world; class 4 is the agent reporting what happened (and what it already
did). HA automations get the event and decide *delivery* (phone, Sonos,
which channels) — they don't decide *whether to lock the door*; the agent
did that.

### Whitelist + policy

Classes 2 and 3 are constrained by a per-camera whitelist + policy block
configured in the per-camera page (Part II §11 *Tuning* section, extended):

```
Per-camera authorized actions (whitelist + policy):
  Perception (class 2)
    light.backyard_floods    [allow]  max-duration 60s
    switch.backyard_ir_cut   [allow]
    PTZ + zoom               [allow]  on this cam (capability)
  Protective (class 3)
    lock.back_door           [allow]  if severity≥critical AND confidence≥0.95
                                       AND not (6am–10pm)
    switch.yard_siren        [allow]  if severity=critical  max-duration 30s
    light.backyard_floods    [allow]  if severity≥normal    max-duration 600s
```

The VLM can *recommend* whatever it wants; the dispatcher silently no-ops
anything outside the whitelist (logged in the trace as *"recommendation X
rejected: not in whitelist for this camera"*). Per-action-class policy is
the deterministic gate on top of the VLM's probabilistic recommendation.

### Composition with HA automations

The user's HA automations *can* fire on `kukiihome_alert` and take their
own actions on top of what the agent did. Two patterns are explicitly
supported:

- **"Always also":** *"on any critical alert, also start Frigate recording
  for the camera in question."* The agent's locks + sirens already fired;
  this is layered on top.
- **"Only when the agent didn't":** automations can read
  `actions_taken` and skip what's already done. Avoids double-firing
  scenes like *"if the agent didn't already turn on yard lights, do so."*

Most actions are **idempotent** (lock-already-locked is a no-op; light-on-
already-on is a no-op), so double-action is usually harmless. The
explicit awareness comes from `actions_taken` in the event payload.

### Implications across the doc

- **Part II § Tuning** picks up the per-camera whitelist + policy editor for
  classes 2 and 3.
- **Part III §22 trace page** renders both perception cycles (class 2) and
  protective actions (class 3) inline in the audit chain, with revert
  status for class 2 and a *"persisted — undo on the camera page"* tag
  for class 3.
- **Part VI Rules** (Iteration 1 Task 9) emits class-4 outcome events; rule
  matching is the most common path to a class-3/class-4 firing but not the
  only one (VLM emergent reasoning without a rule match can also produce
  recommendations).
- **Diagnostics (Part VIII)** carries the persistent action log: every class
  3 action ever taken, who/why/when, with the trace deep-link.

---

# PART II — Per-camera detail

Ratified 2026-06-04. The per-camera page is the dual of the activity stream:
the activity stream answers *"what happened on this camera"* (chronological,
filtered); per-camera detail answers ***"what is this camera, how does the
system treat it, is it healthy."*** Two sides of the same coin — different
shapes, different jobs, neither tries to be the other. The whole design rests
on three principles that apply well beyond this page.

## 10. Principles (load-bearing, apply broadly)

These three are the design's spine — they also constrain the home page, the
activity stream, and every later surface, so they're stated here once.

**P1 · Permissive at the capability layer; selective at the content layer.**
Don't pre-gate recognition by assumptions about what a camera *might* see.
Every model the system has, we try on every camera; the pipelines self-gate by
what they actually detect (face only embeds when SCRFD finds one). The UI
**never** offers per-camera "enable face / body / gait" toggles — those would
just be a way to silently refuse a face that did appear. Same shape as the
small-gallery direction (§7.5): propose against what the data actually
contains, commit conservatively based on confidence.

**P2 · Meet the camera where it is; emit a normalized EvidencePacket regardless.**
Cameras have wildly varying native AI. The system *discovers* what each offers,
*normalizes* into a consistent downstream interface via a capability matrix,
and *exposes the source-of-truth* per signal. Downstream consumers (rules,
VLM, identity, dispatcher) see the same shape whether `person` came from the
camera natively, from Agent DVR, or from our preprocessor.

**P3 · The system is embedded in a network it doesn't own — be a good citizen.**
We don't have full control authority over the devices it sits on top of. When
we have an API, we use it; when we don't, we delegate gracefully to the user
and treat external state as a *watched dependency*. Every external dependency
carries the same triple — **link-out, re-scan, drift surfaces to "needs
attention"** — and the same vocabulary, so users learn the pattern once.

## 11. Page content

It answers *"what is this camera, how does the system treat it, is it
healthy."* It explicitly does **not** answer *"what happened on this camera"*
(that's the activity stream, filtered by camera).

| Section | R/W | Contents |
|---|---|---|
| **At a glance** | R | Refreshable still, connection state, 24h event count |
| **Identity & role** | W | Friendly name, area, role, indoor/outdoor, public-facing flag |
| **Detection capability matrix** | R + override | Per-signal source-of-truth + delegate affordances (§12) |
| **Privacy posture** | W | Privacy zones, capture flags, retention overrides |
| **Tuning** | W | Per-camera thresholds (existing `/tune` knobs); tracker config (ReID + capture-fps from §7.6) |
| **Health** | R | Stream + decode/queue/drop metrics; FP rate trend (Loop 1); VLM-reported quality issues + tuner responses (Loop 2) |
| **Active policies** | R + revoke | Dismissals + TransientIntents scoped to this camera, with rationale + revoke (links forward to Part VI) |
| **Activity link out** | link | "N events today" → activity stream filtered to this camera |

```
┌ Pool cam ── [● connected · 12 events today] ──── [⋯] ┐
│  [ current still + bbox of last detection ]            │
│                                                        │
│  Identity & role                                       │
│    Area: Pool · Role: pool watch                       │
│    Outdoor · faces public: no                          │
│                                                        │
│  Detection capabilities & sources                      │
│    motion       NATIVE  (Dahua SMD)              ✓     │
│    person       AUGMENTED  (Dahua trigger →      ✓     │
│                  our YOLO classify)                    │
│    vehicle      SUBSTITUTED  (our YOLO)          ✓     │
│    dog/cat      SUBSTITUTED  (our YOLO)          ✓     │
│    package      MISSING — no source              ⚠     │
│      ↳ configured on the camera · [Open] [Re-scan]     │
│                                                        │
│  Privacy                                               │
│    2 privacy zones (edit)                              │
│                                                        │
│  Tuning                                                │
│    detection conf ≥ 0.45 · BoT-SORT @ 4fps · ReID off  │
│                                                        │
│  Health                                                │
│    Stream 100% · 0 drops/24h                           │
│    FP rate (7d): 2% ↓                                  │
│    1 quality issue: low light @ 22:14 → CLAHE applied  │
│                                                        │
│  Active policies                                       │
│    Dismiss {dog} on this cam · expires Wed 8pm [revoke]│
│                                                        │
│  Activity: 12 events today → see stream                │
└────────────────────────────────────────────────────────┘
```

## 12. Detection capability matrix — vocabulary

Five source-of-truth states per signal (motion, person, vehicle, pet, package,
line-cross, tamper, …):

- **NATIVE** — camera produces it, we pass it through
- **AUGMENTED** — camera produces it as a *trigger*, our pipeline enriches
- **SUBSTITUTED** — camera doesn't produce it, we run our own
- **DELEGATED** — Agent DVR / NVR produces it for us
- **MISSING** — nobody produces it; if "critical" → red on this page **and** on
  the home page's *Needs Attention*

Each row carries the **external-dependency triple** inline (P3):

```
person   AUGMENTED  (Dahua trigger → our YOLO)         ✓
         ↳ configured on the camera · [Open] [Re-scan]
```

**Criticality is narrow.** Motion is the only **mandatory** signal — without
a motion source, the camera has no event triggers and is invisible to the
agent. Person/vehicle/pet are *graded* — missing them disables specific alert
classes (rule scenarios that depend on `kind=person`, etc.), not the whole
camera. Face / plate are recognition layers downstream of detection and never
"critical" in this sense. Identity modalities (body / face / gait) are the
preprocessor's, not the camera's — they don't appear in the matrix at all.

## 13. Defaults & overrides

- The system **auto-chooses** defaults from the discovered capability profile —
  use NATIVE where the source is trusted, AUGMENT where native is a useful
  trigger, SUBSTITUTE where native is missing or unreliable.
- Per-signal **overrides** are available but rare — *"don't trust Reolink's
  person on this cam," "force-substitute," "ignore native motion under
  threshold X."* **Configure-by-exception, not configure-by-mapping.**
- The vast majority of cameras: the user touches *nothing* in the matrix; it
  surfaces what's discovered and what's chosen, and that's it.

## 14. The external-dependency pattern — generalized from per-camera

The same link-out / re-scan / drift triple applies to *every* dependency the
system has on something it doesn't own. The per-camera page is just the most
visible instance; future surfaces (HA integration, NVR config, network) reuse
this UX verbatim.

| Dependency | Link out to | Re-scan refreshes | Drift to surface |
|---|---|---|---|
| Camera firmware/config | camera web UI | capability matrix + stream | event-type toggles, sensitivity, credentials |
| HA cameras + entities | HA Settings | discovered entities | a camera deleted/renamed in HA |
| Agent DVR | AD admin | AD's detection profile | AD detection disabled on a cam |
| Frigate (future) | Frigate UI | Frigate config | object filter changed |
| Network / mDNS | (none, observable) | reachability + IP | camera IP changed via DHCP |

**Re-scan is both manual *and* scheduled** (daily + on stream-reconnect).
Detected drift becomes a row on the home page's *Needs Attention* lane — not
buried in the per-camera page — so misconfiguration doesn't silently degrade
for weeks before anyone notices.

## 15. Explicit non-goals (the not-an-NVR line)

Worth naming the boundary, because otherwise the page keeps growing:

- No multi-camera grid view *(Agent DVR's job; we read from it).*
- No scrubbing / clip export / arbitrary playback *(the NVR's job).*
- No live RTSP in the browser — refreshable snapshot is enough for "is it
  framed right." Live tile views break ingress + cost a lot.
- No 24/7 motion search — "find every appearance" lives on the *person*
  timeline (Part I), not on a camera-time axis.
- No per-camera identity-signal toggles (P1).
- No mounting / orientation as a config field (P1) — a roof-pointed camera
  that happens to see a face *should* try to recognize it.

## 16. Implications upstream (other parts must honor these)

These ripple beyond per-camera; surfaces designed in later parts must accept
them as constraints:

- **Activity stream needs provenance per event** — `via Reolink` /
  `via preprocessor` / `via AD` tags. Both for trust (native AI quality varies)
  and for debugging *"why did this misfire — was it the native classifier or
  ours?"*  (Part IV.)
- **Home page needs a computational-dependency stripe** — distinguishes
  *"cameras still functional if preprocessor goes down"* from *"cameras
  dependent on preprocessor."* That's the *real* "is the system working"
  question, more useful than a flat preprocessor up/down. (Part III.)
- **A system-wide capability view may belong in Diagnostics** — *"across all
  my cameras, what's the source-of-truth distribution"* — not on the home
  page. (Part VII.)
- **Drift detection writes to the home page's *Needs Attention* lane**, not
  here. The per-camera page shows the *current* state; the home page shows
  *what changed.* (Part III.)

---

# PART III — Home page: Needs Attention + Activity + Trace

Ratified 2026-06-04. The home page is the **front door** of the add-on —
what loads at `/` and what the operator opens first in any session. It is
**not** a system-status page (that's diagnostics, subordinated to a bottom
stripe), and it is **not** a live-state dashboard (that competes with HA's
own Lovelace surfaces and would demand server-push). It is a snapshot of
*"what needs you, what just happened, and is everything healthy"* — in that
order, top to bottom, on one page.

The collapse this ratifies: **Home and Activity are the same surface.** The
activity stream IS the home page's primary content, with Needs Attention
pinned on top and a small system stripe at the bottom. What we previously
called Part IV — Activity Stream as a separate destination — does not exist
as such; it becomes "depth & filter affordances *on* the home stream"
(refocused views, search, day-pickers — TBD, Part IV below).

## 17. Principle

> **Visibility builds trust; reaction is what matters. The passive lane is
> the wallpaper that proves the system is awake; actions are the foreground.
> Neither hides the other by default.**

In a household where most days nothing of consequence happens, an empty
activity page would make it impossible to tell whether *nothing happened* or
*the system died*. Showing passive events — *"motion · dog in backyard ·
auto-dismissed"* — at a quieter visual weight keeps the page **alive** on
quiet days without competing with the actions when they matter. The home
page must honor this principle structurally, not as a footnote.

This composes with the earlier principles: the activity stream displays
**incidents the system reasoned about** (P1 — permissive at capability,
selective at content — applied at the *outcome* layer), each carrying the
provenance per row (P2 — emit a normalized EvidencePacket regardless of
source), and dependency drift surfaces here, not on the page where the
dependency lives (P3 — be a good citizen, surface state changes centrally).

### Corollary — every reasoned event surfaces (iteration 1 Task 5)

> **Every event the system reasoned about — whether or not an alert was
> sent — surfaces in the activity stream as at least a passive row. Silent
> events are a bug.**

This was implicit in the principle above but worth stating explicitly because
it constrains the alert-feed scope: ha-agent's `alert_log` (or whatever feeds
the home stream) must include events that triage processed and *chose not to
escalate*, not just events that produced a push notification. Otherwise the
"system is reasoning" trust line lies — a quiet day might mean *"nothing was
reasoned about today"* rather than *"everything was reasoned about and
nothing was actionable."*

Concrete implication: when a per-camera motion event reaches triage and the
VLM (or a dismissal policy, or the dispatcher) chooses to dismiss, a passive
row still lands in the activity stream with the dismissal reason inline. This
includes pool-cam-style cameras whose events might otherwise never produce a
push notification on their own — they must still appear in the stream.

This is the constraint that makes the home page legible to a watchful user:
no camera is *invisible* to the activity surface, even if it never produces
an alert. The feed scope is *reasoned events*, not *escalated alerts*.

## 18. Page shape

Three zones, fixed order, top-to-bottom:

```
┌ Kukii-Home ──────────────────── [↻] [Settings] ─────────────────┐
│  🟢 All quiet · 12 events today · 0 unhandled                   │ status line
│                                                                  │
│  ─── NEEDS ATTENTION (3) ────────────────────────────────────── │ Zone 1
│  ⓘ  Pool cam: native person detection disappeared yesterday     │
│     — substituted by preprocessor    [Open camera] [Accept]     │ drift (Part II §16)
│  👤  5 unnamed tracks to review                  [Review →]     │ identity inbox (Part I)
│  ⚠  Driveway: package detection MISSING            [Configure]  │ capability gap
│                                                                  │
│  ─── ACTIVITY ────────  passive ✓ · actions ✓ · [Cam ▾] [Who ▾] │ Zone 2
│                                                                  │
│  5m ago    👤 Unknown person walking yard at dusk ·             │
│            pool · driveway · ✓ asked you  ⓘ trace               │ ACTION (foreground)
│  2h ago    ✓ Alice arrived · front door · alerted you           │ ACTION
│  3h ago    Bob left · driveway · passive                        │ passive (muted)
│  yesterday Rex in backyard · ×4 today · auto-dismissed          │ passive, grouped
│            (dog policy)                                          │
│  Tuesday   Mail carrier · front door · auto-dismissed           │ passive
│                                              ↓ See all          │
│                                                                  │
│  Today: 2 actions · 14 passive — system is reasoning             │ trust contract
│                                                                  │
│  ──────────────────────────────────────────────────────────────  │ Zone 3
│  ● 4 cams OK · 1 dependent on preprocessor                       │ system stripe
│  ● Preprocessor on inference-box · last contact 4s ago           │ (collapsed by default,
│  ● HA connected · 18 entities watched                            │  expandable on tap)
└──────────────────────────────────────────────────────────────────┘
```

**Top-line state in plain English, not a status indicator.** *"All quiet.
12 events today, 0 unhandled"* is information; the green dot complements but
doesn't substitute. On a problem day: *"⚠ Pool cam offline · 1 unhandled
alert."*

**Empty state IS the win.** *Needs Attention (0)* should feel rewarding, not
blank: *"Nothing needs you. The system handled everything."* A truly empty
day collapses to the status line + a one-line activity reassurance + system
stripe — not three near-empty sections.

**No live-updating widgets.** `[↻]` + auto-refresh on tab-focus is enough.
Server-push is over-engineered for a home dashboard; HA's Lovelace handles
the live-state job natively if the operator wants it.

## 19. The unit is a Tier-2 incident, not a Tier-1 detection

Each row in the activity stream is a **Canonical Incident Path** in the
sense of Epic 10's three-tier memory model — a coherent thing the system
reasoned about (Approach → Linger → Interaction → Departure → Anomaly, or a
variant). Concretely, an incident has:

- **1..N cameras.** A single incident may span "motion at pool cam, then
  motion at driveway cam, then VLM-reasoned 'unknown person casing the
  house'" — one row, two cameras, one reasoned outcome.
- **A temporal span**, not a single timestamp. The row shows the *peak* or
  *first* relative time; the trace shows the arc.
- **A single VLM-authored headline** (the verb phrasing — see §20).
- **A single outcome** (action / passive / pending), with the per-flavor
  reason inline.

Raw per-camera detections (the per-frame YOLO boxes, the embedding events)
do **not** appear in this stream. They live in **Diagnostics** (Part VII),
which keeps the *observed* stream. Home shows the *reasoned* stream.

This shifts what "12 events today" means everywhere it's used — including
the per-camera page's link out to filtered activity — to **incidents that
touched this camera**, not raw motion events.

## 20. Row schema

Every row is one shape regardless of where it appears (home, focused views,
per-alert page sliver):

```
{relative-time}  {kind-glyph} {VLM scene_description} · {camera(s) joined} ·
                 {outcome-chip}  {trace-link}
```

- **Verb-phrased headline = the VLM's `findings.scene_description`.** It
  reads like a person telling you because a reasoner generated it from the
  EvidencePacket: *"Alice arrived"* / *"Unknown person walking yard at
  dusk"* / *"Delivery left at door"*. Unlabeled subjects fall back to
  *"Unknown person at front door — [label]"* — which doubles as a gentle
  nudge into the Inbox.
- **Camera(s) comma-joined** when an incident spanned multiple — *"pool ·
  driveway"*. The primary camera is the one the VLM's citations point to
  most heavily (extractable from `findings.citations`), with a tiebreaker
  to the first-triggered.
- **Outcome chip** uses the action / passive vocabulary in §21.
- **Trace link** opens the event-detail page (§22) — the *why* behind the
  *what*.

**Visual weight is the lane separator, not stream position.** Actions get
the full row treatment: bold headline, thumbnail, clear outcome chip,
inline ✓/✗/reassign feedback affordance. Passives are muted: single line,
no thumbnail, lower-contrast text, smaller outcome chip. Same scrollable
list; the eye knows the difference.

## 21. The passive lane — three flavors + grouping

"Passive" is not a single state. The system can decide *not to act* in three
internally-distinct ways, and the row surfaces *which* in plain words; the
trace shows the full mechanism:

1. **Policy-matched** — a dismissal policy short-circuited before the VLM
   was called. *"Rex in backyard · matched dismissal policy {dog} until
   Wed 8pm."* The cheapest outcome; reflects the system having previously
   *learned* a pattern. Links directly to the policy (Part VI) for revoke.
2. **VLM-considered, nothing actionable** — VLM called at tier_0 / tier_1,
   returned no recommendations. *"Considered by VLM (tier_0); no
   recommendations."* Reflects *"I looked, this is genuinely fine."*
3. **Recommended-but-gated** — VLM recommended an action; dispatcher gated
   it (below confidence threshold, time-of-day rule, redundancy check).
   *"VLM suggested 'announce' (conf 0.42); dispatcher gated (threshold 0.6
   at this hour)."* Reflects *policy* (dispatcher) overriding *reasoning*
   (VLM) — the kind of thing the operator might want to investigate.

**Grouping repetitive passives.** Same subject + same location + same
outcome flavor → collapses into a single row with a count:

```
yesterday   Rex in backyard · ×4 today · auto-dismissed (dog policy)
            ↳ tap to expand
```

Expand shows individual rows. Active grouping, not truncation — a real
signal that happens to look like the dog (a stranger crossing the yard
while the dog policy is active) **gets its own row** because its trace
differs from the grouped pattern.

**Loop-1 feedback on passives** is first-class. A ✗ on a passive row
("system dismissed this, I disagree") is high-signal correction — often
*higher* than a ✓ on an action — because it surfaces over-broad dismissal
policies. The affordance is smaller on passive rows than on actions, but
it's there.

## 22. The trace (event-detail page)

Clicking any row opens the **incident trace** — the full causality chain
through the system. This is the page that makes the agent *legible*; it
operationalizes the architecture's no-silent-decisions principle as UI.

```
┌ Unknown person walking through yard at dusk ──── 17:10–17:14 ─┐
│  Outcome: ✓ asked you (push, 17:11) — no response yet          │
│  Cameras: pool, driveway                                       │
│                                                                │
│  ── Trace ───────────────────────────────────────────────────  │
│                                                                │
│  17:10:04   pool cam · motion (Dahua native) ────────┐         │
│             1 person (preprocessor YOLO, 0.87)       │ Tier-1  │
│  17:10:34   driveway cam · motion (Dahua native) ────┘ events  │
│             1 person (preprocessor YOLO, 0.84)                 │
│                                                                │
│  17:10:36   Triage: no active dismissal policy matched         │
│  17:10:36   Context: 3 similar past incidents retrieved (RAG)  │
│                                                                │
│  17:10:38   VLM (qwen2.5-vl-7b, tier_1, 1.4s)                  │
│             Findings: "Unknown person walking yard at dusk;     │
│                       not approaching house; possibly neighbor."│
│             Confidence: 0.62                                   │
│             Citations: evt_7f2c, evt_9a1d, cam_pool, area_yard │
│             Recommendations: ask_user_confirm (medium)         │
│             Authored policy: none (low-confidence assessment)  │
│                                                                │
│  17:11:02   Dispatcher → notify.darins_iphone — sent ✓         │
│                                                                │
│  Feedback: [✓ Good catch] [✗ False alarm] [Reassign…]          │
└────────────────────────────────────────────────────────────────┘
```

Every line is something the system did and *why*. The trace IS the
existing audit chain the architecture already mandates (trigger events
→ policy check → context assembled → VLM decision → dispatcher → action)
— so building this is largely rendering existing logs, not generating
new audit data.

- **Default collapsed sections.** Tier-1 events folded by default (count +
  expand); VLM payload folded by default (headline + expand to see
  citations/recommendations/policies). For trust-debug, expand-all is one
  affordance away.
- **Feedback bar closes Loop 1 here, in context.** Not on a separate review
  surface — the user is *looking at the reasoning*; the correction belongs
  next to it.
- **Stable URL.** Each incident gets an `incident_id` minted at incident
  formation (composite of first-triggering event + correlation key);
  `/activity/{incident_id}` is the shareable, link-stable address.

## 23. Time semantics — N-recent + relative, no day boundaries

The home stream shows the **N most recent incidents** with relative
timestamps that graduate naturally:

`Just now · 5m ago · An hour ago · 3h ago · Yesterday · Tuesday · Mar 12`

Default N = 6 on home; *"↓ See all"* opens the focused activity view (Part
IV) for depth. No "today" boundary — relative time handles the read fluidly
without a hard midnight cliff. A quiet day still shows the most-recent
passive ("Yesterday · Rex in backyard ×4") so the stream is never empty
without reason.

## 24. Ripples to other parts (constraints these set)

These cascade — surfaces designed in later parts must honor them:

- **Diagnostics (Part VII) keeps the raw-observation stream.** Home is the
  reasoned stream (incidents); Diagnostics is the observed stream
  (per-frame detections, per-track embeddings, per-VLM-call raw payloads).
  Separate audiences, separate surfaces — never mixed.
- **Identity Inbox (Part I) rows link to the incident, not just the track.**
  Labeling a track *inside its incident context* is meaningfully better UX
  than labeling a contextless thumbnail — the user sees the reasoning chain
  that surfaced the unknown person before naming them.
- **Per-camera "N events today" semantics (Part II §16).** Filtering means
  *incidents that touched this camera*, not *Tier-1 motion events anchored
  there*. A two-camera incident shows once on each camera's filtered view.
- **Policies (Part VI) need a reverse-link from the passive row.** Every
  policy-matched passive shows *"matched policy {X}"* with the policy as a
  link out for revoke. The policy page lists the incidents the policy
  dismissed — completing the loop.
- **HA Companion push (Part VIII)** carries the verb-phrased headline + the
  outcome chip + a deep-link to the trace. The push notification on mobile
  uses the same row schema as a home stream row — written once, rendered
  twice.

---

# PART IV — Activity depth & filters (placeholder)

TBD. Refocused views (by camera, by person, by area), search across all
incidents, day-pickers, export. Shares row schema with Part III; this is
*affordances on the home stream*, not a separate destination.

---

# PART IX — Memory architecture

Ratified 2026-06-04. After Iteration 2 shipped six page-level surfaces
(Rules, Preferences, Policies, Areas, Cameras, Diagnostics), an
architectural pattern surfaced: **the page-per-concept layout was
hiding the fact that almost all of those concepts are the same kind of
thing.** A Rule, a Preference, a TransientIntent, a DismissalPolicy, a
KnownActor.access_profile, and an Area.attention_mode are all
user-authored guidance the VLM reads when reasoning. They differ on
lifecycle, scope, and whether they carry an explicit fire target — not
on what they ARE.

Part IX collapses that surface into one unified *Memory* model: a
single browse, a single authoring surface (Part X), and a clean
separation between *guidance* (instruction) and *evidence* (citation).
This is the architectural reframe that lets the conversational layer
in Part X be possible at all.

## 25. The cut that makes the rest legible — guidance vs evidence

Every piece of state the system holds falls into one of two roles
relative to the VLM:

- **Guidance** — *user-authored signals that shape VLM judgment.*
  Rules, Preferences, SituationalContexts, TransientIntents,
  DismissalPolicies, KnownActor access profiles, Area attention modes,
  per-camera authorized-action whitelists. The VLM reads these as
  *instructions*. They have one job: change what the reasoner decides.

- **Evidence** — *system-observed data the VLM cites.* Episodic events,
  identity galleries (embeddings), VisitLedger counters,
  system-learned behavioral profiles, raw frames, audit logs. The VLM
  reads these as *facts*. They have one job: ground the reasoner in
  what actually happened.

These two classes have **completely different admin needs:**

| Guidance admin | Evidence admin |
|---|---|
| Author, refine, deprecate | Purge, retain, re-embed |
| Conversational entry (Part X) | Bulk delete by date / actor / camera |
| Versioning + provenance | Storage hygiene + privacy ops |
| "Why did this fire" trace | "Stop recognizing X" |

Part IX organizes both halves. The guidance half (mostly built across
Iterations 1 + 2) gets a unified browse surface (`/memory`). The
evidence half (mostly unbuilt) gets two new surfaces (`/identities`
for the identity gallery, `/system` for storage + privacy operations).

## 26. The five memory layers and who admins them

Folding the memory-model concepts (`memory/memory-model-concepts.md`)
into the design surface:

| Layer | What it holds | Class | Admin surface |
|---|---|---|---|
| **1. Working** | In-prompt assembly per VLM call | (transient) | none — visible via Trace (Part III §22) |
| **2. Session** | In-flight journey object incl. drawer conversation state | (transient) | none directly; conversation transcripts surface in audit |
| **3. Episodic** | Closed event records + vector embeddings | evidence | `/system` (retention, purge, re-embed) |
| **4. Identity** | KnownActors, galleries, access profiles, behavioral profiles | mixed: galleries are evidence; profiles are guidance | `/identities` (the unified people + pets + vehicles surface) |
| **5. Semantic** | Rules, SituationalContexts, TransientIntents, DismissalPolicies, Preferences | guidance | `/memory` (unified browse + per-entry detail) |

Working + Session are pure runtime — they have no admin page. Their
*outputs* are auditable via the Trace audit chain (Part III §22 +
Iter 2.F's matched-rules / protective-actions / policy-hits sections),
which is sufficient.

## 27. Load-bearing principle — identity is never lost, only corrected

An identity record IS the audit history. Deleting it is impossible
without losing *"did we ever recognize this actor; when; under what
confidence."* The operations the user thinks of as *"forget Bob"* are
actually two separate, narrower operations on different stores:

- **Stop recognizing Bob** — delete the embeddings under his identity
  record. The record persists with name + history; future events
  cannot match him.
- **Delete rules referencing Bob** — operate on the guidance side, in
  `/memory`. Each rule mentioning Bob is independently removable.

There is no "purge everything about person X" in v1. The vocabulary
across the UI reflects this:

| User language | What it actually does | Where |
|---|---|---|
| "Stop recognizing Bob" | Delete embeddings under his identity | `/identities/{id}` |
| "Correct identity" | Merge / re-label; record persists | `/identities/{id}` |
| "Delete rules about Bob" | Per-rule action on guidance entries | `/memory` (filtered by actor) |
| "Erase last hour of events" | Bulk delete events + frames in time window | `/system` |
| ~~"Forget Bob everywhere"~~ | Not offered as a single op — see above | n/a |

This is the trust contract. If a future jurisdiction (GDPR-style)
demands a true single-purge primitive, the design accepts that as a
follow-up; v1 errs on the side of preserving audit.

## 28. `/memory` — the unified guidance browse

Replaces the separate `/intent`, `/policies`, and the latent
SituationalContexts surface. One list, every guidance entry,
filterable two ways.

**Default cut — by context** (how humans think):

```
┌ Memory ─────────────────────────────────────────────────────────┐
│  ✨ Tell me what to watch for…       [ by context ▾ ] [ by type ] │
│                                                                  │
│  About Winston                                          12 items │
│    ▸ Rule: Winston unsupervised front yard  · critical · 3 hits  │
│    ▸ DismissalPolicy: dog at front cam      · 7 hits             │
│    ▸ KnownActor.access_profile: Backyard 4–7pm                   │
│    ▸ … 9 more                                                    │
│                                                                  │
│  About the Pool                                          5 items │
│    ▸ Area.attention_mode: attention (continuous monitoring)      │
│    ▸ Rule: Person at pool after dusk        · critical           │
│    ▸ … 3 more                                                    │
│                                                                  │
│  About tonight                                           2 items │
│    ▸ TransientIntent: Watch for Bob's car   · expires 11pm       │
│    ▸ SituationalContext: dinner party 6–11pm                     │
│                                                                  │
│  About my preferences                                    4 items │
│    ▸ Preferences.vigilance: normal                               │
│    ▸ Preferences.what_i_care_about: "Winston is our dog…"        │
│    ▸ Preferences.quiet_hours: 11pm–6am                           │
│    ▸ Preferences.relationships: 3 actors labeled                 │
└──────────────────────────────────────────────────────────────────┘
```

**Toggle cut — by type** (for debugging + power users):

```
Rules (3) · Preferences (1) · Policies (8) · Transient intents (2)
· Situational contexts (1) · Access profiles (4) · Area postures (5)
```

Same underlying list, two facets. Each entry renders the same row
schema:

**`name · type-chip · scope · lifecycle · last-applied · provenance-icon`**

Click any row → its detail view (existing per-type forms — they
become the *edit* surfaces, not the *authoring* surfaces; authoring
happens via the conversational drawer in Part X).

The conversational ✨ trigger lives at the top of the page. Clicking
it opens the drawer (Part X) with `/memory`-blank context. Clicking
any entry's "Refine conversationally" action opens the drawer
pre-loaded with that entry as context.

## 29. `/identities` — the unified actor surface

Replaces the standalone Review page. **People, pets, and vehicles are
all identities; one surface manages all three.** Two states per
record, one underlying list:

- **Review** — unresolved tracks awaiting label (current Build #292
  surface; unchanged behavior)
- **Enrolled** — labeled actors with full lifecycle management

```
┌ Identities ─────────────────────────────────────────────────────┐
│  [ Review (5) ] [ Enrolled (12) ]  filter: all ▾                 │
│                                                                  │
│  Enrolled                                                        │
│    👤 Bob          household        face + body          ▸       │
│    👤 Alice        guest            face                 ▸       │
│    🐕 Winston      pet              pet + gait           ▸       │
│    🚗 Bob's car    vehicle          vehicle + plate      ▸       │
│    …                                                             │
│                                                                  │
│  Review (5 unresolved tracks)                                    │
│    track 1248  ·  Pool cam  ·  3h ago         [ Label ]          │
│    …                                                             │
└──────────────────────────────────────────────────────────────────┘
```

**Per-identity detail page** — `/identities/{id}`:

| Section | R/W | Contents |
|---|---|---|
| At a glance | R | Friendly name, kind (person/pet/vehicle), recognition quality, last seen |
| Templates (gallery) | R + curate | All embeddings backing this identity, by modality. Click to inspect a template; remove a misleading one. |
| Access profile | W | Areas, hours, expected pattern — the guidance fields the VLM reads. *This is a guidance entry; it also surfaces under `/memory` "About X."* |
| Behavioral profile | R (system-learned) + override | "Walks fast, sits often, …" — system-inferred from episodic; user can override specific claims. |
| VisitLedger summary | R | Visits per area per week, last visit timestamp |
| Linked guidance | R + link out | Rules, policies, dismissals mentioning this actor (link to `/memory` filtered view) |
| Operations | action | **Stop recognizing** (delete embeddings) · **Correct/merge** (re-label) · **Edit access profile** |

**Vehicles is new pipeline work.** The UI pattern slots in identically
to people + pets, but the embeddings + plate-recognition layer is not
built. Flag as its own arc; the `/identities` page handles vehicles
the day the pipeline lands without UI changes.

## 30. `/system` — storage + privacy operations

A new top-level surface — *not* under Diagnostics. Diagnostics answers
*"is it working"*; `/system` answers *"what's it holding and who can
see it."* Three cards, top-to-bottom:

**Card 1 — Storage usage:**

```
┌ Storage usage ──────────────────────────────────────────────────┐
│  Episodic events       12,483 events   ·  142 MB    [ details ▾ ]│
│    by camera: Pool 4,201 (47 MB) · Front 3,189 (38 MB) · …       │
│  Frame snapshots       9,840 frames    ·  3.8 GB    [ details ▾ ]│
│    by age: <24h 612 (240 MB) · 1-7d 3,201 (1.2 GB) · …           │
│  Identity embeddings   2,840 templates ·  68 MB                  │
│    by modality: face 1,201 · body 940 · gait 412 · pet 287       │
│  Audit logs            48,201 rows     ·  31 MB                  │
│  Stores combined       7 SQLite DBs    ·  14 MB                  │
│                                                                  │
│  Total                                  ~4.1 GB used             │
└──────────────────────────────────────────────────────────────────┘
```

**Card 2 — Retention policy** (per-class editor):

| Class | Knob | Default |
|---|---|---|
| Episodic events | Keep `N days` *or* `N GB`, whichever lower → prune oldest | 90 days, 10 GB |
| Frame snapshots | Keep `N days` (separately tuned; frames dominate disk) | 14 days |
| Identity embeddings | **Never auto-prune** — gallery is precious | n/a |
| Audit logs | Keep `N days` | 365 days |

Per-camera overrides optional — a privacy-sensitive camera can retain
less than the global default.

**Card 3 — Operations:**

- **Erase last hour** — panic button. Bulk-deletes recent events +
  frames + clips across all cameras. Confirms; audit-logged.
- **Purge from `[camera]` between `[start, end]`** — surgical bulk
  delete. Audit-logged.
- **Export everything about `[actor | camera | timeframe]`** —
  portable JSON + frames archive (.zip).

**Admin audit log** at the bottom — every storage/privacy operation
shows here with timestamp + scope + bytes removed. Read-only. This is
the trust contract: anything destructive is recorded.

## 31. Nav collapse + URL backward-compat

Iteration 2's nav was:

```
Home · Activity · Areas · Intent · Policies · Cameras · Identities · Diagnostics
```

After Part IX:

```
Home · Activity · Memory · Areas · Cameras · Identities · System · Diagnostics
```

Changes:

- `/intent` and `/policies` → both folded into `/memory`. Both URLs
  301-redirect to `/memory?type=rule` and `/memory?type=policy`
  respectively for backward-compat with bookmarks + HA Lovelace links.
- `/identities` retained — its scope expanded (Review + Enrolled), not
  its URL.
- `/system` is new — the storage + privacy surface.
- `/diagnostics` retained — same purpose, narrower (system health,
  reasoner roll-up, dev loop).
- Areas + Cameras stay separate — they're *config primitives* that
  *carry* guidance fields (attention_mode, role, whitelists), but
  they're not themselves guidance entries. They surface in `/memory`
  as filtered context groups, but their config lives under their own
  nav for direct authoring.

## 32. Implications upstream (other parts must honor these)

- **Iter 2 page renderers stay, but their nav entries move.** The
  current `intent.py` / `policies.py` rendering becomes detail views
  reachable from `/memory` rather than top-level nav targets.
- **Every guidance entry gets a `provenance` JSON field.** Old rows
  get backfilled with `{"origin": "pre-provenance", "transcript_id":
  null}`. The provenance shape is the contract Part X writes against.
- **The audit chain on `/alert/{id}` (Part III §22) already surfaces
  matched rules + protective actions + policy hits; under Part IX it
  also surfaces any guidance entries' provenance trails** so the user
  can follow "why did this fire" → "from which authored intent" →
  "from which conversation turn."
- **`KnownActor.access_profile` and `Area.attention_mode` get
  promoted to first-class guidance entries** with their own
  provenance + `/memory` row representation. They were always
  guidance; the model just acknowledges it now.

---

# PART X — Conversational dispatcher

Ratified 2026-06-04. The unifying authoring surface for every
guidance type — Rules, Preferences, TransientIntents,
DismissalPolicies, SituationalContexts, access profiles, area
postures. The user expresses intent in natural language; the system
classifies, previews, and writes to the correct store with full
provenance.

This is what separates Kukii-Home from other AI surveillance
products: every other system makes the user think in terms of
*"is this a rule, a preference, an automation, a tag, a smart
alert"* — buckets they had no reason to learn. Kukii-Home lets the
user speak in sentences and places the result on the right axis.

## 33. The cube — three axes, every guidance entry is a point

Every guidance type sits at one corner of:

- **Scope** — global · area · camera · actor · kind · pattern
- **Lifecycle** — persistent · temporal (TTL) · fire-once
- **Fire affordance** — explicit alert · soft prior shift · dismiss / suppress · metadata only

The familiar storage classes are corners of this cube:

| Storage class | Scope | Lifecycle | Fire affordance |
|---|---|---|---|
| **Rule** | scoped (area / actor / kind) | persistent | explicit alert |
| **Preference** | global | persistent | soft prior shift |
| **TransientIntent** | scoped | temporal | explicit alert |
| **DismissalPolicy** | scoped (pattern) | persistent | dismiss |
| **SituationalContext** | global or area | temporal | soft prior shift |
| **KnownActor.access_profile** | actor | persistent | soft prior shift |
| **Area.attention_mode** | area | persistent | metadata (changes pipeline) |

The user doesn't see the cube. The dispatcher sees it and routes.

## 34. The drawer — the conversational surface

A persistent right-side drawer, available from every page via the ✨
trigger in the header. Renders in any web client — including HA
Companion's WebView, which is how Kukii-Home reaches mobile (no
separate mobile app or API surface).

**Drawer requirements:**

- **Persistent across page navigation.** State lives server-side
  (`sessions.db`); the drawer reattaches to the active thread on
  every page load. Closing the drawer doesn't end the conversation.
- **Page-context aware.** Opening on `/alert/{id}` prefills with that
  incident as context. Opening on `/memory` entry detail prefills with
  that entry. Opening from the header is blank.
- **Inline preview cards.** Placement proposals render as compact
  cards inside the conversation flow (not a separate panel). Confirm
  or refine inline.
- **"Open in editor" escape hatch.** Every preview card has a link to
  the full per-type form for adjustments the conversation didn't
  capture.
- **Refinement-as-thread.** Refinements on an existing entry append
  to the same thread, so the audit view shows the conversation arc,
  not just the last turn.
- **WebView-safe.** No Service Workers, no APIs unavailable to HA
  Companion's WebView. Touch-friendly sizing.

```
┌── /memory ─────────────────────────┬── ✨ Conversation ──────────┐
│  Memory                            │  Authoring                  │
│                                    │                             │
│  About Winston            12 items │  > I want to know when      │
│    ▸ Rule: Winston unsupervised…   │    Winston is out front     │
│    …                               │    without anyone with him  │
│                                    │                             │
│  About Pool               5 items  │  ┌─ proposing ─────────────┐│
│    …                               │  │ Rule · persistent · alert││
│                                    │  │ Name: Winston            ││
│                                    │  │   unsupervised front yard││
│                                    │  │ Scope: front_yard area   ││
│                                    │  │ Severity: critical       ││
│                                    │  │                          ││
│                                    │  │ Because: you said        ││
│                                    │  │ 'when' + 'always' → Rule.││
│                                    │  │ Scope inferred from      ││
│                                    │  │ Winston's home areas.    ││
│                                    │  │                          ││
│                                    │  │ [ Confirm ] [ Refine ]   ││
│                                    │  │ [ Open in editor ]       ││
│                                    │  └──────────────────────────┘│
└────────────────────────────────────┴─────────────────────────────┘
```

## 35. The dispatcher — LLM-only, schema-validated

One LLM call per turn. Read-only access to the system state (stores,
KnownActors, areas, recent events) via tool calls. Single
write surface (§37). Returns a typed placement proposal:

```typescript
PlacementProposal = {
  storage_class: 'rule' | 'preference' | 'transient_intent' |
                 'dismissal_policy' | 'situational_context' |
                 'access_profile' | 'area_posture',
  name: string,
  scope: { actor?, area?, camera?, kind?, pattern? },
  lifecycle: 'persistent' | 'temporal' | 'fire_once',
  lifecycle_ttl_iso?: string,         // when temporal
  fire_affordance: 'alert' | 'shift_prior' | 'dismiss' | 'metadata',
  severity?: 'low' | 'normal' | 'critical',
  intent_text: string,                // the prose the VLM reads at eval time
  reasoning: string,                  // ONE SENTENCE explaining the placement
  confidence: number,                 // 0..1
  clarifying_questions: string[],     // empty when confidence high
}
```

**Schema-validated.** Malformed responses retry with the schema in the
retry prompt. Same pattern as the workflow harness's structured-output
agents.

**Two-axis disambiguation** when `confidence < 0.7`: the dispatcher
returns clarifying questions targeting the two cube axes that matter
most — **lifecycle** (*"just for tonight, or always?"*) and **fire
affordance** (*"should it ping you, or just change how I judge things?"*).
Scope is usually inferred from the utterance + KnownActor data; when
it's not, scope becomes a third clarifying question.

**The `reasoning` field is the audit primitive.** Not chain-of-thought
— a one-sentence human-readable justification: *"You said 'always' +
'tell me' → Rule. Scope inferred from Winston's home areas + 'out
front' utterance."* This is what appears under the "How this was
authored" card on every entry's detail page.

## 36. The session model

A new SQLite store, `sessions.db`, sister to the existing five:

```sql
CREATE TABLE sessions (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,      -- HA user id (inherited via ingress auth)
  opened_at     REAL NOT NULL,
  closed_at     REAL,
  page_context  TEXT,               -- where the drawer was first opened
  alert_context TEXT                -- pre-loaded alert_id when relevant
);

CREATE TABLE transcripts (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL,
  turn_index    INTEGER NOT NULL,
  role          TEXT NOT NULL,      -- 'user' | 'system'
  utterance     TEXT NOT NULL,
  proposal_json TEXT,               -- the PlacementProposal when role='system'
  committed_to  TEXT,               -- guidance entry id when confirmed
  ts            REAL NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE guidance_provenance (
  guidance_id   TEXT PRIMARY KEY,   -- the entry id in its own store
  origin        TEXT NOT NULL,      -- 'conversation' | 'form' | 'system_proposed'
  transcript_id TEXT,               -- pointer to the originating turn
  user_utterance TEXT,              -- denormalized for fast audit reads
  placement_reasoning TEXT,         -- the LLM's one-sentence justification
  user_confirmed_at REAL,
  refinement_transcript_ids TEXT    -- JSON array of later refinement turns
);
```

**Session lifetime.** A session lives until 24h of inactivity, then
closes. A new utterance after that opens a new session. Transcripts
persist forever (cheap; the audit value depends on it).

**Forms write through the dispatcher too.** When the user authors via
a per-type form (e.g., the existing Rules editor under `/memory`), the
form synthesizes a fake transcript turn ("authored via form") and
calls `commit_guidance` the same way. Provenance origin becomes
`'form'` instead of `'conversation'`, but the audit view is uniform.

## 37. `commit_guidance` — the single write surface

Every guidance write — conversational, form-authored, or
system-proposed — funnels through one function:

```
commit_guidance(
  proposal: PlacementProposal,
  transcript_id: str | None,
  origin: 'conversation' | 'form' | 'system_proposed',
) -> guidance_id
```

The function:

1. Validates the proposal against the storage class's schema
2. Routes to the right store (RulesStore, PreferencesStore, etc.)
3. Writes the entry
4. Writes the `guidance_provenance` row
5. Returns the new entry id

**Refinement uses the same path.** Editing an existing entry calls
`commit_guidance` with the refined proposal + the new transcript
turn; the provenance row's `refinement_transcript_ids` gets appended.
The entry itself is updated, not duplicated.

## 38. Provenance + audit primitives

On any guidance entry's detail view, a *"How this was authored"* card
shows:

```
┌─ How this was authored ───────────────────────────────────────────┐
│  You said (2026-06-04 19:42):                                     │
│    "I want to know when Winston is out front without anyone       │
│     with him."                                                    │
│                                                                   │
│  System placed this as a Rule because:                            │
│    "You said 'always' + 'tell me' → Rule. Scope inferred from     │
│     Winston's home areas + 'out front' utterance."                │
│                                                                   │
│  Confirmed: 2026-06-04 19:43                                      │
│                                                                   │
│  Refinements (2):                                                 │
│    ▸ 2026-06-05: "…also if my brother isn't with him"             │
│    ▸ 2026-06-12: "drop severity from critical to normal"          │
│                                                                   │
│  [ Refine conversationally ]   [ Open in editor ]                 │
└───────────────────────────────────────────────────────────────────┘
```

Three trust guarantees this enforces:

1. **Preview before save.** The dispatcher NEVER silently writes. The
   preview card with the type chip, scope, lifecycle, and reasoning
   appears for confirmation on every commit.
2. **Storage class is visible.** The user always sees *"this becomes
   a Rule"* (or whichever class) before confirming. Misclassification
   gets caught at the preview step.
3. **Round-trip from every entry.** Click any guidance row in
   `/memory` → see the originating conversation → re-enter that
   thread to refine. The audit isn't a separate log; it's the
   authoring trail.

## 39. Misclassification backstops

Even with preview + disambiguation, the LLM will occasionally place
wrong. Three backstops:

1. **Preview-before-save** (§38) — primary line of defense.
2. **Reversible by re-typing.** Every entry detail view has a
   *"Convert to a different type"* action. The dispatcher takes the
   existing intent_text + a target class hint, re-runs placement, and
   moves the entry to the new store with a new provenance trail
   linked back.
3. **Drift detection.** A nightly sweep flags:
   - Rules with 0 fires in 30 days → suggest *"this rule hasn't
     fired; maybe a Preference?"*
   - TransientIntents that outlived their TTL via manual extension
     twice → suggest *"convert to a Rule?"*
   - DismissalPolicies with 0 hits in 30 days → suggest *"revoke?"*

   Drift suggestions surface in `/memory` as a small banner row at
   the top of the affected entry's context group. Never auto-applied.

## 40. Push-reply via fragment-load

The highest-leverage authoring moment is *right after an alert
fires*, when the user is looking at the push notification and
context is fresh. Kukii-Home reaches this without any special HA
Companion protocol:

1. `kukiihome_alert` fires → HA Companion delivers push
2. User taps push → HA Companion opens add-on at `/alert/{event_id}#drawer`
3. The `#drawer` fragment + `alert={id}` context auto-opens the
   drawer pre-loaded with that alert
4. User types *"this is fine — Winston was with me"* → standard
   utterance flow
5. Dispatcher recognizes the context (alert_id + the rule that fired)
   → proposes a refinement of that rule
6. User confirms → rule updated with new refinement transcript turn

**Zero new push-side infrastructure.** The drawer just listens for
the URL fragment and prefills.

## 41. Implications upstream (other parts must honor these)

- **Part IX's `/memory` browse depends on this part.** The ✨ trigger,
  the per-entry "Refine conversationally" action, and the "How this
  was authored" card all live here; `/memory` references but does not
  duplicate.
- **Part III §22 trace audit chain extends to include provenance.**
  When a rule fires, the trace already shows the rule. Under Part X
  the trace also shows the originating conversation turn (one-line
  excerpt + link to full transcript).
- **Forms become structured authoring shortcuts.** The existing Iter 2
  rule / preference / area / policy / whitelist forms still work —
  they just feed `commit_guidance` with a synthetic transcript instead
  of writing directly. No user-facing change; the audit becomes
  uniform.
- **HA Companion is the mobile client.** No separate mobile API
  surface, no separate mobile UI, no separate auth. The same web
  endpoints serve HA Companion's WebView; ingress auth carries
  through. Voice input is HA Assist's concern, not Kukii-Home's.

---

# Build state — after Iteration 2 + Parts IX/X ratification

Page-level surfaces:

| Surface | State |
|---|---|
| `/home` | built (Task 7-era + audit chain) |
| `/activity` | built |
| `/intent`, `/policies` | built; collapse into `/memory` per Part IX |
| `/areas`, `/cameras` | built (Iter 2.B / 2.C) |
| `/identities` (Review only) | built; expand to Enrolled per Part IX §29 |
| `/diagnostics` | built (Iter 2.E) |
| `/memory` | unbuilt (Part IX §28) |
| `/system` | unbuilt (Part IX §30) |
| Drawer + dispatcher | unbuilt (Part X) |
| `/identities/{id}` detail | unbuilt (Part IX §29) |

Backend stores:

| Store | State |
|---|---|
| RulesStore, ActionStore, AreaStore, PreferencesStore, PolicyStore | built |
| `sessions.db` (transcripts + provenance) | unbuilt (Part X §36) |
| Storage + retention enforcement | unbuilt (Part IX §30) |
| Vehicle identity pipeline | unbuilt (Part IX §29 note) |
| SituationalContexts store + reasoner integration | unbuilt (dispatched as guidance under Part X) |

Cross-cutting:

- Provenance backfill for existing entries: one-time migration writing
  `{"origin": "pre-provenance"}` rows for everything authored before
  Part X lands.
- Nav reorganization: redirects from old URLs.
- Audit-chain extension on `/alert/{id}` to surface provenance one-line
  excerpts.

