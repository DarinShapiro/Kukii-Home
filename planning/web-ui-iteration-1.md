# Web UI iteration 1 — feedback round (2026-06-04)

First round of feedback on the v2 UI skeleton (commit `9d60e38`, shipped as
0.7.0). Items captured **as actionable tasks with enough detail to implement
from a cleared context**. Reference: `planning/web-ui-design.md` for the
overall IA, principles, and ratified parts.

Each task carries:
- **Intent** — what the user actually wants
- **Approach** — how to implement
- **Touches** — concrete files/functions
- **Open** — design questions to resolve while implementing
- **Done when** — acceptance criteria

The tasks are listed in implementation order (lighter ones first, the big
architectural one last). Tasks 2, 3, 4, 6, 7 are tractable in a single
session; task 1 is multi-session and benefits from being scoped explicitly;
task 5 is a diagnosis task that may surface its own follow-ups.

---

## Task 1 — Event clips: playable real video, not assembled sparse frames

**Intent.** Every event thumbnail in the UI should be **playable** —
clicking opens the actual archived video clip of the event, not a GIF
assembled from sparse keyframes. This is a *quality* issue, not a feature
issue: assembled-frame playback feels cheap and doesn't carry the natural
motion / sound that makes an event recognizable.

This is the only **architectural** task in this round; the other six are
refinements on top of what exists.

### Design (preprocessor-native path)

Today the event recorder persists `frame_NNNNN.jpg` files + `event.json` per
event. We need a *clip* artifact alongside.

Two viable production designs:

**Design A — *recorder-mux* (re-encode at event close).** When the event
recorder closes an event, alongside writing JPEG frames + manifest, it muxes
the buffered JPEGs into a `clip.mp4` (H.264, browser-playable). The frames
remain on disk for the identity pipeline; the MP4 is the user-facing
artifact.
- Pros: single source of truth, deterministic, no second capture path.
- Cons: re-encoding 60-180 s of JPEG frames is non-trivial CPU; the result
  isn't as good as a native H.264 stream because we lost B-frames at
  decode time. Quality depends on rolling-buffer keyframe density (today
  ~1 fps after the §7.6 capture-fps decoupling — so the clip would be
  jumpy unless capture is raised).

**Design B — *stream-tap* (parallel encode while live).** A separate capture
sink consumes the H.264 sub-stream directly (PyAV passthrough, no decode)
and writes a rolling MP4 segment per camera. On event close, the recorder
locates the segment(s) overlapping the event window and saves them as
`clip.mp4` alongside the JPEGs.
- Pros: native H.264 quality, no re-encode CPU, full-rate motion.
- Cons: doubles per-camera capture cost (one PyAV session for keyframes
  to the rolling buffer + one for continuous MP4 segments); more state to
  manage (segment rotation, alignment to event windows).

**Recommendation:** start with **Design A** for the offline-enrich worker
*and* the event recorder, raising the capture fps for the live path
(already wired via `KUKIIHOME_PREPROCESSOR_CAPTURE_INTERVAL_S`). Defer
Design B to a later phase if quality is insufficient. The Design A clip
at 4 fps capture is usually adequate for "what happened" review.

### Design (Agent DVR path)

When the preprocessor backend is `agent_dvr_native` or `agent_dvr_passthrough`
(per Epic 10's preprocessor router taxonomy), AD already records continuous
H.264 clips natively at full quality. The right design here is **not** to
duplicate — instead:

- The event manifest carries an `external_clip` field with AD's clip
  reference: `{kind: "agent_dvr", camera, start_ts, end_ts, url}`.
- The UI's clip player checks for `external_clip` first; if present, the
  `<video src>` points at AD's clip-serving endpoint (proxied through the
  ha-agent to honor ingress auth — same pattern as `/alert/<id>/frame.jpg`).
- The preprocessor never writes its own clip.

This becomes the *delegated* state of the capability matrix (Part II §12):
clip recording is DELEGATED to AD.

### Stop-gap (what to ship in the meantime)

The assembled GIF I built for track-detail (Part I) is still useful — for a
*single track* (one person, padded crop, ~28 frames) it's faster + smaller
than a real clip and answers a different question. **Keep the GIF for track
detail; build real clips for event detail.**

For the activity-row / trace page thumbnails, **stop-gap = the existing
single representative frame**, with a small ▶ play indicator that, when
clicked, falls back to:
1. If `external_clip` is set → fetch from AD via proxy
2. Else if `clip.mp4` exists in the event dir → serve it
3. Else (legacy events) → assemble JPEG frames into MP4 on demand and cache

Path 3 lets us ship something usable for the events we already have on disk,
without rebuilding the recorder.

### Touches

- New: `services/preprocessor/src/.../clip_writer.py` — `mux_jpegs_to_mp4()`
  helper using `pyav` or `imageio[ffmpeg]`.
- `event_recorder.py`: on close, write `clip.mp4` alongside JPEGs.
- `event.json` schema bump: add `clip_path` (relative) and optional
  `external_clip` block.
- New endpoint on the preprocessor:
  `GET /events/{event_id}/clip.mp4` → serves the clip (range requests
  for `<video>` seeking).
- New endpoint on ha-agent proxying the above through ingress auth.
- UI changes (Part III §22 trace page + Part IV activity list): swap static
  `<img>` for `<video>` with the clip URL where available; ▶ play indicator
  overlay; click → open clip in modal or inline.
- Adapter work: `agent_dvr` adapter implements the clip-URL discovery.

### Open

- **Codec / container.** H.264 + MP4 + AAC (or no audio) is the most
  universal. PyAV can mux directly. Hardware H.264 encode on the inference
  box (NVENC) keeps cost negligible if Design B is later adopted.
- **Retention.** Clips are bigger than JPEGs — the existing 10 min disk
  horizon will fill faster. Set per-event retention (e.g. keep clips for
  events with a tier ≥ tier_1; auto-prune passive-dismissed events earlier).
