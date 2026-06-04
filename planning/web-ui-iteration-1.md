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
natural-language intents the VLM evaluates per-event.* The user writes
prose ("Alert me if Winston seems to have gotten outside without someone
watching him"); the system gates the evaluation by scope (camera / area /
time); the VLM judges whether the situation matches AND at what severity;
on match, the dispatcher fires the **outcome notification** (class 4 — a
`kukiihome_alert` event HA routes by severity) and optionally executes
the agent's authorized **protective actions** (class 3 — lock/siren/
floods within the per-camera whitelist + policy, see web-ui-design.md
§7.7). HA automations branch on severity for delivery (phone, Sonos,
sirens, etc.).

This is the single most novel surface in the product — the place where the
agent stops feeling like a security alarm and starts feeling like an
*agent you can talk to.* It's also the page that turns the "no manual
alert rules" architectural commitment from §1.5 of web-ui-design.md into a
concrete user experience.

### Architectural model

A **rule** has three parts. **Severity is reasoned by the VLM** for natural-
language rules (so a same-rule match can fire `critical` at midnight and
`normal` at noon), and **statically set** for structured-shortcut rules
(simple identity match — *"Bob seen → critical"* — no VLM call needed).

| Layer | Form | Deterministic? |
|---|---|---|
| **Scope** | camera / area / time-window picker | Yes — gates *when* the VLM evaluates this rule |
| **Intent** | free-text user prose | No — the VLM reads it as guidance, decides match + severity per-incident |
| **Severity mode** | "let the VLM decide" (NL rules) or static picker (shortcut rules) | Yes (in shortcut mode) / No (in NL mode) |
| **Action** | none — outcome alert always fires via class-4 event | The agent owns alert emission; HA owns delivery; class-3 protective actions are configured per-camera, not per-rule (§7.7) |

Rules are evaluated as part of the existing VLM call on each incident.
**No per-rule VLM calls** — the active rules in scope are folded into the
single VLM prompt; the structured output adds a `matched_rules` field with
both `matched: bool` and `severity: critical|normal|low`; the dispatcher
reads that field and emits the alert event (and authorizes class-3 actions
per the per-camera whitelist). One VLM call, many rules.

### Why rules don't carry their own action list anymore

**Earlier draft of this task had each rule carry an `alert_enabled` flag
+ a custom HA event name + a severity field.** That was wrong on three
counts:

1. **The built-in alert path duplicated HA's notify ecosystem** — and HA's
   is unambiguously better (Companion, Sonos, Telegram, etc.).
2. **The custom-event-per-rule pattern** ("`kukiihome.winston_unsupervised`")
   forces HA automations to fan out across many event types instead of
   routing one event by severity.
3. **Severity-as-rule-property** was deterministic — but severity is
   genuinely *reasoned* for natural-language rules. The same intent can
   match at different severities depending on the situation.

Corrected: one `kukiihome_alert` event per match, severity in the payload,
HA automations branch on severity (see web-ui-design.md §7.7).

### Data model

A new SQLite store in ha-agent (co-located with the existing alert log
persistence under `/data/kukiihome/`). Two tables:

```sql
CREATE TABLE rules (
  id               TEXT PRIMARY KEY,                -- slug derived from name
  name             TEXT NOT NULL,                   -- display name
  enabled          INTEGER NOT NULL DEFAULT 1,
  mode             TEXT NOT NULL,                   -- 'nl' (natural-language, VLM-evaluated)
                                                    -- or 'shortcut' (identity-only match)
  shortcut_subject TEXT,                            -- shortcut mode: a KnownActor/KnownPet id
  scope_json       TEXT NOT NULL,                   -- {cameras:[], areas:[], time_windows:[]}
  intent_text      TEXT NOT NULL,                   -- NL mode: the prose the VLM reads
                                                    -- shortcut mode: empty (or a default phrase)
  severity_static  TEXT,                            -- shortcut mode only: critical | normal | low
                                                    -- NL mode: NULL — severity is VLM-reasoned per match
  created_at       REAL NOT NULL,
  updated_at       REAL NOT NULL,
  matched_count    INTEGER NOT NULL DEFAULT 0,      -- denormalized counter
  last_matched_at  REAL,
  retired_at       REAL                             -- soft-delete; NULL while active
);

CREATE TABLE rule_matches (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id      TEXT NOT NULL,
  incident_id  TEXT NOT NULL,                       -- joins to alert_log
  matched_at   REAL NOT NULL,
  severity     TEXT NOT NULL,                       -- the severity reasoned for THIS match
                                                    -- (== severity_static for shortcut rules,
                                                    --  VLM-reasoned for NL rules)
  confidence   REAL,                                -- VLM's reported match confidence
  reasoning    TEXT,                                -- VLM's brief explanation
  protective_actions_taken TEXT,                    -- JSON list of class-3 actions actually executed
                                                    -- by the dispatcher (filtered through whitelist)
  alert_emitted INTEGER NOT NULL DEFAULT 1,         -- did we fire the kukiihome_alert event?
                                                    -- (0 only if the rule itself was disabled mid-event)
  FOREIGN KEY (rule_id) REFERENCES rules(id)
);
CREATE INDEX idx_match_rule ON rule_matches(rule_id, matched_at DESC);
CREATE INDEX idx_match_inc  ON rule_matches(incident_id);
```

