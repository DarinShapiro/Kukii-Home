# Identity Resolution & Enrollment — UI Design Spec

**Status:** Draft (design only; no implementation). Companion to Build #292
(always-embed → persist → resolve). Maps the end-to-end information
architecture for making the identity loop *usable*.

**Refs:** `planning/epics/10-identity-recognition.md` (the system design this
serves), `services/preprocessor` (owns the store + frames + recognizer),
`frontend/operator-dashboard` (React web UI, currently skeleton),
`frontend/ha-cards` (in-HA surfaces).

---

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