- **Audio.** Most security cams have audio. Worth capturing? Privacy + legal
  implications — flag as a per-camera privacy posture toggle (Part II §11).

### Done when

- Event rows in Activity and trace-page snapshots are clickable.
- Clicking opens a real `<video>`-tag player with the actual clip (or an
  AD-served clip when in AD mode).
- `agent_dvr_native` backend path documented + stubbed (real adapter can
  land later).
- Track-detail page (Part I) keeps its existing GIF — distinct artifact for
  distinct job.
- Per-event retention policy documented in DOCS.md.

---

## Task 2 — Trace page timestamp ambiguity

**Intent.** On the activity *detail* (trace) page, the listed time is
ambiguous — e.g. `17:10:38` without context, or no date. Want a friendlier,
graduated format consistent with the activity *list*:

```
20 minutes ago
2h ago
Yesterday at 4:51 PM
Last Saturday at 12:05 PM
March 12 at 8:14 AM
```

The same helper should be used everywhere a timestamp is shown to the user
(activity list, trace, alert page, identity inbox cards, …).

### Approach

Extend `relative_time()` in
`services/ha-agent/src/kukiihome_ha_agent/web_ui/shell.py` to combine
**relative bucket** + **clock time** when the bucket is "Yesterday" or
older. The existing graduation stays:

| Bucket | Output |
|---|---|
| < 60 s | `Just now` |
| < 1 h | `12 minutes ago` (singular `1 minute ago`) |
| < 2 h | `An hour ago` |
| < 24 h | `3h ago` |
| < 48 h | `Yesterday at 4:51 PM` |
| < 7 d | `Last Saturday at 12:05 PM` |
| else | `March 12 at 8:14 AM` |

Use the user's local timezone (via `astimezone()`) for clock time. 12-hour
format with AM/PM. Add an HTML `title` attribute with the absolute ISO
timestamp for hover/inspection.

Also: rename the function to `friendly_time()` so it covers both the
"relative" and "absolute" cases.

### Touches

- `shell.py::relative_time()` — extend + rename to `friendly_time()`
- All call sites:
  - `web_ui/home.py::_render_activity_row()`
  - Per-alert page render in `__main__.py::_render_alert_page()` and the
    timestamps it shows (line ~480-540)
  - Review identity cards (`review_page.py::_track_card()` — has `_hms()`
    currently; replace)
  - Track-detail page time-span header

### Open

- Singular vs plural ("1 minute ago" vs "1 min ago"). Pick one convention.
- 12 h or 24 h? `4:51 PM` reads natural in EN-US; `16:51` more compact.
  Default 12 h for warmth; keep 24 h available if region demands.
- For the trace page's per-step timestamps inside the audit chain (e.g.
  `17:10:38 VLM (qwen2.5-vl-7b, tier_1, 1.4s)`), keep precision since those
  are debug. Don't replace step-internal precision with the friendly form.

### Done when

- Every user-visible event timestamp uses `friendly_time()`.
- Hovering a timestamp shows the absolute ISO time in the tooltip.
- Trace page header carries "Yesterday at 4:51 PM" not "17:10–17:14".
- Step-internal precision timestamps inside the trace stay precise.

---

## Task 3 — Activity headlines: drop redundant camera labels

**Intent.** Current: `Person at Front South Camera Fluent · front_south`.
Issues:

- *"Fluent"* is the Reolink stream-quality name (the lower-resolution
  sub-stream). Not user-meaningful; should be stripped.
- *"front_south"* is the entity-slug rendered alongside a friendly name
  that already says "Front South Camera." Redundant.
- The phrasing is robotic. Should read like a sentence.

Better: `Person detected at Front South Camera`.

### Approach

Two pieces:

**A. Camera display-name normalizer.** New helper in
`web_ui/shell.py::camera_display_name(raw_name: str) -> str`:

- Strip well-known stream-quality suffixes: `Fluent`, `Clear`, `Balanced`,
  `Main`, `Sub`, `Substream`, `Mainstream`, `HD`, `SD`. Case-insensitive,
  whole-word match at the end of the name.
- Strip trailing `Stream`, `Camera Stream`, `Cam Stream` similarly.
- If the resulting string already contains "Camera" / "Cam", keep as-is;
  otherwise append " Camera".

**B. Activity row headline composition.** Update
`web_ui/home.py::_alert_headline()`:

- If the alert has a VLM `findings.scene_description`, use it verbatim
  (it already reads like a person speaking — Part III §20).
- Otherwise compose from kind + camera display name, with a single space and
  no slug separator:
  - `Person detected at Front South Camera`
  - `Dog detected at Backyard Camera`
  - `Motion at Front South Camera` (when no kind classification)