Notes on the field changes from the earlier draft:

- **Dropped `alert_enabled`** — every match fires `kukiihome_alert`. There's
  no per-rule opt-out of the agent's outbound contract; if you don't want
  an alert from a rule, retire/disable the rule.
- **Dropped `ha_event_name`** — one event type (`kukiihome_alert`), payload
  carries `rule_id` and `rule_name`. HA automations branch on severity (or
  on rule_id if they really want rule-specific routing, but that's rare).
- **Added `mode` + `shortcut_subject` + `severity_static`** — explicit
  distinction between NL rules (VLM evaluates intent + severity) and
  shortcut rules (deterministic identity match, static severity).
- **Added `protective_actions_taken`** to the match record — when a rule
  match triggers class-3 actions (dispatcher policy passes), we record
  exactly what fired so the trace shows the full chain and so audits can
  answer *"did the door actually lock?"*

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
intents* — lists each active NL rule, and the schema asks for both a
**match decision** and a **reasoned severity**:

```
[rule:R3] "Winston unsupervised in front"
  Intent: Winston seems to have gotten outside in front without someone
          watching him.
  → judge match (yes/no, confidence), and if yes, reason about severity
    (critical / normal / low) given the scene + time-of-day + context.
```

Shortcut rules (identity-only) are NOT in the prompt — they're matched
deterministically by triage before/around the VLM call and the static
severity is used.

**3. Structured output schema extension.** `VLMResponse` gains:

```json
"matched_rules": [
  {
    "rule_id": "R3",
    "matched": true,
    "confidence": 0.87,
    "severity": "critical",
    "reasoning": "no adult human visible in scene; pet appears to have exited unattended; near nightfall."
  },
  {
    "rule_id": "R7",
    "matched": false,
    "confidence": 0.0,
    "severity": null,
    "reasoning": null
  }
]
```

The dispatcher reads this. For each `matched: true` entry with
`confidence >= threshold` (default 0.6 global; per-rule override later):

- **Always emits** the `kukiihome_alert` event (class 4) with the reasoned
  severity (see payload below).
- **Optionally executes** authorized class-3 protective actions, per the
  per-camera whitelist + policy (see web-ui-design.md §7.7 — VLM's
  `recommendations` list determines candidates; the dispatcher gates them).

Every evaluation — match or non-match — is recorded in `rule_matches` so
the per-rule audit log shows the full picture.

### Action authority — when rule and VLM both speak

The VLM's standalone `recommendations` (class 3 candidates) and the rule's
matched intent (class 4 emission) are **complementary, not competing**.
A single VLM call can produce both. The dispatcher:

- Fires the `kukiihome_alert` event for **every** matched rule (severity
  comes from each rule's `matched_rules` entry).
- Authorizes class-3 protective actions from `recommendations` against
  the per-camera whitelist + policy, **independent** of which (if any)
  rule matched. So an unscoped *"this looks like an intrusion"* VLM
  judgment can still result in `lock.back_door` firing even without an
  explicit *"intruder rule"* on the user's part.

The trace surfaces all of this side-by-side: matched rules (with
severities), VLM recommendations, dispatcher policy gates, actions
executed, action results.

### HA event payload — `kukiihome_alert`

One event type, one shape. HA automations subscribe to `kukiihome_alert`
and branch on `data.severity`:

```yaml
event_type: kukiihome_alert
data:
  # === identity of the alert ===
  alert_id: "alert_abc123"            # unique per emission; for de-dupe
  incident_id: "inc_abc123"           # the incident, may produce N alerts
  rule_id: "winston_unsupervised"     # which rule matched (NULL if VLM emergent)
  rule_name: "Winston unsupervised in front"
  ts: 1717512637.4

  # === reasoning summary (for the notification body) ===
  scene_description: "Winston is in the front yard with no adult visible..."
  severity: "critical"                # critical | normal | low
  confidence: 0.87
  reasoning: "no adult human visible in scene; pet exited unattended."

  # === context ===
  camera_id: "front_south"
  camera_name: "Front South Camera"   # friendly name, suffix-stripped (Task 3)
  area_id: "front_yard"
  area_name: "Front Yard"
  kind: "person"                      # primary detection
  actors: ["winston"]                 # resolved KnownActor/KnownPet ids
  actor_names: ["Winston"]            # friendly names for the message body

  # === what the agent already did (class 3) ===
  actions_taken:
    - service: "lock.lock"
      target: "lock.back_door"
      result: "ok"
    - service: "light.turn_on"
      target: "light.backyard_floods"
      result: "ok"
      data: { color_name: "red" }

  # === references for the UI / Companion push ===
  trace_url: "/api/kukiihome/alert/inc_abc123"     # HA-Core proxy, signed
  thumbnail_url: "/api/kukiihome/alert/inc_abc123/frame.jpg"
  clip_url: "/api/kukiihome/alert/inc_abc123/clip.mp4"   # when Task 1 lands
```

HA automation pattern (severity-routed):

```yaml
trigger:
  - platform: event
    event_type: kukiihome_alert
action:
  - choose:
      - conditions: "{{ trigger.event.data.severity == 'critical' }}"
        sequence:
          - service: notify.mobile_app_darins_iphone
            data:
              title: "{{ trigger.event.data.rule_name or 'Kukii-Home alert' }}"
              message: |
                {{ trigger.event.data.scene_description }}
                {% if trigger.event.data.actions_taken %}
                Agent already: {{ trigger.event.data.actions_taken
                                 | map(attribute='target') | join(', ') }}
                {% endif %}
              data:
                push: { interruption-level: critical }
                url: "{{ trigger.event.data.trace_url }}"
                image: "{{ trigger.event.data.thumbnail_url }}"
          - service: media_player.play_media
            target: { entity_id: media_player.living_room_sonos }
            data: { media_content_id: "/media/sounds/critical-alert.mp3" }
      - conditions: "{{ trigger.event.data.severity == 'normal' }}"
        sequence:
          - service: notify.mobile_app_darins_iphone
            data:
              title: "{{ trigger.event.data.rule_name or 'Kukii-Home alert' }}"
              message: "{{ trigger.event.data.scene_description }}"
              data:
                url: "{{ trigger.event.data.trace_url }}"
      - conditions: "{{ trigger.event.data.severity == 'low' }}"
        sequence:
          - service: logbook.log
            data:
              name: "Kukii-Home"
              message: "{{ trigger.event.data.scene_description }}"
```

### Status entities (small, for Lovelace; not the dispatch path)

In addition to the event, the integration exposes a small set of *status*
entities for dashboards. **These are NOT the primary alert dispatch
surface** — they're for at-a-glance visibility:

- `binary_sensor.kukiihome_alert_active` — `on` if any unacknowledged alert
  in the last 5 minutes. Useful for a *"is anything happening?"* Lovelace
  tile.
- `sensor.kukiihome_last_alert_severity` — value is `critical` / `normal`
  / `low` / `none`. For badge coloring.
- `sensor.kukiihome_alerts_today` — integer count.

No per-rule entities. Rules are payload, not entity proliferation.

### Default automation shipped with the integration

A single blueprint, importable on first-run setup:

```yaml
blueprint:
  name: Kukii-Home alert (severity-routed)
  description: Routes reasoned Kukii-Home alerts to your delivery channels by severity.
  domain: automation
  input:
    notify_target:
      name: Mobile notify service
      selector: { entity: { domain: notify } }
    critical_audio:
      name: (Optional) Sonos for critical alerts
      default: ~
      selector: { entity: { domain: media_player } }
    critical_lights:
      name: (Optional) Lights for critical alerts
      default: ~
      selector: { entity: { domain: light } }
  trigger: { platform: event, event_type: kukiihome_alert }
  action:
    # severity-branched delivery as above
```

The first-run wizard in the integration prompts: *"Pick a mobile notify
service to receive Kukii-Home alerts on, and optionally a Sonos / lights
for critical-severity alerts."* That installs the blueprint pointed at
their picks. They can edit / extend it from there — wire a siren for
critical, color the lights based on severity, add custom announcements,
whatever.

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

━━ Winston unsupervised in front ━━━━━━ severity: VLM-reasoned · enabled ●
   WHEN  Front Yard · any time
   ALERT IF  "Winston seems to have gotten outside in front
              without someone watching him."
   ↳ matched 2 times this month · last match Tuesday at 6:21 PM
      severities observed: normal (1), critical (1)
   [Edit] [Disable] [View matches] [Delete]

━━ Bob arrives ━━━━━━━━━━━━━━━━━━━━━━━ severity: critical (static) · enabled ●
   WHEN  any camera · any time
   ALERT IF  Bob seen   (structured shortcut — identity match)
   ↳ matched 14 times this month · last match 23 minutes ago
   [Edit] [Disable] [View matches] [Delete]

━━ Delivery at front door ━━━━━━━━━━━ severity: VLM-reasoned · enabled ●
   WHEN  Front South · any time
   ALERT IF  "A delivery happened — a person dropped a package and
              left within a few seconds."
   ↳ matched 8 times this month · always reasoned as 'low'
   ↳ HA automation routes 'low' alerts to Sonos chime + lights 1h
     [Open in HA]
   [Edit] [Disable] [View matches] [Delete]

ⓘ Every matched rule fires a kukiihome_alert event with reasoned severity;
  your HA automation routes by severity. Rules don't carry custom event
  names or built-in alert toggles — see web-ui-design.md §7.7 for why.
```

**The rule editor — new + edit form**. Two modes selected by a radio at
the top of the form:

**NL mode (natural-language intent — the main shape):**

```
┌ New rule ─────────────────────────────────────────────[ × ]┐
│  Mode      ◉ Natural-language intent (VLM-evaluated)        │
│            ◯ Identity shortcut (subject seen → alert)       │
│                                                             │
│  Name      [Winston unsupervised in front           ]       │
│            ↳ rule id: winston_unsupervised_in_front          │
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
│   The VLM evaluates this intent against the situation      │
│   AND reasons about severity (critical / normal / low)     │
│   based on the scene + time-of-day + context.              │
│                                                             │
│   [Try against the most recent eligible incident ↓]        │
│   (Shows match verdict + reasoned severity + reasoning)    │
│                                                             │
│  SEVERITY                                                   │
│   ◉ Let the VLM decide per-match (recommended)             │
│   ◯ Force a static severity for this rule:                 │
│        ◯ Low  ◯ Normal  ◯ Critical                         │
│   ⓘ If you force a static severity, the VLM still judges   │
│     match — only severity is fixed.                        │
│                                                             │
│                              [Cancel]  [Save & enable]      │
└─────────────────────────────────────────────────────────────┘
```

**Shortcut mode (identity-only, deterministic):**

```
┌ New rule ─────────────────────────────────────────────[ × ]┐
│  Mode      ◯ Natural-language intent (VLM-evaluated)        │
│            ◉ Identity shortcut (subject seen → alert)       │
│                                                             │
│  Name      [Bob arrives                              ]      │
│                                                             │
│  Trigger when                                              │
│    Subject  [Bob ▾]  (enrolled people + pets)              │
│             is seen on                                      │
│    Camera / Area  ☑ Any camera                              │
│    Time           ☑ Any time                                │
│                                                             │
│  Severity  ◯ Low  ◯ Normal  ◉ Critical                     │
│  ⓘ Shortcut rules don't call the VLM — identity match is   │
│    deterministic. Severity is fixed.                       │
│                                                             │
│                              [Cancel]  [Save & enable]      │
└─────────────────────────────────────────────────────────────┘
```

There is **no per-rule "fire HA event" name** anymore — every match emits
`kukiihome_alert` carrying `rule_id` and `rule_name`. HA automations
route by severity (most cases), or by `rule_id` if a user genuinely wants
rule-specific behavior. The form is shorter than the earlier draft and
the contract with HA is cleaner.

Protective actions (class 3 — locks, sirens, floods on critical) are
**not configured per-rule** either. They're configured per-camera in the
camera detail page's *Authorized actions* block (web-ui-design.md §7.7)
and triggered by the VLM's `recommendations` independent of which rule
matched. A user who wants per-rule protective actions writes an HA
automation keying on `data.rule_id` — that's HA's job.

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
| `services/ha-agent/src/.../rules_runtime.py` | **NEW** — in-memory rule cache, scope-filter helper, prompt-section builder, shortcut-rule deterministic matcher |
| `services/ha-agent/src/.../triage.py` | extend: filter rules by scope; pass NL rules into VLM call; match shortcut rules deterministically; read `matched_rules` from response (with severity); record matches; **emit `kukiihome_alert` event** per match |
| `services/ha-agent/src/.../reasoning.py` (or the VLM prompt builder) | add the *Named user intents* section + the per-rule severity-reasoning instruction to the prompt; update output schema for `matched_rules[*].severity` |
| `services/ha-agent/src/.../http_api.py` | add the `/api/intent/rules/*` endpoints (CRUD + test + matches) |
| `services/ha-agent/src/.../client.py` (HA client) | new `fire_event(name, data)` method calling HA's `/api/events/{name}`; new `call_service(domain, service, target, data)` if not present |
| `ha-integration/custom_components/kukiihome/binary_sensor.py` | extend: add `kukiihome_alert_active` (5-min sliding window) |
| `ha-integration/custom_components/kukiihome/sensor.py` | extend: add `kukiihome_last_alert_severity`, `kukiihome_alerts_today` |
| `ha-integration/addon/kukiihome/blueprints/severity-routed-alert.yaml` | **NEW** — the default severity-routed blueprint shipped with the integration |
| `ha-integration/custom_components/kukiihome/__init__.py` | extend: on first-run, install the default blueprint pointed at the user-picked notify target |
| `services/ha-agent/src/.../web_ui/intent.py` | **NEW** — `/intent` page renderer (Rules section live, Preferences placeholder) |
| `services/ha-agent/src/.../web_ui/intent_rule_form.py` | **NEW** — new + edit form rendering + parsing, with the NL/shortcut mode selector |
| `services/ha-agent/src/.../web_ui/mocks.py` | remove the Intent mock |
| `services/ha-agent/src/.../__main__.py` | wire routes for `/intent`, `/intent/rules/new`, `/intent/rules/{id}`, `/intent/rules/{id}/matches`, and the POST handlers |
| `services/ha-agent/src/.../web_ui/shell.py` | add form CSS (textarea, multi-select chips, severity radio, mode selector) |
| `planning/web-ui-design.md` | ratify Part VI Rules section based on the design decisions in this task |
| `tests/test_rules_store.py` | **NEW** — store CRUD + scope-filter + audit log |
| `tests/test_rules_runtime.py` | **NEW** — prompt-section building, response parsing (incl. reasoned severity), shortcut-rule matching |
| `tests/test_intent_page.py` | **NEW** — page renderers + form parsing (both modes) |
| `tests/test_triage_rules.py` | **NEW** — integration test: triage → rules → `kukiihome_alert` event emission with correct severity |

Notably **NOT** in the touches list anymore:

- `notifier.py` — there is no built-in alert path; HA owns delivery via the blueprint.
- Per-rule binary_sensor entities — the integration only adds the small status entity set (3 entities total), not N per rule.

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
  page using the form sketched above (NL mode + shortcut mode).
- The `Try against the most recent eligible incident` dry-run works and
  returns the VLM's match verdict + reasoned severity + reasoning inline.
- Triage assembles the active NL-rules section into the VLM prompt; the
  VLM returns `matched_rules` (with reasoned severity per match); shortcut
  rules are matched deterministically by triage; the dispatcher emits a
  `kukiihome_alert` event per match with the correct severity.
- The default severity-routed blueprint is installed by the first-run
  wizard pointed at the user's chosen notify target.
- A rule-matched alert lands on HA Companion via the user's notify
  service, branched by severity per the blueprint.
- The 3 status entities (`alert_active`, `last_alert_severity`,
  `alerts_today`) reflect state correctly.
- Every rule evaluation (match or non-match) is recorded in `rule_matches`
  with reasoned severity; the per-rule matches page shows recent matches +
  the severity distribution.
- The activity trace page (Part III §22) renders *"matched rule X
  (severity: critical, conf 0.87): …"* lines in the audit chain alongside
  the VLM call.
- Loop 1: a ✗ on a rule-matched alert surfaces the *"refine intent"*
  prompt and lets the user edit the rule's text inline.
- `planning/web-ui-design.md` §VI Rules section is updated to reflect the
  final design (especially: severity-as-VLM-output for NL rules, no
  per-rule HA event names, no per-rule entities).

---

## Task 10 — Perception + protective action plumbing (action classes 2 & 3)

**Intent.** Build the *dispatcher-side* mechanism for the agent's two
direct-action classes from web-ui-design.md §7.7: **class 2 (perception
actions — transient, revertable, for the agent's own perception during
reasoning)** and **class 3 (protective / responsive actions — persistent,
mitigation of the assessed situation)**. Both are direct HA service
calls / camera API calls, gated by per-camera whitelist + policy, with a
revert queue for class 2 and an audit log for class 3.

Task 9 (Rules) emits the *class 4* outcome notification; Task 10 wires
the rest of the action taxonomy. Together they cover the full agent →
HA action surface.

### What's needed

Three pieces:

**A. Dispatcher action runtime.** A new module in ha-agent that:

- Reads `perception_requests` and `recommendations` from the VLM's
  structured output.
- For each request/recommendation, looks up the **per-camera whitelist +
  policy** to decide whether to execute, gate, or reject.
- Executes via `HAClient.call_service()` for HA services, or via the
  preprocessor's `/tune` endpoint (for PTZ / IR-cut / stream-switch).
- For class 2: schedules a revert at `+revert_after_s` (an asyncio task
  per request, tracked by camera so we can coalesce overlapping requests
  on the same target).
- For class 3: records the action in a persistent `protective_actions`
  table (audit log) with status (`ok` / `gated` / `failed`).

**B. Per-camera whitelist + policy editor.** Extends the per-camera page
(Part II §11 *Tuning* section). Two sub-blocks:

- **Perception (class 2)** — list of allowed target entities + camera ops
  + a max-duration per row. Most users leave this at defaults; the agent
  asks for very narrow things (lights adjacent to the camera, the
  camera's own PTZ).
- **Protective (class 3)** — list of authorized actions with policy
  conditions inline: severity gate, confidence gate, time-of-day rule,
  max-duration if applicable. The form sketched in web-ui-design.md §7.7.

**C. Trace page rendering.** The activity trace page (Part III §22)
already shows the audit chain. Extends to render perception cycles + the
protective-actions executed, with revert status (class 2) or persistent
status (class 3). A protective-action row links out to the camera page's
authorized-actions section so the user can adjust authority if desired.

### VLM structured-output extension

`VLMResponse` gains two new top-level fields beyond the existing ones (and
beyond `matched_rules` added in Task 9):

```json
{
  "findings": { ... },
  "matched_rules": [ ... ],
  "perception_requests": [
    {
      "kind": "ha_service",
      "service": "light.turn_on",
      "target": "light.front_porch",
      "data": { "brightness": 255 },
      "revert_after_s": 45,
      "rationale": "low-light scene; visibility too poor to ID person"
    },
    {
      "kind": "camera_api",
      "camera_id": "front",
      "op": "ptz_zoom",
      "data": { "factor": 1.8 },
      "revert_after_s": 45
    }
  ],
  "recommendations": [
    {
      "action_class": "lock",
      "service": "lock.lock",
      "target": "lock.back_door",
      "urgency": "critical",
      "confidence": 0.96,
      "rationale": "unknown person climbed fence; intrusion likely"
    }
  ],
  "tentative": false   // when true, requested_perception was the reason
                       // (a re-look + re-call follows)
}
```

The trace page renders all of these inline in the audit chain.

### Whitelist data model

A new SQLite store in ha-agent, scoped per-camera:

```sql
CREATE TABLE perception_whitelist (
  camera_id      TEXT NOT NULL,
  target_kind    TEXT NOT NULL,        -- 'ha_service' | 'camera_api'
  target         TEXT NOT NULL,        -- service:target or op
  enabled        INTEGER NOT NULL DEFAULT 1,
  max_duration_s INTEGER,              -- NULL = no max
  PRIMARY KEY (camera_id, target_kind, target)
);

CREATE TABLE protective_whitelist (
  camera_id          TEXT NOT NULL,
  action_class       TEXT NOT NULL,    -- 'lock', 'siren', 'spotlight', 'announcement', etc.
  service            TEXT NOT NULL,    -- 'lock.lock', 'switch.turn_on', etc.
  target             TEXT NOT NULL,    -- entity_id
  enabled            INTEGER NOT NULL DEFAULT 1,
  min_severity       TEXT NOT NULL,    -- 'critical' | 'normal' | 'low' | 'any'
  min_confidence     REAL NOT NULL,    -- 0.0 - 1.0
  blackout_window    TEXT,             -- JSON: [{days, start, end}] when this action is suppressed
  max_duration_s     INTEGER,
  redundancy_required INTEGER NOT NULL DEFAULT 0, -- N consecutive recommendations required
  PRIMARY KEY (camera_id, action_class, service, target)
);

CREATE TABLE protective_actions_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id  TEXT NOT NULL,
  camera_id    TEXT,
  ts           REAL NOT NULL,
  action_class TEXT NOT NULL,
  service      TEXT NOT NULL,
  target       TEXT NOT NULL,
  data_json    TEXT,
  status       TEXT NOT NULL,           -- 'ok' | 'gated' | 'failed' | 'whitelisted_rejected'
  gate_reason  TEXT,                    -- if status != 'ok'
  vlm_confidence REAL,
  vlm_rationale  TEXT
);
```

### Touches

| File | Change |
|---|---|
| `services/ha-agent/src/.../action_runtime.py` | **NEW** — perception + protective action execution, revert queue |
| `services/ha-agent/src/.../action_store.py` | **NEW** — SQLite-backed whitelist + audit log |
| `services/ha-agent/src/.../triage.py` | wire `perception_requests` → action_runtime BEFORE re-invoking VLM; wire `recommendations` → action_runtime AFTER final assessment |
| `services/ha-agent/src/.../reasoning.py` | VLMResponse schema extension; prompt includes per-camera authorized actions summary |
| `services/ha-agent/src/.../client.py` | extend `call_service()` signature for richer target/data; add `fire_event()` (also used by Task 9) |
| `services/preprocessor/src/.../app.py` | extend `/tune` to accept PTZ / IR-cut / stream-switch ops with revert hints |
| `services/ha-agent/src/.../http_api.py` | new `/api/cameras/{id}/whitelist` GET/PUT endpoints |
| `services/ha-agent/src/.../web_ui/camera_detail.py` (Part II builder, future) | render the whitelist + policy editor section |
| `services/ha-agent/src/.../web_ui/home.py` & `trace.py` (future) | render perception cycles + protective actions in the trace audit chain |
| `planning/web-ui-design.md` | ratify §7.7 details + Part II §11 *Tuning* extension |
| `tests/test_action_runtime.py` | **NEW** — whitelist enforcement, policy gating, revert queue, idempotency |
| `tests/test_action_store.py` | **NEW** — whitelist CRUD + audit log |
| `tests/test_triage_actions.py` | **NEW** — integration: VLM emits perception+recommendations → dispatcher executes correctly |

### Open

- **Default whitelist on new cameras.** Empty (zero authorized actions) or
  permissive (allow lights adjacent to the camera; no protective actions
  by default)? [Recommend: **empty by default**. Protective authority is
  earned per-action through explicit user opt-in. First-run setup may
  offer a wizard to seed common authorizations per camera role.]
- **"Adjacent entity" inference for perception.** How does the system
  know `light.front_porch` is near `camera.front_porch`? [Options: HA
  Areas (preferred — leverage HA's own grouping), explicit per-camera
  config, name-matching heuristic (camera_X → light_X). **Recommend
  HA Areas first**, with explicit override.]
- **Redundancy / consensus across VLM calls.** Should some protective
  actions require N consecutive recommendations? (e.g., *"lock the door
  only if the next VLM call also says lock"*) [Recommend: yes — model it
  per-action as `redundancy_required`. Default 0 (single-call sufficient)
  for most; user can crank it to 2 for irreversible-ish actions.]
- **Idempotency assumptions.** Lock-already-locked is a no-op; light-on-
  already-on is a no-op. But media_player.play_media isn't idempotent.
  Action_runtime should query state before executing for safety. [Recommend:
  yes — state check for known-non-idempotent classes (media_player).]
- **Revert collision.** If two overlapping perception requests target the
  same light (one wants on, one off), what wins? [Recommend: latest wins
  for the apply step; revert is to the state captured at the *earliest*
  request's apply time. Simple and audit-able.]
- **Class 3 action lifecycle.** Persistent means "doesn't auto-revert" —
  but should there be a "revert when incident closes" option for the
  specific case of sustained mitigations (e.g., *"keep floods on while
  the unknown person is still in frame"*)? [Defer to a future iteration;
  starts simple with no auto-revert. The user does the undo on the
  camera page or via HA.]

### Done when

- VLM emits `perception_requests` and the dispatcher executes the
  whitelisted ones, schedules reverts, and re-invokes the VLM with the
  fresh frame data. Iteration cap honored.
- VLM emits `recommendations` and the dispatcher gates them through the
  per-camera protective whitelist + policy (severity, confidence,
  time-of-day, redundancy); whitelisted+gated ones execute via direct HA
  service calls; everything is logged in `protective_actions_log`.
- The per-camera page's *Authorized actions* block lets the user view +
  edit the perception and protective whitelists per camera.
- The trace page renders perception cycles (with revert status) and
  protective actions (with policy gate decision + status) inline in the
  audit chain.
- The Diagnostics view (Part VIII, future) shows the persistent action
  log: every class-3 action ever taken across the home, with deep-links
  to the trace.
- web-ui-design.md §7.7 reflects the final shape (especially the
  whitelist schema and the policy gate vocabulary).

---

## Implementation order recommendation

If a fresh context picks this up, I'd build in this order — lighter and
non-architectural first, three big arcs (Tasks 1, 9, 10) at the end:

1. **Task 2** (timestamp formatting) — pure helper change, low risk, immediately visible.
2. **Task 3** (headline cleanup) — pure rendering change, low risk, immediately visible.
3. **Task 4 + 6** (sticky nav + Identities under shell) — one refactor, two wins.
4. **Task 7** (build real /activity) — formal Part IV, builds on row schema from Home.
5. **Task 5** (pool cam diagnosis + aspect-ratio principles) — diagnostic + small CSS, may surface follow-ups.
6. **Task 9** (Rules editor) — large multi-session arc; should resolve its
   open questions (storage location, prompt cost, dry-run rate-limit) with
   the user before code lands.
7. **Task 10** (Perception + protective action plumbing) — pairs with Task 9
   architecturally (the dispatcher's *other* lanes beyond the event emission
   Task 9 owns); benefits from Task 9's prompt-extension work being live
   first. Per-camera page extension lands as a sub-task of Task 10 if Part II
   hasn't been built yet.
8. **Task 1** (event clips, architectural) — biggest single arc, scope-out
   per the Design A/B/stop-gap framing. Order may flip with Tasks 9/10
   depending on which the user wants to feel sooner.

Tasks 2–4, 6–7 land cleanly in a single session each. Tasks 1, 9, 10 are
multi-session arcs that should be planned + scoped before code starts.
Tasks 9 and 10 share the VLM-output schema extension — landing them
together (or 9 first, 10 immediately after) is more efficient than
interleaving with other work.

---

## File-by-file change summary (for quick reference)

| File | Tasks touching it |
|---|---|
| `services/ha-agent/src/.../web_ui/shell.py` | 2 (friendly_time), 3 (camera_display_name), 4 (sticky CSS), 7 (filter chip CSS), 9 (form CSS), 10 (whitelist editor CSS) |
| `services/ha-agent/src/.../web_ui/home.py` | 2 (use friendly_time), 3 (headline + where line) |
| `services/ha-agent/src/.../web_ui/activity.py` | 7 (new file) |
| `services/ha-agent/src/.../web_ui/intent.py` | 9 (new file — Rules section + Preferences placeholder) |
| `services/ha-agent/src/.../web_ui/intent_rule_form.py` | 9 (new file — new + edit form render + parse) |
| `services/ha-agent/src/.../web_ui/mocks.py` | 7 (remove activity mock), 9 (remove intent mock) |
| `services/ha-agent/src/.../review_page.py` | 4, 6 (body-only renderers) |
| `services/ha-agent/src/.../__main__.py` | 4, 6, 7, 9 (wrap legacy routes; real activity + intent handlers + rule POSTs) |
| `services/ha-agent/src/.../rules_store.py` | 9 (new file — SQLite-backed rules + matches) |
| `services/ha-agent/src/.../rules_runtime.py` | 9 (new file — in-memory cache, scope filter, prompt builder) |
| `services/ha-agent/src/.../triage.py` | 9 (rule filter + prompt extension + match recording + kukiihome_alert emission), 10 (perception_requests pre-VLM, recommendations post-VLM) |
| `services/ha-agent/src/.../reasoning.py` | 9 (VLM prompt + matched_rules schema), 10 (perception_requests + recommendations schema, authorized-actions summary in prompt) |
| `services/ha-agent/src/.../action_runtime.py` | 10 (new — perception + protective execution, revert queue) |
| `services/ha-agent/src/.../action_store.py` | 10 (new — whitelist + audit log) |
| `services/preprocessor/src/.../app.py` | 1 (clip serve endpoint), 10 (extend /tune for PTZ + IR-cut + stream-switch) |
| `ha-integration/addon/kukiihome/blueprints/severity-routed-alert.yaml` | 9 (new — default blueprint) |
| `ha-integration/custom_components/kukiihome/binary_sensor.py` | 9 (alert_active entity) |
| `ha-integration/custom_components/kukiihome/sensor.py` | 9 (last_alert_severity, alerts_today) |
| `ha-integration/custom_components/kukiihome/__init__.py` | 9 (first-run blueprint install) |
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
| `tests/test_action_runtime.py` | 10 (new file — whitelist + policy + revert queue) |
| `tests/test_action_store.py` | 10 (new file — whitelist CRUD + audit) |
| `tests/test_triage_actions.py` | 10 (new file — integration) |

Conventional Commit prefixes (per the auto-release workflow):

- Tasks 2, 3, 4, 6: `fix(web-ui): …` or `refactor(web-ui): …` — patch bumps.
- Task 7: `feat(web-ui): build real activity page` — minor bump.
- Task 5: `fix(activity): surface pool cam events in the home stream` — patch.
- Task 9: `feat(intent): rules editor + kukiihome_alert event contract` — minor.
- Task 10: `feat(dispatcher): perception + protective action runtime` — minor.
- Task 1: `feat(events): clip recording + browser playback` — minor.