- Drop the camera slug from the where-line in `_render_activity_row()` once
  the headline already contains the camera name. (Only render `· <slug>` if
  the headline *doesn't* contain it.)

### Touches

- `web_ui/shell.py` — add `camera_display_name()`.
- `web_ui/home.py::_alert_headline()` — rewrite.
- `web_ui/home.py::_render_activity_row()` — only emit `· cam` when the
  headline doesn't already include the camera display name.
- Tests: add cases to `tests/test_web_ui.py` covering each suffix-strip
  case and the redundancy elimination.

### Open

- Where does the camera's friendly name come from? Today it's the HA
  camera entity's `friendly_name`. Need to make sure the alert payload
  carries that (not the entity_id slug) when it reaches `_alert_headline()`.
  Verify in `alert_log` shape.
- Suffix list is Reolink-centric. Add `Dahua`-style suffixes (`Main`,
  `Sub`) — overlaps already. Hikvision uses `01`/`02` numerals — skip for
  now, surface as open question.
- What if a camera is named with intentional brand text (e.g. user named it
  "Reolink Front")? Suffix strip would not touch this. Keep the strip
  conservative to avoid eating legitimate names.

### Done when

- `Person detected at Front South Camera` appears instead of the verbose
  redundant form.
- Stream-quality suffixes (`Fluent`, etc.) never appear in user-facing
  headlines.
- The "where" line in the activity row does not duplicate the camera that's
  already in the headline.
- Tests cover the suffix-strip + redundancy-elimination cases.

---

## Task 4 — Sticky navigation header on every page

**Intent.** Header (with the Home / Activity / Areas / ... nav) should stay
pinned at the top of the viewport on scroll, on **every** page — including
trace / alert / review pages — so the user navigates within the app and
never has to use the browser back button.

### Diagnosis

The shell CSS already declares `position: sticky; top: 0; z-index: 10` on
the `<header>`. So the new pages built under the shell *should* be sticky
already. The issue is one of two things:

1. **Some pages don't use the shell at all.** The legacy `/` status page,
   the per-alert page (`alert/<event_id>`), the Review page (`/review`),
   and the track-detail page (`/review-track`) have their own templates
   and *don't* go through `render_shell()`. Those pages have their own
   non-sticky headers.
2. **Layout interferes with sticky.** Some ancestor element may have
   `overflow: auto` or a height constraint that breaks sticky positioning.

### Approach

Two-part:

**A. Bring legacy + Review + track-detail pages under the shell.**

- `review_page.py::render_review_html()` and `render_track_detail_html()`:
  refactor to return only the body content; have the route handler wrap
  it in `render_shell("review", body)` or `render_shell("identities", body)`.
- The page-specific styles in `review_page.py::_STYLE` move into a section
  of `web_ui/shell.py::_STYLE` (or live as a per-page CSS block embedded
  in the body return — both work).
- The track-detail page becomes a "detail view" under the Identities tab —
  add a per-page sub-title in the content area, but the main nav stays.

**B. Confirm sticky works cross-page.**

- Add an explicit `body { margin: 0 }` reset (already done in shell.py).
- Confirm `main` doesn't constrain height; if it does, the sticky context
  is `main`, not the viewport. Should be `min-height: calc(100vh -
  <header-height>)` or just left alone.
- Add a CI/test step that renders each page and asserts `position:sticky`
  is in the head (cheap regression).

This task pairs naturally with **Task 6** below (Identities/Review wired
under the shell) — they're the same refactor.

### Touches

- `web_ui/shell.py` — confirm sticky CSS rules.
- `review_page.py` — refactor renderers to return body-only.
- `__main__.py` route handlers: `/review`, `/review-track`, and
  `/alert/<event_id>` updated to wrap content in the shell.
- Tests in `tests/test_web_ui.py` and `tests/test_review_page.py` updated
  for the new structure.

### Open

- Should the legacy `/` status page also be brought under the shell? Argued
  yes (so /diagnostics' "legacy status" link is a tab not a different look),
  but that's part of dissolving Diagnostics — separate from this task.
- The per-alert page is reached via HA Companion notifications and lives
  under both `/alert/<id>` (add-on direct) and `/api/kukiihome/alert/<id>`
  (HA Core proxy view). Both should render under the shell; the proxy view
  just re-serves the same HTML so it'll inherit automatically.

### Done when

- Header is visually pinned on every page (Home, Activity, Areas, Intent,
  Policies, Identities/Review, track-detail, alert, Diagnostics, per-camera).
- Nav links work from inside any detail view; back button is never needed.
- Manual scroll test on Review (which has the most page-content) confirms
  header stays put.

---

## Task 5 — Pool cam events missing; aspect-ratio handling

**Intent.** Two related items in one user observation:

- *"Why am I only seeing events from front_south and not pool cam?"* — pool
  cam events should be in the activity stream if pool cam is generating
  events at all.
- *"How does the UI handle different aspect ratios from these two
  cameras?"* — 4K top-down pool vs side-view 1080p front-south. Thumbnails
  and clip players need to render both gracefully.

### Diagnosis (5a — pool cam missing)

Likely causes, in decreasing probability:

1. **Pool cam events are being *auto-dismissed* and the activity row code
   isn't surfacing passive events**. Check `_alert_is_action()` in
   `web_ui/home.py` — passives should show but muted.
2. **Pool cam motion isn't reaching the alert log.** The alert log is
   populated when the triage layer fires; pool cam might be motion-only
   (recorded as events on disk but never alerts).
3. **`alert_log.recent()` is capped at 100** and pool cam events have
   aged out.
4. **Pool cam camera_id mismatch** between the recorder and the alert
   feed (e.g. `pool` vs `pool_cam`).

Investigation steps (do in order):
1. `gh ssh <add-on host>` (or open the add-on logs) and confirm pool cam
   events are firing — look for `event_recorder.persisted cam=pool`.
2. Query the alert log JSON file directly: `cat
   /data/kukiihome/alerts.json | jq '.[].camera_id' | sort -u`.
3. Confirm filter on home page: passive ✓ is the default; pool cam
   passive should render.
4. If passives are firing but not surfacing: trace whether they reach
   `alert_log.record()` from the triage layer.

### Diagnosis (5b — aspect ratio)

Today (in the v2 UI skeleton):

- Activity rows: no thumbnails yet (just a verbal headline). When thumbnails
  are added — *and they will be once Task 1's clip-player work lands* —
  they need to handle varying source aspect ratios.

Two cameras' likely native sizes:
- Pool cam (top-down 4K Reolink): 3840×2160 = 16:9, but visually portrait
  due to top-down framing (a person looks tall + narrow within the frame).
- Front-south (side-view 1080p): 1920×1080 = 16:9, with subjects roughly
  centered.

### Approach (5a fix)

If 5a's cause is (1) or (2), the fix is wiring — make sure pool cam
auto-dismissed events appear in the passive lane, and that motion-only
camera events make it into `alert_log` (today they may only land on disk).

A new principle worth committing to: **every event the system reasons about
(or chooses not to reason about) must surface in the activity stream as at
least a passive row — even if no alert was sent.** That's the trust
contract from Part III §17. The current alert_log feed is too narrow;
should be widened to "all events" with a derived "had outcome" flag.

### Approach (5b: aspect-ratio UI handling)

Standardize on a **16:9 thumbnail container** for activity rows with
`object-fit: cover` (crop) and a **flexible aspect-ratio video container**
for the modal clip player (`max-width` constrained, native aspect
preserved).

For top-down pool cam thumbnails, `object-fit: cover` will crop heavily;
this is acceptable for a thumbnail — the user clicks to play the real clip
in its native aspect.

For trace-page snapshots and detail views: respect the source aspect ratio,
constrain by `max-width: 480px` (or similar), let height adapt.

For *track* crops (the existing GIF builder), keep the existing 240×320
letterbox — it's per-track, not per-camera.

### Touches

- Investigate alert_log feed scope (5a): `services/ha-agent/.../triage.py`,
  `event_recorder` → `alert_log.record()` plumbing.
- New principle in `web-ui-design.md` §17: *"all reasoned events surface in
  the activity stream, at minimum as passive."*
- CSS rules in `web_ui/shell.py::_STYLE` for thumbnail aspect-ratio
  containers — TBD with Task 1.

### Open

- Whether the alert_log should be widened in scope or a new feed should be
  added for "reasoned-events stream." The data is already on disk
  (event_store_dir); the question is what feeds the home page query.
- Should pool cam events flag the camera as life-safety / AttentionMode?
  That'd change the passive→action treatment (Part II §11). Not a UI
  question per se but adjacent.

### Done when

- Pool cam events appear in the home activity stream when they fire
  (whether action or passive).
- Activity-row thumbnails handle 16:9 and top-down 16:9 gracefully
  (`object-fit: cover`).
- Clip modal preserves native aspect (no distortion).
- A documented principle in design doc §17 covers "every reasoned event
  surfaces."

---

## Task 6 — Wire the Identities page under the shell

**Intent.** Clicking "Identities" in the nav currently jumps to the
standalone `/review` page, which has its own header / look / nav. Feels
like jumping out of the app — should feel like a tab within the app.

This is functionally a subset of Task 4 (sticky nav) but worth listing
separately because it's the most visible inconsistency.

### Approach

Wrap `/review` and `/review-track` in the unified shell — same refactor
described in Task 4. The Review page's existing styles (review-specific
chrome like the merge form, label form, card grid) stay; only the *outer
chrome* (header, nav, page background) gets replaced with the shell.

The track-detail page becomes "Identities → Track (detail)" — the nav
shows Identities highlighted; the page body shows the track-specific UI.

### Touches

Same as Task 4. Specifically:

- `review_page.py::render_review_html()` — return body only.
- `review_page.py::render_track_detail_html()` — return body only.
- `__main__.py` `/review` and `/review-track` handlers — wrap in
  `render_shell("review", body_html, version=__version__)`.
- Move the `_STYLE` block from `review_page.py` either:
  - inline as a `<style>` tag in the returned body (per-page scoped) — quick,
  - or merge into `web_ui/shell.py::_STYLE` (cleaner, more global).
- Tests updated for new structure.

### Open

- The Review page has a flash-message convention (`?labeled=…&n=…` query
  params, etc.). The shell already supports a `flash` parameter to
  `render_shell()`. Use that consistently.
- Should the track-detail page actually be a separate "Track" page in the
  nav, or stay under Identities? Stay under Identities (sub-page);
  navigation breadcrumb in the body if needed.

### Done when

- `/review` and `/review-track` render with the unified shell + sticky nav.
- The "Identities" nav link is highlighted on those pages.
- All existing Review functionality (label / reject / merge / track detail
  with clip + candidates) works unchanged.
- The visual transition between Home → Identities feels like the same app.

---

## Task 7 — Make `/activity` (Part IV) a real page

**Intent.** Home page's "↓ See all activity" link points at `/activity`,
which is currently a "Coming soon" mock. The home page is incomplete until
"See all" leads somewhere real.

This is the formal Part IV ratification + initial build.

### Approach

Build Part IV — Activity depth & filters — as the real activity page.
Shares row schema with Home (same `_render_activity_row()`), adds:

- **Full chronological list** (not capped at 6).
- **Filter chips** at the top (sticky too — sub-header below the main nav):
  - `Passive ✓ · Actions ✓` (lane toggles, both on by default per Part III §17)
  - `Camera ▾` (multi-select dropdown)
  - `Person ▾` (multi-select; sourced from `/identity/subjects`)
  - `Kind ▾` (person / vehicle / pet / package / motion)
- **"Show fragments" toggle** (currently in identity store; surface here too).
- **Load earlier** button at the bottom, paginated.
- **Search box** — deferred to a later iteration (Part IV mentions it as
  separate work).

### Touches

- New: `services/ha-agent/src/.../web_ui/activity.py`. Mirrors `home.py`'s
  structure. Reuses `_render_activity_row()` from home.py (extract that to
  a shared module `web_ui/activity_row.py` or `web_ui/_shared.py`).
- `__main__.py::v2_activity()` handler: replaces the mock; pulls the full
  alert log + applies query-string filters; renders with the shell.
- New CSS in `shell.py::_STYLE` for filter chips + load-more button.
- Tests: `tests/test_activity_page.py` covering filter combinations.

### Open

- Pagination model: cursor-based (since alert_log isn't queryable by ID
  range) or offset-based? Probably offset for v1.
- Filter persistence: per design doc, default off (resets on visit). Confirm.
- Camera and Person filter dropdowns: rendered as `<select multiple>` for
  v1 (lo-fi but functional). Polish later.

### Done when

- `/activity` renders the full chronological list of alerts with the same
  row schema as Home.
- The four filter chips work; defaults match Home (everything on).
- "Load earlier" works (pagination).
- Home's "↓ See all activity" link lands here meaningfully.
- The row data structure is shared with Home (no duplication).

---

## Task 8 — Next-step planning after this round

**Updated 2026-06-04**: Rules has been pulled into Iteration 1 (Task 9
below). Preferences + Policies remain for Iteration 2.

After Tasks 1-7, 9 land, the natural next iteration is the rest of Part VI
plus Part VII:

- **Iteration 2.A — Preferences** (dials + free-text + per-actor
  relationship + per-area posture; persona-shaping that complements the
  named Rules from Task 9). Already sketched in web-ui-design.md §VI.
- **Iteration 2.B — Policies (Part VII)**. The VLM-authored dismissal
  policies + transient intents view + revoke + audit. Pairs naturally with
  Rules — both surface as "active behaviour the agent has," with Rules
  user-authored and Policies VLM-authored.
- **Iteration 2.C — Areas (Part V)**. The metadata page; can stay as a
  placeholder a bit longer because it doesn't carry user-driven action.

This document (web-ui-iteration-1.md) is the spec for **Iteration 1**.
Iteration 2 begins as `planning/web-ui-iteration-2.md` once Iteration 1's
tasks land.

---

## Task 9 — Rules editor (Part VI: Intent · Rules)

**Intent.** Build the Rules half of Part VI — *named, scoped,
natural-language intents the VLM evaluates per-event with explicit
actions.* The user writes prose ("Alert me if Winston seems to have gotten
outside without someone watching him"); the system gates the evaluation by
scope (camera / area / time); the VLM judges whether the situation
matches; on match, the dispatcher fires the rule's action(s) (built-in
alert + a structured HA event for downstream automations).

This is the single most novel surface in the product — the place where the
agent stops feeling like a security alarm and starts feeling like an
*agent you can talk to.* It's also the page that turns the "no manual
alert rules" architectural commitment from §1.5 of web-ui-design.md into a
concrete user experience.

### Architectural model (recap from web-ui-design.md §VI)

A **rule** has three parts:

| Layer | Form | Deterministic? |
|---|---|---|
| **Scope** | camera / area / time-window picker | Yes — gates *when* the VLM evaluates this rule |
| **Intent** | free-text user prose | No — the VLM reads it as guidance, decides match per-incident |
| **Action** | built-in alert + optional HA event fire | Yes — once matched, the dispatcher executes deterministically |

Rules are evaluated as part of the existing VLM call on each incident.
**No per-rule VLM calls** — the active rules in scope are folded into the
single VLM prompt; the structured output adds a `matched_rules` field;
the dispatcher reads that field and fires actions. One call, many rules.

### Data model

A new SQLite store in ha-agent (co-located with the existing alert log
persistence under `/data/kukiihome/`). Two tables:

```sql
CREATE TABLE rules (
  id              TEXT PRIMARY KEY,           -- slug derived from name
  name            TEXT NOT NULL,              -- display name
  enabled         INTEGER NOT NULL DEFAULT 1,
  scope_json      TEXT NOT NULL,              -- {cameras:[], areas:[], time_windows:[]}
  intent_text     TEXT NOT NULL,              -- the prose the VLM reads
  severity        TEXT NOT NULL DEFAULT 'normal',  -- critical | normal | low
  alert_enabled   INTEGER NOT NULL DEFAULT 1, -- built-in push?
  ha_event_name   TEXT,                       -- e.g. 'kukiihome.winston_unsupervised'; NULL = no HA event
  created_at      REAL NOT NULL,
  updated_at      REAL NOT NULL,
  matched_count   INTEGER NOT NULL DEFAULT 0, -- denormalized counter
  last_matched_at REAL,
  retired_at      REAL                        -- soft-delete; NULL while active
);

CREATE TABLE rule_matches (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id      TEXT NOT NULL,
  incident_id  TEXT NOT NULL,                 -- joins to alert_log
  matched_at   REAL NOT NULL,
  confidence   REAL,                          -- VLM's reported match confidence
  reasoning    TEXT,                          -- VLM's brief explanation
  action_fired TEXT,                          -- 'alert', 'ha_event', 'alert+ha_event', or 'gated'
  FOREIGN KEY (rule_id) REFERENCES rules(id)
);
CREATE INDEX idx_match_rule ON rule_matches(rule_id, matched_at DESC);
CREATE INDEX idx_match_inc  ON rule_matches(incident_id);
```

**`scope_json` shape:**

```json
{
  "cameras": ["front_south", "pool"],         // empty list = any camera
  "areas":   ["front_yard"],                  // empty list = any area
  "time_windows": [
    {"days": ["mon","tue","wed","thu","fri"], "start": "09:00", "end": "17:00"},
    {"days": ["sat","sun"], "start": "00:00", "end": "23:59"}
  ]                                           // empty list = any time
}
```

All three scope fields are **AND-combined**; within each field the values
are **OR-combined**. *"Any time"* / *"any camera"* / *"any area"* are
represented by an empty list. Time windows are in the user's local
timezone (read from add-on options).

### Triage / VLM prompt integration

This is the load-bearing backend change. The triage layer
(`services/ha-agent/.../triage.py`) is extended in three places:

**1. Rule activation per event.** When an event arrives, before calling the
VLM, triage:
- Loads enabled, non-retired rules from the rules table (cached, refreshed
  on rule create/update/delete via an in-memory copy + change pubsub).
- Filters by scope: keeps rules whose camera/area/time-window matches the
  event's `(camera_id, area_id, ts)`.
- The resulting active rule set is passed into VLM prompt assembly.

**2. Prompt assembly.** A new section in the VLM prompt — *Named user
intents* — lists each active rule as:

```
[rule:R3] "Winston unsupervised in front"
  Intent: Winston seems to have gotten outside in front without someone
          watching him.
```

The prompt instructs the VLM to evaluate each named intent against the
situation and emit a `matched_rules` field in its structured output.

**3. Structured output schema extension.** `VLMResponse` (per
web-ui-design.md spec) gains:

```json
"matched_rules": [
  {
    "rule_id": "R3",
    "matched": true,
    "confidence": 0.87,
    "reasoning": "no adult human visible in scene; pet appears to have exited unattended."
  },
  {
    "rule_id": "R7",
    "matched": false,
    "confidence": 0.0,
    "reasoning": null
  }
]
```

The dispatcher reads this, looks up each `matched: true` rule, and fires
its action(s) (alert + HA event) if `confidence >= threshold` (default
0.6, configurable per-rule later). Every evaluation — match or non-match —
is recorded in `rule_matches` so the audit log shows the full picture.

### Action authority — when rule and VLM both speak

If a rule fires AND the VLM also emits standalone `recommendations` in the
same call, **the rule's action takes priority** (deterministic, what the
user asked for). The VLM's contemporaneous recommendation is recorded in
the trace as *"VLM also suggested: …; rule action took priority"* but is
not enacted. The trace shows both, the dispatcher acts on one.

### HA event payload shape

When a rule fires `ha_event_name`, the dispatcher emits an HA event with
this payload (subscribable by HA automations):

```json
{
  "rule_id": "winston_unsupervised",
  "rule_name": "Winston unsupervised in front",
  "incident_id": "inc_abc123",
  "camera_id": "front_south",
  "area_id": "front_yard",
  "ts": 1717512637.4,
  "kind": "person",          // or pet, vehicle, etc. — primary detection
  "actors": ["winston"],     // resolved KnownActor/KnownPet ids
  "severity": "critical",
  "confidence": 0.87,
  "trace_url": "/api/kukiihome/alert/inc_abc123"   // HA-Core proxy, signed
}
```

HA automations key on `event_type: kukiihome_event` + the event-name
discriminator (e.g. `data.rule_id == "winston_unsupervised"`).

### REST API surface

New endpoints on the existing ha-agent HTTP API:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/intent/rules` | List all rules (filter `?enabled=true/false&retired=true/false`) |
| `POST` | `/api/intent/rules` | Create rule; body = full rule object minus id/timestamps; id derived from name |
| `GET` | `/api/intent/rules/{id}` | Read rule (including recent matches summary) |
| `PUT` | `/api/intent/rules/{id}` | Update rule (any field except id; updates `updated_at`) |
| `DELETE` | `/api/intent/rules/{id}` | Soft-delete: sets `retired_at`; preserves audit history |
| `POST` | `/api/intent/rules/{id}/enable` | Toggle `enabled` field |
| `GET` | `/api/intent/rules/{id}/matches` | Paginated audit log of matches (newest first) |
| `POST` | `/api/intent/rules/{id}/test` | Dry-run: replay the most recent eligible incident through the VLM with this rule's intent, return matched/confidence/reasoning *without* writing to the matches table or firing actions |

The `test` endpoint is the "preview before save" UX — lets the user paste
intent text, see how the VLM evaluates it against a real recent incident,
iterate, then save when it reads right.

### UI

Lives at `/intent` (replaces the current mock). Two sections, top-to-bottom:

**Top — Preferences** (placeholder section, becomes real in Iteration 2.A):

```
─── PREFERENCES ──────────────────────────────────────────────
   (Coming in Iteration 2 — vigilance dial, "what I care about"
   free text, per-actor relationships, per-area posture.)
```

This keeps the page from feeling lopsided while Preferences is being
designed; the Rules section gets full attention now.

**Bottom — Rules** (built in this task):

```
─── RULES ────────────────────────────────────────  [+ New rule]

━━ Winston unsupervised in front ━━━━━━ critical · enabled ●
   WHEN  Front Yard · any time
   ALERT IF  "Winston seems to have gotten outside in front
              without someone watching him."
   → alert (built-in) + fires kukiihome.winston_unsupervised
   ↳ matched 2 times this month · last match Tuesday at 6:21 PM
   [Edit] [Disable] [View matches] [Delete]

━━ Bob arrives ━━━━━━━━━━━━━━━━━━━━━━━ critical · enabled ●
   WHEN  any camera · any time
   ALERT IF (structured shortcut: Bob seen)
   → alert (built-in) + fires kukiihome.bob_arrives
   ↳ matched 14 times this month · last match 23 minutes ago
   [Edit] [Disable] [View matches] [Delete]

━━ Delivery at front door ━━━━━━━━━━━ event-only · enabled ●
   WHEN  front_south · any time
   ALERT IF  "A delivery happened — a person dropped a package and
              left within a few seconds."
   → fires kukiihome.delivery_at_front_door (no built-in alert)
     ↳ HA automation: Sonos chime + lights red 1h  [Open in HA]
   ↳ matched 8 times this month
   [Edit] [Disable] [View matches] [Delete]

ⓘ Rules are fast-path intents the VLM evaluates per event. Anything
  not matched by any rule is reasoned about by the VLM under your
  general preferences (Iteration 2).
```

**The rule editor — new + edit form**:

```
┌ New rule ─────────────────────────────────────────────[ × ]┐
│  Name        [Winston unsupervised in front           ]    │
│              ↳ rule id: winston_unsupervised_in_front       │
│                                                             │
│  WHEN  (scope — when to evaluate this rule)                │
│    Camera / Area  [Front Yard ▾]  (multi-select)            │
│                   ☐ Apply to any camera                     │
│    Time           ☑ Any time                                │
│                   ↳ or pick windows…                        │
│                                                             │
│  ALERT IF                                                   │
│   ┌──────────────────────────────────────────────────────┐ │
│   │ Winston seems to have gotten outside in front        │ │
│   │ without someone watching him.                        │ │
│   └──────────────────────────────────────────────────────┘ │
│   The VLM evaluates this intent against the situation.     │
│   [Try against the most recent eligible incident ↓]        │
│   (After click: shows VLM verdict + reasoning inline)      │
│                                                             │
│  THEN                                                       │
│   ☑ Alert (built-in push notification)                     │
│   ☑ Fire HA event: kukiihome.winston_unsupervised          │
│      ↳ auto-suggested from name; editable                  │
│   Severity:  ◯ Low  ◉ Normal  ◯ Critical                   │
│                                                             │
│                              [Cancel]  [Save & enable]      │
└─────────────────────────────────────────────────────────────┘
```

For the simple identity-match case (*"alert me when Bob arrives"*) the
form offers a **structured shortcut**: pick *"Subject seen → alert"* from
a dropdown at the top of the form, pick a subject, and the rest of the
form auto-fills (intent_text becomes `Subject seen anywhere within scope`,
which is a no-op the VLM matches trivially). The shortcut is a UX win for
the trivial case; the full form is the main shape.

**Per-rule matches page** (`/intent/rules/{id}/matches`):

```
┌ Matches · Winston unsupervised in front ─────[× Close] ┐
│                                                          │
│  Recent matches                                          │
│                                                          │
│  Tuesday at 6:21 PM  [thumb] Front Yard                  │
│    confidence 0.87 · "no adult human visible in scene;   │
│    pet appears to have exited unattended."               │
│    → alert sent  ⓘ open trace                            │
│                                                          │
│  May 19 at 4:02 PM  [thumb] Front Yard                   │
│    confidence 0.91 · …                                   │
│    → alert sent  ⓘ open trace                            │
│                                                          │
│  Recent non-matches (sample)                             │
│  Mon at 11:14 AM  conf 0.12  "Winston with Alice nearby" │
└──────────────────────────────────────────────────────────┘
```

Both matches and a sample of non-matches surface — the non-matches are
where the trust contract lives: *"the rule was evaluated, the VLM said
no, here's why."*

### Loop 1 feedback on rules

A ✗ on a rule-matched alert *also* surfaces a *"this rule matched but you
said no. Want to refine the intent?"* prompt with an inline editor showing
the current intent text and a one-tap *Refine* that opens the rule's edit
form pre-focused on the intent textarea. The rule literally **learns its
own wording** through user corrections — exactly the Loop 1 closure that
makes Preferences and Rules architecturally distinct from policies.

### Touches

| File | Change |
|---|---|
| `services/ha-agent/src/.../rules_store.py` | **NEW** — SQLite-backed rule + match storage; mirrors `alert_log` shape |
| `services/ha-agent/src/.../rules_runtime.py` | **NEW** — in-memory rule cache, scope-filter helper, prompt-section builder |
| `services/ha-agent/src/.../triage.py` | extend: filter rules by scope; pass active rules into VLM call; read `matched_rules` from response; record matches; fire actions |
| `services/ha-agent/src/.../reasoning.py` (or the VLM prompt builder) | add the *Named user intents* section to the prompt; update output schema |
| `services/ha-agent/src/.../http_api.py` | add the `/api/intent/rules/*` endpoints |
| `services/ha-agent/src/.../notifier.py` | new "rule-matched alert" path (carries rule context into push body) |
| `services/ha-agent/src/.../client.py` (HA client) | new `fire_event(name, data)` method calling HA's `/api/events/{name}` |
| `services/ha-agent/src/.../web_ui/intent.py` | **NEW** — `/intent` page renderer (Rules section live, Preferences placeholder) |
| `services/ha-agent/src/.../web_ui/intent_rule_form.py` | **NEW** — new + edit form rendering + parsing |
| `services/ha-agent/src/.../web_ui/mocks.py` | remove the Intent mock |
| `services/ha-agent/src/.../__main__.py` | wire routes for `/intent`, `/intent/rules/new`, `/intent/rules/{id}`, `/intent/rules/{id}/matches`, and the POST handlers |
| `services/ha-agent/src/.../web_ui/shell.py` | add form CSS (textarea, multi-select chips, severity radio) |
| `planning/web-ui-design.md` | ratify Part VI Rules section based on the design decisions in this task |
| `tests/test_rules_store.py` | **NEW** — store CRUD + scope-filter + audit log |
| `tests/test_rules_runtime.py` | **NEW** — prompt-section building, response parsing, action gating |
| `tests/test_intent_page.py` | **NEW** — page renderers + form parsing |
| `tests/test_triage_rules.py` | **NEW** — integration test for triage → rules → dispatcher |

### Open

These are the design questions worth resolving with the user before
implementation. Each has a recommended default in brackets; flagging so a
fresh context knows what to ask.

- **Storage location.** ha-agent local SQLite vs HA-Core (as automations).
  [Recommend ha-agent SQLite — co-locates with dispatcher, doesn't pollute
  HA's automation list, survives HA restart, queryable from the trace UI.
  HA still consumes the *output* via fired events.]
- **Prompt cost.** With N rules, prompt grows. At ~50 tokens per rule, 20
  rules = ~1k extra prompt tokens per VLM call. [Recommend: no per-rule
  budget for v1; surface as a knob if it becomes painful. The cheap
  per-call tier_0 evaluation is exactly what makes natural-language rules
  economically viable.]
- **VLM cost** of the `/test` (dry-run) endpoint. Each test call is one
  full VLM round-trip. [Recommend: rate-limit to N tests per minute per
  user; cache the most-recent-incident pull for 60 s so iterating on a
  rule's intent text doesn't refire context assembly.]
- **Match confidence threshold.** Global 0.6 default; per-rule override.
  [Recommend: global default in config; per-rule override only when a
  rule consistently misfires — pair with Loop 1 feedback.]
- **Structured shortcut for identity rules.** Stay within the same form
  (radio at top: *"natural-language intent"* vs *"subject seen → alert"*)
  or a separate "Quick add" flow. [Recommend: same form with a top-of-
  form mode selector — fewer routes, less code, the simple case stays
  one-click.]
- **HA automation discovery.** Should the rule list show which HA
  automations subscribe to its fired event? [Recommend: nice-to-have, not
  required for v1. The "Open in HA" link is enough.]
- **Rule retirement / soft-delete.** Deleted rules go to `retired_at` but
  stay queryable for audit. UI shows them on a *Retired* tab. [Recommend:
  yes — audit-friendly, undoable.]
- **Suggested rules from observed patterns.** "We notice you keep
  dismissing delivery events — want a rule for that?" [Defer to a later
  iteration; depends on dismissal-policy + feedback loop infra. Out of
  scope for Task 9.]
- **Multiple rules matching one incident.** All fire (multiple alerts +
  multiple HA events). [Recommend: yes — explicit user concerns are
  additive. The trace shows all matched rules for transparency.]

### Done when

- User can create, edit, enable/disable, delete a rule via the `/intent`
  page using the form sketched above.
- The `Try against the most recent eligible incident` dry-run works and
  returns a real VLM verdict + reasoning inline.
- Triage assembles the active-rules section into the VLM prompt; the VLM
  returns `matched_rules`; the dispatcher fires the rule's actions on
  matches above threshold.
- A rule-matched alert lands on HA Companion *and* fires the configured
  HA event with the documented payload shape.
- Every rule evaluation (match or non-match) is recorded in `rule_matches`
  and shown on the per-rule matches page.
- The activity trace page renders *"matched rule X"* lines in the audit
  chain alongside the VLM call, with each matched rule's name + confidence
  + reasoning, exactly as scoped in web-ui-design.md §22.
- Loop 1: a ✗ on a rule-matched alert surfaces the *"refine intent"* prompt
  and lets the user edit the rule's text inline.
- `planning/web-ui-design.md` §VI Rules section is updated to reflect any
  design choices made during implementation that differ from the current
  spec (especially the action-priority rule and the matched_rules schema).

---

## Implementation order recommendation

If a fresh context picks this up, I'd build in this order — lighter and
non-architectural first, two big arcs (Task 1, Task 9) at the end:

1. **Task 2** (timestamp formatting) — pure helper change, low risk, immediately visible.
2. **Task 3** (headline cleanup) — pure rendering change, low risk, immediately visible.
3. **Task 4 + 6** (sticky nav + Identities under shell) — one refactor, two wins.
4. **Task 7** (build real /activity) — formal Part IV, builds on row schema from Home.
5. **Task 5** (pool cam diagnosis + aspect-ratio principles) — diagnostic + small CSS, may surface follow-ups.
6. **Task 9** (Rules editor) — large multi-session arc; should resolve its
   open questions (storage location, prompt cost, dry-run rate-limit) with
   the user before code lands. Mostly self-contained — touches triage but
   not the existing UI structure.
7. **Task 1** (event clips, architectural) — biggest, scope-out per the
   Design A/B/stop-gap framing. Order may flip with Task 9 depending on
   which the user wants to feel sooner.

Tasks 2–4, 6–7 land cleanly in a single session each. Tasks 1 and 9 are
their own multi-session arcs and should be planned + scoped before code
starts. Task 9 has more open product-design questions to resolve with the
user; Task 1 has more open systems-engineering questions to resolve
internally (codec, retention, AD integration depth).

---

## File-by-file change summary (for quick reference)

| File | Tasks touching it |
|---|---|
| `services/ha-agent/src/.../web_ui/shell.py` | 2 (friendly_time), 3 (camera_display_name), 4 (sticky CSS), 7 (filter chip CSS), 9 (form CSS) |
| `services/ha-agent/src/.../web_ui/home.py` | 2 (use friendly_time), 3 (headline + where line) |
| `services/ha-agent/src/.../web_ui/activity.py` | 7 (new file) |
| `services/ha-agent/src/.../web_ui/intent.py` | 9 (new file — Rules section + Preferences placeholder) |
| `services/ha-agent/src/.../web_ui/intent_rule_form.py` | 9 (new file — new + edit form render + parse) |
| `services/ha-agent/src/.../web_ui/mocks.py` | 7 (remove activity mock), 9 (remove intent mock) |
| `services/ha-agent/src/.../review_page.py` | 4, 6 (body-only renderers) |
| `services/ha-agent/src/.../__main__.py` | 4, 6, 7, 9 (wrap legacy routes; real activity + intent handlers + rule POSTs) |
| `services/ha-agent/src/.../rules_store.py` | 9 (new file — SQLite-backed rules + matches) |
| `services/ha-agent/src/.../rules_runtime.py` | 9 (new file — in-memory cache, scope filter, prompt builder) |
| `services/ha-agent/src/.../triage.py` | 9 (rule filter + prompt extension + match recording + dispatcher hook) |
| `services/ha-agent/src/.../reasoning.py` | 9 (VLM prompt + output schema for matched_rules) |
| `services/ha-agent/src/.../http_api.py` | 9 (/api/intent/rules/* CRUD + test + matches) |
| `services/ha-agent/src/.../notifier.py` | 9 (rule-matched alert path) |
| `services/ha-agent/src/.../client.py` | 9 (HA fire_event method) |
| `services/preprocessor/src/.../event_recorder.py` | 1 (clip writer integration) |
| `services/preprocessor/src/.../clip_writer.py` | 1 (new file) |
| `services/preprocessor/src/.../app.py` | 1 (clip serve endpoint) |
| `services/ha-agent/.../preprocessor_client.py` | 1 (clip fetch method) |
| `planning/web-ui-design.md` | 5 (new §17 principle), 7 (Part IV ratification), 9 (Part VI Rules ratification) |
| `tests/test_web_ui.py` | 2, 3, 4, 7 |
| `tests/test_review_page.py` | 4, 6 |
| `tests/test_rules_store.py` | 9 (new file) |
| `tests/test_rules_runtime.py` | 9 (new file) |
| `tests/test_intent_page.py` | 9 (new file) |
| `tests/test_triage_rules.py` | 9 (new file — integration) |

Conventional Commit prefixes (per the auto-release workflow):

- Tasks 2, 3, 4, 6: `fix(web-ui): …` or `refactor(web-ui): …` — patch bumps.
- Task 7: `feat(web-ui): build real activity page` — minor bump.
- Task 5: `fix(activity): surface pool cam events in the home stream` — patch.
- Task 1: `feat(events): clip recording + browser playback` — minor.
