# Changelog

## [0.26.10](https://github.com/DarinShapiro/Kukii-Home/compare/v0.26.9...v0.26.10) (2026-06-05)


### Features

* **memory:** wire memory graph into the add-on — shadow dual-write + Neo4j sidecar (Epic 10.2 Phase 1+2) ([fafac5a](https://github.com/DarinShapiro/Kukii-Home/commit/fafac5a84e29603b742edd3a949ae15c60440da8))


### Bug Fixes

* **ci:** exempt non-python s6 services from venv-python gate + ruff RUF059 ([4b6c6fa](https://github.com/DarinShapiro/Kukii-Home/commit/4b6c6faafe42fd4d7d4bbc6fdb5e0658e9f7ebd3))

## [0.26.9](https://github.com/DarinShapiro/Kukii-Home/compare/v0.26.8...v0.26.9) (2026-06-05)

### Bug Fixes

- **memory:** defensive scope coercion + play button fallback + drawer pop-out ([419b47e](https://github.com/DarinShapiro/Kukii-Home/commit/419b47e511e7d170345bd3efd5fe0cbb918872d9))

## [0.26.8](https://github.com/DarinShapiro/Kukii-Home/compare/v0.26.7...v0.26.8) (2026-06-05)

### Bug Fixes

- **memory:** drawer persistence across nav + escape alert-table src URLs ([1b5b6fa](https://github.com/DarinShapiro/Kukii-Home/commit/1b5b6face1c7a67d8b1a663c20bec90b97a6a942))

## [0.26.7](https://github.com/DarinShapiro/Kukii-Home/compare/v0.26.6...v0.26.7) (2026-06-05)

### Bug Fixes

- **memory:** four self-audit findings — drawer close, history class, refine guard, diag perf ([f82002f](https://github.com/DarinShapiro/Kukii-Home/commit/f82002f46689557e1bc94415ac93935b2e50974b))

## [0.26.6](https://github.com/DarinShapiro/Kukii-Home/compare/v0.26.5...v0.26.6) (2026-06-05)

### Bug Fixes

- **memory:** drawer is page-specific — every page hosts it with current path as context ([eecbb5d](https://github.com/DarinShapiro/Kukii-Home/commit/eecbb5df75f6b27e7931b2ae5fd43d3b93620749))

## [0.26.5](https://github.com/DarinShapiro/Kukii-Home/compare/v0.26.4...v0.26.5) (2026-06-05)

### Bug Fixes

- **memory:** silence LLM-down banner until an actual failure is recorded ([f9b45bf](https://github.com/DarinShapiro/Kukii-Home/commit/f9b45bf62095c238dec85e8d476f22c6b4f67dc9))

## [0.26.4](https://github.com/DarinShapiro/Kukii-Home/compare/v0.26.3...v0.26.4) (2026-06-05)

### Features

- **memory:** persistent header drawer trigger — open from any page ([87a3fcc](https://github.com/DarinShapiro/Kukii-Home/commit/87a3fccda24a6558c89f8a4dd9758ee3b148b44e))

## [0.26.3](https://github.com/DarinShapiro/Kukii-Home/compare/v0.26.2...v0.26.3) (2026-06-05)

### Features

- **memory:** multi-turn dispatcher + tool calling + memory-layer tools (Part X §38, Part IX §26) ([e939505](https://github.com/DarinShapiro/Kukii-Home/commit/e939505d983eacadbdbe3723e79ffca36a6b58db))

## [0.26.2](https://github.com/DarinShapiro/Kukii-Home/compare/v0.26.1...v0.26.2) (2026-06-05)

### Bug Fixes

- **web-ui:** batch UI fixups from user review (depth-aware base href, nav on alert page, identities default, clip fallback, drop activity nav) ([704adee](https://github.com/DarinShapiro/Kukii-Home/commit/704adeef95fd2b2bb6fe3c23c9ee0817c5f53053)), closes [#5](https://github.com/DarinShapiro/Kukii-Home/issues/5) [#6](https://github.com/DarinShapiro/Kukii-Home/issues/6) [#7](https://github.com/DarinShapiro/Kukii-Home/issues/7) [#8](https://github.com/DarinShapiro/Kukii-Home/issues/8) [#3](https://github.com/DarinShapiro/Kukii-Home/issues/3) [#1](https://github.com/DarinShapiro/Kukii-Home/issues/1) [#2](https://github.com/DarinShapiro/Kukii-Home/issues/2) [#4](https://github.com/DarinShapiro/Kukii-Home/issues/4) [#9](https://github.com/DarinShapiro/Kukii-Home/issues/9)

## [0.26.1](https://github.com/DarinShapiro/Kukii-Home/compare/v0.26.0...v0.26.1) (2026-06-05)

### Bug Fixes

- **memory:** default model to gpt-oss-120b + tighten scope schema discipline ([115ac8f](https://github.com/DarinShapiro/Kukii-Home/commit/115ac8f4267a39537a8896ef69da324fffdbf4bb))

### Miscellaneous Chores

- **release:** slow version bumps — feat → patch, breaking → minor ([c066a83](https://github.com/DarinShapiro/Kukii-Home/commit/c066a83b1ef5af5e014d268c6d57d48f07ac1ad2))

## [0.26.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.25.0...v0.26.0) (2026-06-05)

### Features

- **addon:** expose LLM endpoint config as add-on options ([f9fc4b0](https://github.com/DarinShapiro/Kukii-Home/commit/f9fc4b042d33eb2727be277e03aace68769b2d53))

## [0.25.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.24.0...v0.25.0) (2026-06-04)

### Features

- **memory:** wire Cerebras LLM dispatcher + degraded-mode banner ([a9d8a33](https://github.com/DarinShapiro/Kukii-Home/commit/a9d8a33984a5246cd0401c8abe6741c7f8992184))

## [0.24.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.23.0...v0.24.0) (2026-06-04)

### Features

- **memory:** drift detection — suggest re-classification for stale guidance (Part X §39) ([1fb6e21](https://github.com/DarinShapiro/Kukii-Home/commit/1fb6e2163cafc11721f8c07e6116cc13ba6747c2))

## [0.23.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.22.0...v0.23.0) (2026-06-04)

### Features

- **memory:** push-reply fragment-load — drawer opens contextualized on /alert/{id}[#drawer](https://github.com/DarinShapiro/Kukii-Home/issues/drawer) (Part X §40) ([4d6bf80](https://github.com/DarinShapiro/Kukii-Home/commit/4d6bf8067dbbde6aab8b367d2d9f2ef2189a2e7d))

## [0.22.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.21.0...v0.22.0) (2026-06-04)

### Features

- **memory:** /system storage + privacy page + RetentionStore (Part IX §30) ([d360198](https://github.com/DarinShapiro/Kukii-Home/commit/d3601981a2d167b189ec117e9c67576bd1966020))

## [0.21.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.20.0...v0.21.0) (2026-06-04)

### Features

- **memory:** /identities expansion + per-identity detail (Part IX §29) ([6680bf9](https://github.com/DarinShapiro/Kukii-Home/commit/6680bf9b264f652b2dd4c95c3647a2ef4cd7980e)), closes [#292](https://github.com/DarinShapiro/Kukii-Home/issues/292)

## [0.20.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.19.0...v0.20.0) (2026-06-04)

### Features

- **memory:** LLM dispatcher provider + composite fallback (Part X §35) ([35d2af2](https://github.com/DarinShapiro/Kukii-Home/commit/35d2af2c554c6506eda7725d7bd24ebb550b54b5))

## [0.19.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.18.0...v0.19.0) (2026-06-04)

### Features

- **memory:** drawer skeleton + heuristic dispatcher + wired POST endpoints (Part X §34-35) ([4ca9365](https://github.com/DarinShapiro/Kukii-Home/commit/4ca9365c6a82960a4760be7597cf4025df500e8a))
- **memory:** unified /memory browse — collapse /intent + /policies (Part IX §28) ([445a317](https://github.com/DarinShapiro/Kukii-Home/commit/445a317a0199ae9d73e804a0274fa6c7e0803f00))

## [0.18.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.17.0...v0.18.0) (2026-06-04)

### Features

- **memory:** ProvenanceStore + commit_guidance dispatcher (Parts IX/X foundation) ([ff8564b](https://github.com/DarinShapiro/Kukii-Home/commit/ff8564b12d2bc90c970929adbd3471214245a03a)), closes [#2](https://github.com/DarinShapiro/Kukii-Home/issues/2)

## [0.17.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.16.0...v0.17.0) (2026-06-04)

### Features

- **events:** trace audit chain on alert detail (Part III §22 extension) ([1c53788](https://github.com/DarinShapiro/Kukii-Home/commit/1c53788af50aea1c1e7a4492b8fc839a62fcabb5))

## [0.16.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.15.0...v0.16.0) (2026-06-04)

### Features

- **diagnostics:** live Part VIII page — system + stores + reasoner roll-up ([17a9a83](https://github.com/DarinShapiro/Kukii-Home/commit/17a9a83001f9c6e3b30a75a1b5656ab2264136d3))

## [0.15.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.14.0...v0.15.0) (2026-06-04)

### Features

- **policies:** live Part VII page with PolicyStore + revoke ([bdbcad6](https://github.com/DarinShapiro/Kukii-Home/commit/bdbcad6d142800e460daf0ac659f342bd96c2693))

## [0.14.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.13.0...v0.14.0) (2026-06-04)

### Features

- **intent:** live Preferences section + PreferencesStore ([3970fa9](https://github.com/DarinShapiro/Kukii-Home/commit/3970fa9353baf2cfdf8703518e5fd40cd633e94d)), closes [#292](https://github.com/DarinShapiro/Kukii-Home/issues/292)

## [0.13.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.12.0...v0.13.0) (2026-06-04)

### Features

- **areas:** live Part V page with AreaStore + AttentionMode + camera assignment ([a725f45](https://github.com/DarinShapiro/Kukii-Home/commit/a725f45a9a6bd2116de9e1c4018b8e7d9a8b7662))

## [0.12.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.11.0...v0.12.0) (2026-06-04)

### Features

- **cameras:** live Part II page — list + detail + Task 10 whitelist editor ([e31d5d4](https://github.com/DarinShapiro/Kukii-Home/commit/e31d5d4bf680454d38681500474222948932fce4))

## [0.11.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.10.0...v0.11.0) (2026-06-04)

### Features

- **events:** event clip playback — preprocessor mux + ha-agent proxy ([1fc2ec3](https://github.com/DarinShapiro/Kukii-Home/commit/1fc2ec3d16e9aeec1cd36be1a9cfff724da79e5b))

## [0.10.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.9.0...v0.10.0) (2026-06-04)

### Features

- **dispatcher:** perception + protective action runtime (classes 2 & 3) ([01e46d4](https://github.com/DarinShapiro/Kukii-Home/commit/01e46d49619bdba2db46e815cb934f9369122e38))

## [0.9.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.8.0...v0.9.0) (2026-06-04)

### Features

- **intent:** rules editor MVP — store + runtime + UI + CRUD + shortcut firing ([b9751ef](https://github.com/DarinShapiro/Kukii-Home/commit/b9751ef5248031e5a6c558a3d0a4e0c45620ab92))

## [0.8.0](https://github.com/DarinShapiro/Kukii-Home/compare/v0.7.0...v0.8.0) (2026-06-04)

### Features

- **web-ui:** build real /activity page (Part IV depth + filters) ([55cd77f](https://github.com/DarinShapiro/Kukii-Home/commit/55cd77f197725dffc714698b325bcc0e43f50a6c))

### Bug Fixes

- **web-ui:** friendly_time graduates with clock-time + tooltip ([016014b](https://github.com/DarinShapiro/Kukii-Home/commit/016014b04fc7c7579b41da52f4d04514a585cbfa))
- **web-ui:** readable activity headlines, strip stream-quality suffixes ([198026a](https://github.com/DarinShapiro/Kukii-Home/commit/198026a08ba7c876183caa626ff3d11d5667d0e4))
- **web-ui:** standardize thumbnail aspect-ratio containers + §17 corollary ([93a4b98](https://github.com/DarinShapiro/Kukii-Home/commit/93a4b98f208a49c0c113595ea2d9dd475ed1af73))

## 0.7.0 — 2026-06-04

**Preview: a new product Web UI you can try alongside the existing one.**

The Web UI is being rebuilt around what Kukii-Home actually is — a home AI
agent, not a status dashboard. This release lands the skeleton of that new UI
**non-disruptively**: the page you've always used (system status, cameras,
notifications, logs) is exactly where it was. A small **✨ Try the new UI**
chip on it opens the new home page.

What's there to try:

- **Home (fleshed)** — top-line state in plain English; a "Needs attention"
  row for unnamed tracks waiting to be labeled; an activity stream of the
  most-recent incidents (passive vs action lanes, friendly relative times,
  no day boundaries); a small system stripe at the bottom.
- **Activity, Areas, Intent, Policies, Cameras, Diagnostics** — credible
  _Coming soon_ skeletons so the navigation works end-to-end. Each tab
  explains what'll go there. The Intent tab shows a sample of what
  natural-language rules will look like.
- **Identities** — the same Review page you've been using, now under the
  new shell.

The legacy UI is unchanged. When the new UI is ready to be the default, a
later release will flip the switch.

## 0.6.0 — 2026-06-03

**New: open a track to see it move — and one-tap confirm who it is.**

Click any thumbnail in Identity Review to open the track:

- **Animated playback** of the whole track — padded crops that follow the
  person across every frame — the context a single still can't give when it's a
  top-down head or there's no clear face.
- **"We think this is…"** — the people and pets you've already enrolled, ranked
  by similarity with a confidence margin, each a one-tap **Confirm**. Because
  your known set is small, even a soft match usually points clearly at the right
  person — so labeling becomes a tap instead of typing. (Or label someone new,
  right there.)
- Confirming now **strengthens** that person's template (averaged across every
  track you confirm) instead of replacing it.

Also preprocessor-side (no add-on change needed): **gait** now flows end-to-end
from the enrichment worker — one descriptor per walking track — joining body,
pet, and face as an always-embed identity signal.

## 0.5.1 — 2026-06-03

**Fix: Identity Review actions no longer show a false "action failed."**

Labeling a track, **✗ not them**, and **Merge** sometimes reported "preprocessor
unreachable or rejected it" even though the change _had_ been saved — a stale
pooled connection to the preprocessor that the add-on didn't recover for writes
(page loads, which retry automatically, were unaffected). The add-on now retires
idle connections promptly and retries a write once on a transient blip, so the
confirmation matches reality. (On 0.5.0 the workaround was to refresh — the edit
had actually landed.)

Also new, preprocessor-side (no add-on change needed): **face recognition is now
an enrollable identity signal** — the most durable one, recognizing a person
across days, outfits, and lighting whenever a face is visible. It shows up in
Review as a `face` badge once your preprocessor is enriching with face enabled.

## 0.5.0 — 2026-06-03

**New: Identity Review — name the people & pets your cameras see.**

A new **🔎 Review identities** page in the add-on Web UI (top of the status
page, and the Kukii-Home sidebar panel). The recognition preprocessor now
remembers every person and pet it sees — even before you've named anyone — so
Review lets you:

- See the un-named people/pets the cameras captured, each with a crop.
- **Label** one ("this is Alice", "this is Rex"). It's enrolled on the spot and
  every past _and_ future sighting is matched automatically — no re-processing
  of old footage.
- Fix mistakes: **✗ not them** clears a wrong match (it returns to the queue to
  re-label), and **Merge** combines two labels that are actually the same
  person/pet.

Newly-labelled people/pets are also folded into live recognition immediately,
so the next alert can name them.

**Requires the recognition preprocessor.** Set **preprocessor_url** in the
add-on options to your inference box, and point it at a detection database
(`KUKIIHOME_PREPROCESSOR_DETECTION_DB_PATH`). Without a preprocessor configured,
the Review page shows a short setup notice and the rest of the add-on is
unchanged.

Under the hood: always-embed identity pipelines (body / pet / gait), a
persist-then-resolve loop, and a new `/identity` API the Review page reads.

## 0.4.0 — 2026-05-31

**Rebrand: SentiHome → Kukii-Home.**

The whole project is renamed. The add-on slug, the integration domain,
the Python packages, NATS subjects, the npm scope, and the zeroconf
service are now `kukiihome`; the display name is **Kukii-Home**. New
icon and logo ship with this release.

**Breaking — you must reinstall, not update:**

The Home Assistant integration domain changed from `sentihome` to
`kukiihome`. Config entries do not migrate across a domain change, so:

1. **Remove** the old SentiHome integration (Settings → Devices &
   Services) and **uninstall** the old add-on.
2. Delete the stale integration dir if present:
   `/config/custom_components/sentihome/`.
3. Install this 0.4.0 add-on; on boot it installs the new
   `kukiihome` integration, then prompts you to restart HA.
4. Add the Kukii-Home integration fresh and reconfigure.

## 0.3.34 — 2026-05-30

**Notifications are now reasoning-gated (Epic 10.6 — the real pipeline).**

Until now every camera motion event that cleared the cooldown became a
push. That's backwards: a camera event alone shouldn't notify you —
something has to decide the event is _worth_ knowing about. This
release puts that decision in the path.

New flow for every motion event:

1. Record to the timeline (always — nothing is lost).
2. **Gather evidence** — the preprocessor's frame window when an
   inference box is configured, otherwise HA's own AI classification
   (person / vehicle / animal / motion).
3. **Reason** about it → a structured decision (the project's real
   `VLMResponse` contract: `criticality` + explanation + confidence).
4. **Notify only when warranted** — `alert`/`warning` push; `info`
   stays a silent timeline entry.

There's no VLM backend running yet, so a **stub reasoner** stands in.
It's deterministic and shaped exactly like the real VLM's output, so
the VLM drops in later with a one-line wiring change. Its decisions
today:

- **Unknown person → alert** (push)
- **Known/enrolled person → silent** (the "boring known person" case;
  activates once enrollment lands)
- **Vehicle → notify** (configurable)
- **Animal only → silent**
- **Unclassified motion (rippling water, blowing foliage) → silent**

That last one is the direct fix for the pool-cam flood: generic motion
with no person/vehicle is dismissed by default. Every dismissal is
still recorded — the **Recent alerts** list shows it as "dismissed"
with the reason on hover, and the per-alert page shows a **Reasoning**
section (marked as stub until a real VLM is wired).

Notes:

- Set `KUKIIHOME_TRIAGE_REASONING=off` to revert to the old
  every-event-notifies behavior.
- Reasoning fails **open**: if the reasoner errors, the event notifies
  anyway, so a bug can't silently swallow a real alert.
- "Send test alert" still always notifies — it bypasses reasoning by
  design (it's a wiring diagnostic).

## 0.3.33 — 2026-05-30

**Fix: disabling a camera didn't stop its alerts (the alert flood).**

Each camera ran a loop that subscribed to its motion sensor by
registering a handler on the shared HA connection — but stopping the
loop (on Disable or any config change) never **un**registered that
handler. So a disabled camera's motion kept firing alerts forever, and
toggling a camera on/off stacked multiple live handlers, multiplying
alerts per motion event. A camera watching rippling pool water could
push notifications non-stop.

Three-part fix:

- The HA client now supports **removing** a state-change handler, and
  the camera loop unregisters itself on stop. Disable now actually
  silences a camera.
- Re-subscribing is idempotent, so a restart can't double-deliver.
- Belt-and-suspenders: a stopped loop's handler refuses to fire even
  if a stale registration somehow lingers.

Two notes if you hit this:

- **Disable from the Kukii-Home panel**, not just the HA UI. Kukii-Home
  keys off the camera's _motion sensor_, not the camera entity —
  disabling only the camera in HA leaves the motion sensor firing.
  Use the **Disable** button on the device card (now effective), or
  uncheck its motion sensor.
- Rippling water / blowing foliage still trip the camera's own motion
  AI. The reasoning layer that learns to dismiss "boring" motion
  (known scene, no person) is the next epic and not wired into this
  path yet; for now, raise that camera's cooldown or point it at a
  person-only sensor.

## 0.3.32 — 2026-05-30

**Fix: "Send test alert" sent the notification twice per service.**

The per-camera **Send test alert** button recorded the alert (which
auto-fires the notifier) _and_ dispatched it again explicitly to
collect per-service results for the UI — so every selected notify
service got the notification twice. With three services selected that
was six notifications from one click.

Now the recorded test alert is flagged so the auto-notify path skips
it; the single explicit dispatch does the sending (and still reports
per-service results inline). One click → one notification per service.

Real motion alerts were never affected — they only ever notify once.

As cheap insurance against the whole class of bug, registering the
same alert callback twice is now a no-op, so an accidental double-wire
can't silently multiply notifications.

(If you saw alerts for a _different_ camera than the one you tested,
those were almost certainly real motion events that happened to fire
while you were testing — check the **Recent alerts** list: each row
links to its detail page showing the source and camera.)

## 0.3.31 — 2026-05-30

**Tapping a notification opens that alert (Epic 10.8.7 deep-link).**

Until now, tapping an alert notification on your phone dropped you on
the generic Kukii-Home status page — you then had to find the alert in
the Recent alerts list yourself. The per-alert detail page existed
(headline, snapshot, identity, why-it-fired, dismiss / feedback), but
nothing carried you straight to it.

The catch was authentication. The HA Companion app only opens
**frontend routes** (`/app/<slug>`) in-app with your session; every
backend path we tried (signed `/api/...`, ingress-token URLs) opened
in an external browser with no session and 401'd. So the tap had to
land on the bare panel route.

This release threads the alert id through as a URL **fragment**:
`/app/<slug>#alert=<id>`. HA's frontend router only sees `/app/<slug>`
— the proven, in-app, authenticated route — and ignores the fragment,
so it can't reintroduce a 401. The fragment rides along to the panel,
where a small in-panel reader picks it up and navigates the
(already-authenticated) Kukii-Home iframe straight to that alert's
detail page.

Also: each row in the Recent alerts list now links to its detail page,
so the manual path works too.

Tap an alert → land on that alert. No YAML, no hunting.

## 0.3.30 — 2026-05-28

**Alerts get enriched with recognition (Epic 10.9).**

When HA's AI motion sensor fires, the add-on records an alert with a
camera snapshot — but until now it had no idea _who or what_ set it
off. The preprocessor (inference box) has been quietly buffering RTSP
frames for the same cameras and running YOLO + face / body-ID / pet
recognition over them. This release closes the loop.

Two parts:

- **Rule that fired** (always on): the per-alert page now opens with a
  "Triggered by" card — the HA classification (Person / Vehicle /
  Animal) and the underlying `binary_sensor` that latched. The alert
  explains itself before any enrichment lands.

- **Recognition enrichment** (when a preprocessor is configured): each
  recorded alert fires an async pull of that camera's frame window
  around the event time. Detections, identified entities (Alice / Rex
  / "Bob's truck"), and a boxes-drawn annotated frame are folded into
  the stored event — so the page's Identities/Detections sections and
  the annotated hero image light up automatically.

  Set the new **`preprocessor_url`** option (e.g.
  `http://192.168.68.50:8090`) to your inference box. Leave it blank
  if you don't run a separate preprocessor — alerts still fire with
  the HA snapshot + rule.

Fully graceful: a sleeping/unreachable inference box, an empty window,
or a parse error simply leaves the alert with its HA snapshot. The
notification path is never blocked on the network round-trip
(fire-and-forget, exactly like the notifier).

Add-on only — no HA restart.

---

## 0.3.29 — 2026-05-28

**Ship the exact URL that was proven on the phone.**

A 60-second Developer Tools → notify test confirmed that a
notification with `clickAction: /app/<slug>` opens the Kukii-Home
panel **in-app and authenticated** on the phone — no 401, no
external browser. The notification tap problem (v0.3.15–27) is
solved.

v0.3.28 appended a `#alert=<id>` hash as a forward hook. The proven
test used the **bare** `/app/<slug>`. Since the hash has no reader
yet and "it's probably harmless" is what broke this six times,
v0.3.29 ships the bare URL — byte-identical to what was tested.

No functional difference from v0.3.28 for the tap; this just
removes the one untested element. Deep-link to the _specific_
alert (hash + an in-panel reader) is a separate, deliberately
tested follow-up.

Add-on only — no HA restart.

---

## 0.3.28 — 2026-05-28

**Notification tap finally works: point at the frontend panel route.**

After v0.3.15–27 chased this through ingress tokens, `/api/` paths,
and signed URLs — all of which 401'd — the root cause was simple
and documented: **the HA Companion app only navigates in-app
(authenticated) for FRONTEND routes.** Any `/api/…` path it hands
to an external browser, which has no session → 401.

You confirmed it directly: opening `/app/<slug>` loads the
Kukii-Home UI fine, while `/api/…` opens a browser and 401s.

So the notification tap now points at **`/app/<slug>`** — the
Kukii-Home panel's frontend route. The app opens it in-app with your
existing session. It cannot 401. The add-on discovers its own
`/app/<slug>` from the Supervisor API at boot, so nothing's
hardcoded.

What you'll get: tapping a notification opens the Kukii-Home panel
(the recent-alerts view). The alert id rides along as a
`#alert=<id>` hash — a hook for a follow-up that jumps straight to
the specific alert; today it opens to the list and you tap the one
you want.

**This is an add-on-only change** — the custom integration is
untouched, so this update does NOT trigger a Home Assistant
restart. Update the add-on and test.

What was removed: the `/api/kukiihome/alert` signing dance
(`_maybe_sign_alert_url`, the sign round-trip). The integration's
proxy views still exist but are no longer used by the tap; they'll
be cleaned up in a later release (which will need a restart, so
it's batched separately).

Known cosmetic issue (not blocking): the add-on config still emits
a Supervisor warning about `config` vs `addon_config` map options
(introduced in v0.3.24). Harmless; cleanup batched with the
integration-view removal.

---

## 0.3.27 — 2026-05-28

**Hotfix: `async_sign_path` import location in HA 2024+.**

v0.3.26 imported `async_sign_path` from
`homeassistant.helpers.network` — that location was removed in
newer HA. In HA 2026.5.4 (and likely back to ~2024.8), the helper
lives in `homeassistant.components.http.auth`. The whole Kukii-Home
integration failed to load with ImportError on startup, which is
why v0.3.26's `/api/kukiihome/sign` returned 404 (the view was
never registered).

Fix: try the modern import path first, fall back to the legacy one
for older HA installs. The function signature is unchanged across
versions.

Self-criticism: I should have validated import paths against the
actual HA version running on your Yellow before shipping. I'll
keep a "known-good HA version" reference for future integration
changes so cross-version surface issues get caught at PR time, not
on your phone.

---

## 0.3.26 — 2026-05-28

**Real fix for notification 401: HA signed-path URLs (Epic 10.8.5).**

User tested v0.3.25 and the notification tap still 401'd. HA's
log showed "Login attempt or request with invalid authentication
from <phone-IP>." Root cause: the HA Companion app's notification
tap loads URLs in an in-app webview using SESSION COOKIES, not
bearer tokens. My v0.3.23 assumption that `/api/*` would accept
bearer auth from the Companion was incomplete — bearer works for
explicit REST calls the app makes, but the URL-tap path uses
cookies, and the cookie isn't valid for arbitrary phone IPs.

Fix: HA's **signed-path** mechanism. Same pattern
`/api/camera_proxy/` uses for the notification image
attachments — that's why those have always worked while our
own paths didn't.

### How it works

1. Integration (v0.3.1) registers a new `SignURLView` at
   `/api/kukiihome/sign?path=...` that calls HA's
   `async_sign_path` helper to produce a URL with `?authSig=<jwt>`
   appended. 24-hour expiration.
2. Add-on, when building each notification, makes an HTTP call to
   that view and uses the signed URL in `data.url`,
   `data.clickAction`, and all action button `uri` fields.
3. HA's auth middleware sees the JWT in the URL and accepts it
   in place of a session cookie. No cookie needed → no IP-bound
   auth issue → tap works.

### What's signed

- The main tap URL
- iOS lock-screen action buttons (Dismiss / Open / False positive
  — FP keeps its `#fp` anchor for the form scroll-to)

### What's NOT changed

The image attachment (`data.image = /api/camera_proxy/...`) keeps
working as before — HA's camera_proxy uses signed paths internally
when emitting URLs to the Companion. Our handling now matches that
pattern.

### Failure mode (logged at warning)

If the integration's sign view is unreachable (HA still
restarting, integration not yet loaded), the notification fires
with the unsigned URL. Tap will 401 (same as v0.3.25), but the
notification body + image still work. Logs say
`notifier.url_unsigned ... Restart HA Core if persistent
notification asked you to.`

### Testing

24/24 notifier tests pass, including 3 new tests for the sign
flow + the FP action signing + the unsigned fallback.

---

## 0.3.25 — 2026-05-28

**Fix: stop auto-restarting HA Core (Epic 10.8.4 follow-up).**

v0.3.24's cont-init script asked Supervisor to restart Home
Assistant Core immediately after syncing the integration files.
That was wrong — installing a single add-on should not bring down
your whole smart home:

- Every other integration drops and reconnects
- Z-Wave / Zigbee / Matter networks re-handshake (30-60s recovery)
- Automations pause; in-flight scripts may abort
- Timers, occupancy state, notification ring all reset

Now: the script posts a **persistent notification** asking you to
restart at your convenience (Settings → System → Power). The
notification has a stable id so subsequent installs replace rather
than stack it. When you do restart, HA clears all persistent
notifications, so the message naturally goes away at the right
moment.

What you'll see after this update:

1. Bell icon shows: "Kukii-Home: restart needed"
2. You click Restart Home Assistant when you're ready (after
   dinner, in the morning, whenever)
3. Notifications work again post-restart

Caveat: until you restart, tap-to-open-alert and FP feedback
won't work — the alert page lives in the integration which needs
HA to scan the new code. Test notifications + alert recording
itself are unaffected.

---

## 0.3.24 — 2026-05-28

**Auto-install integration + zeroconf discovery (Epic 10.8.4).**

Eliminates the two-component install dance. Going forward, the
Kukii-Home add-on bundles the matching custom integration and
installs it automatically on first boot. Updates always ship the
integration version that matches the add-on — version skew (the
v0.3.15/17/20/23 failure mode) is now structurally impossible.

### What changes for you

**Install (fresh):**

1. Add the Kukii-Home add-on repository.
2. Install the Kukii-Home add-on.
3. Start it. The add-on copies the integration into
   `/config/custom_components/kukiihome/` and asks HA to restart.
4. After HA restarts: a "Discovered: Kukii-Home" card appears in
   Settings → Devices & Services. Click Configure → Done.

**Update:** click Update on the add-on; the integration updates with
it automatically. No HACS step, no second restart.

### What changed under the hood

- Add-on `config.yaml`: `map:` adds `config:rw` so the add-on can
  write to `/config/custom_components/`. You'll see a one-time
  permission prompt during the update.
- New `cont-init` script `20-install-integration.sh` syncs the
  bundled integration files on every boot. Uses a content-hash
  stamp so it no-ops when nothing changed (common path).
- After sync, the script asks Supervisor to restart HA Core via
  the REST API so the new integration code loads. Falls back to
  a "please restart manually" log line if Supervisor doesn't
  respond.
- New `discovery_publish.py` in ha-agent registers an
  `_kukiihome._tcp.local.` mDNS service on the LAN. The
  integration's `manifest.json` declares it consumes this service
  type, so HA's zeroconf component routes the broadcast to the
  integration's discovery flow.
- New `async_step_zeroconf` in the integration's `config_flow.py`
  handles the auto-discovery, pre-fills host/port from the TXT
  records, and shows a one-click confirm card.

### Dropping HACS support

Kukii-Home is no longer distributable via HACS — the two-component
shape doesn't fit HACS's "independent integration" assumption, and
trying to support both was the source of the install/update bugs
in v0.3.15/17/20/23. The bundled-with-add-on install is strictly
better for this product shape:

- One install path
- One update path
- Version skew impossible by construction
- Easier discoverability (zeroconf surfaces it without HACS browsing)

If you'd previously installed the integration via HACS, you can
remove it — the add-on will install its own copy in the right place.

### What this fixes

The 404-on-tap from v0.3.23: now genuinely impossible because the
integration that handles `/api/kukiihome/alert/<id>` is always
present at the matching version whenever the add-on is.

---

## 0.3.23 — 2026-05-28

**Real fix for the notification tap 401 (Epic 10.8.3).**

v0.3.15/17/20 all tried variations of `/api/hassio_ingress/<token>/...`
as the notification tap URL. All 401'd because that token is bound
to the browser ingress session — the Companion mobile app uses
bearer-token auth and doesn't carry the ingress cookie.

The actual fix: register the per-alert page as a
`HomeAssistantView` in the Kukii-Home custom integration. HA's
auth middleware accepts bearer tokens for `/api/*` paths, which
Companion DOES carry. The view proxies to the add-on's existing
`/alert/<id>` endpoint — Companion never talks to the add-on
directly, so the ingress-auth mismatch never arises.

### Notification tap target

- **Before:** `/api/hassio_ingress/<token>/alert/<id>` → 401
- **Now:** `/api/kukiihome/alert/<id>` → works

### Custom integration bumped to v0.2.0

The Kukii-Home integration now registers five views on HA Core:

```
GET  /api/kukiihome/alert/<event_id>              → HTML page
GET  /api/kukiihome/alert/<event_id>/frame.jpg
GET  /api/kukiihome/alert/<event_id>/annotated.jpg
POST /api/kukiihome/alert/<event_id>/dismiss
POST /api/kukiihome/alert/<event_id>/feedback
```

All five proxy to the add-on's matching endpoints internally. If
you don't have the Kukii-Home custom integration installed +
configured, the notification tap won't work — install it from
HACS or `custom_components/kukiihome/` in the repo.

### Two bugs fixed in the add-on alert page

While in there: fixed two latent bugs in v0.3.20's per-alert page
that hadn't been triggered yet because nobody had submitted the
form:

1. **Relative URLs doubled `alert/`** — `<img src='alert/<id>/frame.jpg'>`
   from page `/alert/<id>` resolved (per RFC 3986) to
   `/alert/alert/<id>/frame.jpg`. Now uses the unambiguous
   `<id>/frame.jpg` form.
2. **303 redirect Locations equally wrong** — `../alert/<id>?...`
   from POST `/alert/<id>/dismiss` resolved to `/alert/alert/<id>`.
   Now uses `../<id>?...`.

Tests strengthened to verify the FULL Location header (not just
substring presence) so this class of bug can't recur silently.

---

## 0.3.22 — 2026-05-28

**Add-on packaging split: ha-agent only on Yellow (Epic 10.8.2).**

Stops installing the preprocessor (with torch, onnxruntime,
openvino, insightface — ~3GB of inference stack) on the Yellow
add-on image. The preprocessor was never going to run inference
on Yellow's aarch64 CPU anyway; it runs on a separate inference
box (your laptop, NUC, etc.) and the add-on calls its REST
endpoints over the LAN.

### Topology

```
Yellow (aarch64)            ◀── LAN (NATS + REST) ──▶   Laptop (x86_64 + Intel iGPU)
  Kukii-Home add-on                                          Preprocessor (Docker)
    ha-agent                                                  RTSP capture
    Web UI                                                    YOLO11x via OpenVINO
    Notifications                                             ArcFace, OSNet
    Alert pipeline                                            Identity router
```

### What changed under the hood

- `MOG2MotionDetector` moved from `kukiihome_preprocessor.motion`
  to `kukiihome_shared.motion`. Existing import paths still work
  via a re-export shim, but new code should import from shared.
- `kukiihome-preprocessor` dropped from `kukiihome-ha-agent`'s
  pyproject deps. Yellow no longer pulls torch.
- Add-on Dockerfile now uses
  `uv sync --frozen --no-dev --package kukiihome-ha-agent`
  instead of `--all-packages`. Only ha-agent + its deps install.
- Reverted the v0.3.21 openvino platform marker — no longer
  needed since the preprocessor isn't in the add-on's dep graph
  at all. The marker was a workaround; this is the actual fix.

### Why you'd care

- Add-on image shrinks by ~3GB (no torch / onnxruntime /
  openvino / insightface).
- Add-on builds finish in ~1 min instead of ~5+ min on Yellow.
- Yellow's image-cap pressure drops significantly.
- Architecturally honest: Yellow IS the HA bridge, not the
  inference host.

No functional change for users running the preprocessor on a
separate box (which is everyone — the previous packaging was
just installing dead code on Yellow).

---

## 0.3.21 — 2026-05-28

**Hotfix: aarch64 build failure (HA Yellow / Raspberry Pi).**

v0.3.20 failed to build on aarch64 because the `openvino`
package (Epic 10.3.1) only ships x86_64 Linux / macOS / Windows
wheels — no aarch64 wheel exists. `uv sync` correctly refused
to install it on HA Yellow and the build aborted.

Fix: marker-gate the openvino dep to the platforms where it
actually has wheels. On aarch64 the dependency graph skips it
entirely. The OpenVINO inference backend was never usable on
aarch64 anyway (no Intel iGPU); the PyTorch backend works fine
via torch's aarch64 wheels.

No functional change for x86_64 hosts. This patch unblocks the
0.3.20 notification UX update on HA Yellow.

---

## 0.3.20 — 2026-05-28

**Notification tap UX (Epic 10.8.1).**

The notification you get on your phone now opens to a real
per-alert page instead of a blank HA screen. Three iOS Companion
action buttons appear on long-press for one-tap dismiss / open /
report-as-false-positive.

### What you'll see

Tap a notification → Kukii-Home opens to `/alert/<id>` showing:

- **Hero**: the snapshot from the alert
- **Identities**: who Kukii-Home thinks is in frame (face / body
  match + confidence)
- **Detections**: object summary (`person x 2`, `vehicle x 1`)
- **VLM analysis**: "Not yet analyzed" placeholder (Phase 11)
- **Sticky bottom row**: Dismiss / False positive

Long-press the notification on iOS lock screen → three quick
actions:

- **Dismiss** (red, no app open) — fires in the background
- **Open** — same as default tap
- **False positive** — opens the page scrolled to the FP form

### False-positive feedback

The FP button reveals an inline form with five categorized
reasons (`empty frame`, `wrong identity`, `known event`, `camera
glitch`, `other`) plus optional notes + actor-correction picker.
Each submission gets stored at
`/data/kukiihome/events/<id>/feedback.json` and feeds the
tuning loop (Phase 10.feedback).

### URL strategy

The notification URL is now
`/api/hassio_ingress/<token>/alert/<event_id>`. v0.3.15 tried
this and got 401; this time the ingress base is resolved from
the live boot context at notification time rather than being
hard-coded. If 401 reappears in production, the next move is
registering an HA panel that owns its own URL routing under
`/lovelace/kukiihome`.

### Per-alert persistence

Every alert now writes a durable event directory:

```
/data/kukiihome/events/<event_id>/
   meta.json       full alert record + reserved vlm_response field
   frame.jpg       snapshot copy
   feedback.json   when user submits FP
```

The schema includes a `triage_decision` discriminator
(`alert_fired` today) so the future near-miss + VLM-flagged-
silent records can use the same layout without a migration —
the data corpus for finding false negatives ("VLM noticed
something the rule-based triage missed") starts here.

---

## 0.3.19 — 2026-05-27

**Fix 404 on notification tap + per-alert latency capture.**

Two changes, both prompted by the user asking "how can we track
all forms of latency end-to-end?"

### Fix: 404 on notification tap

v0.3.17 used `/hassio/ingress/kukiihome` for `data.url`. Probed
live: HA 2026.5 returns 404 server-side for every `/hassio/*`
path without an auth cookie. The HA Companion app fetches the
URL first and renders 404 when the server doesn't respond.

Both URL strategies we've tried so far broke:

- v0.3.15: `/api/hassio_ingress/<addon_token>/` → 401
  (token is for browser ingress sessions, not mobile auth)
- v0.3.17: `/hassio/ingress/<slug>` → 404
  (route doesn't exist server-side in HA 2026.5+)

Until we work out the right deep-link form, **we omit the
tap-action URL entirely**. Tapping a notification just opens
the HA Companion app to wherever it was; from there the user
navigates to Kukii-Home manually (it's in the sidebar). The
image attachment + tag + body content all still work.

### Per-alert latency capture

Every alert now carries a `timings` sub-dict with:

- `ha_to_received_ms` — HA Core's view of the sensor flip →
  our WebSocket handler woke up. Should be a few ms on LAN.
  Spikes indicate HA Core overload or WebSocket lag.
- `handler_to_snapshot_start_ms` — our handler overhead.
  Should be sub-millisecond.
- `snapshot_duration_ms` — HTTP fetch through HA's
  `camera_proxy`. This is the camera + integration's
  round-trip time.
- `ha_to_snapshot_complete_ms` — total "time to have a
  frame in hand" from HA's view of motion. Best single
  number for "is this snapshot still representative of
  what triggered the alert?"

The Recent alerts table on the Web UI now has a Latency
column showing the total in seconds, color-coded:

- Green <1.5 s — snapshot likely still shows the event
- Orange <4 s — borderline
- Red ≥4 s — snapshot may be stale; consider camera-side
  buffering (future epic)

What we CAN'T measure from HA events alone: the camera ↔ HA
integration delay (real-world motion → HA's binary_sensor
flipping). HA's `last_changed` is the moment HA Core
observed the change, not the moment the camera detected
motion. The camera-side delay is typically <100 ms for
native push-based integrations (Reolink webhook, Dahua
alarm-listen) but can be seconds for polling-based ones
(ONVIF without subscription).

For the stale-snapshot problem the user raised: the right
long-term fix is continuous RTSP frame buffering so we can
pick the frame closest to the alert time. That's a future
epic. For now: measure + surface so you can SEE the
staleness on each alert.

## 0.3.18 — 2026-05-27

**Faster notification delivery — mark alerts as high-priority +
time-sensitive.**

User report: "notifications seem slow." Measured: Kukii-Home's
portion is ~270-400 ms (POST → call HA `notify` service → return).
Rest is HA + APNs/FCM push delivery — which on iOS specifically
defers NORMAL-priority pushes when the phone is in low-power
mode, Focus mode, screen-off, etc. Kukii-Home alerts are
security/presence events; they should not be deferred.

Added flags to every notify payload:

- `data.priority = "high"` — Android FCM, bypasses Doze / App
  Standby.
- `data.apns_headers = {apns-priority: "10", apns-push-type:
"alert"}` — iOS APNs, immediate delivery, surface-now.
- `data.push.interruption-level = "time-sensitive"` — iOS 15+,
  bypasses Focus modes so you get the buzz even when "Do Not
  Disturb" is on.

If you want truly silent alerts (e.g. an evening Focus mode that
suppresses everything), that's now a per-Focus-mode setting on
the phone itself — HA Companion + iOS handle it correctly when
the notification is tagged time-sensitive.

## 0.3.17 — 2026-05-27

**Fix: tap notification → 401 unauthorized.**

v0.3.15 set `data.url` and `data.image` to
`/api/hassio_ingress/<token>/...` using the token fetched from
Supervisor at boot. That token is the add-on's server-side
ingress token — bound to browser ingress sessions, not the
HA Companion mobile app's session. Result: tap notification →
HA → 401 unauthorized page.

Correct strategy (now shipped):

- `data.url` = `/hassio/ingress/kukiihome` — HA's user-session-
  aware frontend route. Tap → HA resolves it for the current
  user → opens the Kukii-Home status page in the Companion app.
- `data.image` = `/api/camera_proxy/<camera_entity>` — HA's own
  image endpoint. Served with the mobile app's session auth.

Trade-off on the image: it's the **current camera frame** at
the moment your phone fetches the notification, not the
at-alert-time snapshot. For security alerts this is arguably
more useful — "what's happening right now" — and it just works
without us trying to thread a Kukii-Home-served path through
HA's auth.

The supervisor ingress URL discovery from v0.3.15 stays in
place (we may need it for other surfaces later), but the
notifier no longer uses it.

## 0.3.16 — 2026-05-27

**Per-device motion-switch toggles + one-click fallback to generic
motion alarm.**

Two related diagnostics surfaces on the HA cameras card, both
addressing the same class of "alerts aren't firing" misconfig:

- **HA motion-detection switch is off** → orange banner with a
  Turn-on button. Heuristic match: any `switch.*motion*` /
  `switch.*detection*` entity sharing the camera's device tokens.
  Common Dahua + ONVIF misconfig: the parent switch is off, so
  none of the `binary_sensor.*motion*` ever transitions on.
  Click → POST `/discovery/switch_toggle` → HA `switch.turn_on` →
  reconcile → the next render shows the switch on (banner gone).
- **AI sensors picked but silent + a generic motion_alarm
  exists** → blue banner with a "Use generic motion alarm"
  button. One-click fallback for cameras whose AI plan isn't
  configured (Dahua Smart Plan trap and equivalents). Click →
  POST `/discovery/use_generic_motion` → motion override
  persisted → loop restarts subscribed to the alarm sensor.
  Noisier than AI (fires on leaves / shadows / pool ripples)
  but recovers the alert path without camera-side setup.

New backend:

- `HATools.find_motion_switches(camera_entity)` — token-overlap
  heuristic over HA's switch.\* entities.
- `DiscoveryDecision.motion_switches` (list of switch states)
  populated by `_reconcile_discovery` on every reconcile.
- `DiscoveryDecision.suggest_generic_motion` (entity id of a
  matching motion_alarm when one's available and AI sensors are
  currently picked).
- Two new POST endpoints (`/discovery/switch_toggle` +
  `/discovery/use_generic_motion`).

Heuristic detail: the AI/fallback suggestion only fires when
EVERY currently-picked motion entity is AI-classified — if the
user already overrode to motion_alarm or mixed in a generic
sensor, no banner. Keeps the UI quiet when the user already
made a deliberate choice.

## 0.3.15 — 2026-05-27

**Notifications now open Kukii-Home on tap + render the snapshot + read
sensibly at a glance.**

User report: "test notification works but tap opens HA random
startup page; no info gleanable from the notification itself." Root
cause: the notify payload was using `/` for tap-action (= HA root)
and a relative path for the image (= HA can't serve it). Fixed by
fetching the add-on's HA Ingress URL from Supervisor at boot and
using it for both URLs.

Changes:

- New `kukiihome_ha_agent.supervisor` thin client that calls
  `GET /addons/self/info` against the Supervisor REST API to
  read this add-on's `ingress_url` (e.g.
  `/api/hassio_ingress/<token>/`). Boot fetches it and stashes
  on BootState.
- AlertNotifier accepts the ingress base in its constructor and
  uses it for `data.url` (tap → Kukii-Home status page) and
  `data.image` (snapshot URL HA Companion can fetch via its
  auth session).
- When no ingress base is available (rare — only outside
  Supervisor), falls back to the stable `/hassio/ingress/kukiihome`
  path so tap at least lands Kukii-Home; image is omitted (HA only
  redirects the root, not deep paths).
- Notification message body rewritten — uses the camera's
  friendly name ("DahuaPoolCam Main") instead of the slug
  ("dahuapoolcam"), classification is capitalized and clear
  ("Person detected on DahuaPoolCam Main at 14:23:01"), area
  included when known.
- New `data.tag = "kukiihome_<camera_id>"` — sequential alerts
  from the same camera collapse on the phone instead of stacking.
- `data.clickAction` set as well (Android Companion expects that
  field name; iOS uses `url`).
- Synthetic test alerts get a `[TEST]` title prefix so the user
  knows what they're looking at on the phone.
- HACameraLoop now stamps `camera_name` on every alert it
  records so the notifier doesn't have to re-derive a friendly
  name from the slug.

## 0.3.14 — 2026-05-27

**In-UI diagnostic tools — verify the pipeline without waiting for
real motion.**

User report: "I didn't get any notifications and I don't even see an
alert in the UI." Diagnosis showed both cameras were correctly
subscribed but no motion sensor had fired since boot — there was
literally nothing for the system to act on. Hard to debug a quiet
system. v0.3.14 adds two click-to-test buttons:

- **Send test notification** on the Notifications card. POSTs
  `/notify/test`, which calls a new
  `AlertNotifier.test_send()` that awaits each dispatch and
  returns per-service results. The Notifications card renders the
  outcome inline ("✓ sent to notify.mobile_app_iphone" or "✗ {error
  reason}"), so notification setup is verifiable in one click.
- **Send test alert** on each enabled device in the HA cameras
  card. POSTs `/discovery/test_alert` which:
  1. Captures a real snapshot from the device's chosen stream.
  2. Records a synthetic `[TEST]` alert (so it's visible in the
     Recent alerts table + persisted on disk).
  3. Fires the notifier (awaits per-service results).
  4. Renders the outcome inline under the device card —
     snapshot bytes captured, alert id recorded, per-service
     send outcome.

This verifies the full pipeline: camera reachability → snapshot
fetch → alert persistence → notification dispatch — independently
of any HA motion sensor configuration. So even with a Dahua whose
Smart Plan isn't yet enabled (smart_motion sensors silent), you
can click Send test alert and see the alert flow end-to-end with
a real snapshot.

Test notifications include a real snapshot attachment if any prior
alert has one on disk (so you see the image rendered in the HA
Companion app), otherwise text-only.

Both test results clear next time you click Refresh in the page
header, so the cards don't accumulate stale state.

## 0.3.13 — 2026-05-27

**No more YAML for notifications. New Notifications card on the Web UI.**

v0.3.12 shipped `notify.alert_services` as a YAML list — but the
project mandate is "no handwriting." Fixed: Kukii-Home now discovers
every `notify.*` service HA exposes and renders one checkbox per
service in a new **Notifications** card. Tick the boxes you want →
**Save selection** → changes apply live (no restart).

- New backend `HATools.list_notify_services()` calls HA's
  `/api/services` and returns the `notify.*` services sorted.
- New persistent overrides at `/data/kukiihome/notify_overrides.json`
  (atomic writes; survives add-on updates).
- New POST `/notify/services` endpoint persists selection +
  `AlertNotifier.set_services(...)` updates the live notifier
  without a restart.
- New status-page card shows discovered services with checkboxes,
  current active list, and instant feedback ("● Active: notify.X").

Source-of-truth ordering at boot:

1. If `notify_overrides.json` exists → use it (empty list is a
   valid "all unchecked" choice).
2. Else if `topology.notify.alert_services` (YAML) is non-empty →
   use that as the initial seed. First UI save persists to the
   file and YAML stops mattering.
3. Else → no notifications.

So v0.3.12 users with YAML config see their selection pre-checked
on first load of v0.3.13's UI. Click Save → it's now in the file
and the YAML is moot.

The Capabilities card no longer doubles as a notify-config hint —
the dedicated Notifications card owns that role now.

## 0.3.12 — 2026-05-27

**Bug bundle + HA notifications.**

Fixes:

- **Save override → 404.** POST `/discovery/override` redirected to
  `./` which resolved to `/discovery/` (no GET route). Changed to
  `../` so the redirect lands on `/` under both HA Ingress and
  direct port access.
- **Auto-refresh clobbered forms.** The `<meta http-equiv="refresh"
content="10">` re-rendered the page every 10 s, wiping any
  override form mid-edit. Removed the meta refresh; added a
  manual **↻ Refresh** button in the page header. Re-discovery
  still runs every 5 min in the background.
- **Alerts non-persistent.** `AlertLog` was in-memory only;
  restarting the add-on wiped the history. Now persists to
  `/data/kukiihome/alerts.json` (atomic writes; survives add-on
  updates because `/data` is the persistent volume).

New feature — HA notifications on every alert:

- New `notify.alert_services: list[str]` config field. Each entry
  is a full HA notify service like `notify.mobile_app_pixel_8` or
  `notify.alexa_media_kitchen`. Empty list = no notifications
  (default; opt-in).
- Payload per service:
  - `title`: the alert headline (e.g. "Person at Pool Cam")
  - `message`: classification + camera + timestamp
  - `data.url`: link to the Kukii-Home status page
  - `data.image`: link to the alert's snapshot (HA Companion app
    renders inline). Included only when a snapshot file exists.
- Fires concurrently to all configured services. One service
  failing doesn't block the others.
- The Capabilities card on the Web UI now shows which services
  are wired so you can verify the configuration at a glance.

To enable notifications, in the add-on Configuration tab → YAML
mode, add:

```yaml
notify:
  alert_services:
    - notify.mobile_app_YOUR_DEVICE
```

Save → Restart. Trigger motion and your phone should buzz with the
snapshot.

Heads-up — if a camera's HA motion-detection switches are off
(`switch.<camera>_motion_detection`, `switch.<camera>_smart_motion_detection`),
none of the `binary_sensor.*_motion_*` sensors will fire. Turn them
on in HA's device page. A future version will surface this in the
Kukii-Home UI with a one-click toggle.

## 0.3.11 — 2026-05-27

**Zero-config camera onboarding — stop hand-writing adapter YAML.**

The old setup flow was: read DOCS, look at `/ha_cameras`, copy
entity-ids + motion-sensor lists into the Configuration tab in YAML
form, restart. Every new camera in HA = repeat. v0.3.11 replaces
that with discovery + AI defaults + a clickable per-device UI.

What's new:

- **New `auto_discover: true` option** (default ON). At boot the
  add-on lists every HA camera entity, groups them by physical
  device, and AI-picks per-device:
  - **Stream**: prefer low-bandwidth substreams (`_fluent`, `_sub`)
    over `_main` / `_mainstream` / `_clear`. Exclude known-broken
    Reolink `_profile*` ONVIF entries and Dahua duplicate substreams
    (`_sub_2`, `_sub_3`).
  - **Motion sensors**: prefer AI-classified
    (`_smart_motion_human`, `_person_detection`, `_intrusion_area_*`)
    over noisy generics (`_motion_alarm`,
    `_cell_motion_detection`, `_video_motion_info`).
  - **Cooldown**: 10 s default.
- **New "HA cameras" card UI** (replaces the read-only discovery
  table). One row per device:
  - Click **Enable / Disable** to start/stop the camera loop live.
  - Expand **Override** to override the stream, motion sensors, or
    cooldown via radio / checkbox / number inputs.
  - Click **Reset to AI defaults** to drop overrides.
  - **Re-discover now** picks up newly-added HA cameras.
  - The card also runs a periodic re-discovery every 5 minutes.
- **Persistent overrides** at
  `/data/kukiihome/adapter_overrides.json`. Atomic writes; survives
  add-on updates because `/data` is the persistent volume.
- **Live reconciler**: changes take effect immediately, no restart
  needed. Click Enable → loop starts in milliseconds. Change stream
  → loop restarts with the new config.

Back-compat: the legacy hand-written `adapters: [...]` path still
works exactly as before. If `auto_discover: false` (or `adapters`
is non-empty), the add-on uses the manual config. Existing users
on 0.3.x with a populated `adapters` array see no behavior change.

The user's mandate behind this release: "get away from any
handwriting. Discovered devices should be filtered and selected if
there are options per device. Otherwise AI should make the
configuration decisions." Done. The conversational AI wizard
they also asked about is deferred to a later epic.

## 0.3.10 — 2026-05-27

**Click a thumbnail → full-size lightbox in-page.**

Recent-alert and per-camera thumbnails are tiny on purpose (the table
would be unreadable at full res), but the user couldn't get a closer
look without right-click → Open in new tab. Added an in-page lightbox:

- Click any thumbnail → dark overlay with the full snapshot
- Click overlay / press Esc → dismiss
- Pure vanilla JS + CSS — no library deps, works identically under
  HA Ingress and direct port 8765
- Anchor `href` preserved as a fallback so middle-click / right-click
  → "open in new tab" still works
- Also stamps a `.thumb` class on thumbnails with `cursor: zoom-in`
  and a subtle hover transform so the click affordance is obvious

End-to-end verification on the Reolink Front South Camera (via the
Reolink HA integration, after 0.3.9's diagnosis): snapshot was a real
97 KB JPEG (`FF D8 FF DB ...`), thumbnail rendered, lightbox opens
the full image inline.

## 0.3.9 — 2026-05-27

**Remove the broken WS `camera/get_image` path + surface camera-side
errors directly in the Cameras card.**

The v0.3.7 attempt to use HA's WebSocket `camera/get_image` command
was based on a guessed API name. Live `/debug/test_snapshot` against
HA 2026.5.3 returned:

{ success: false, error: "ws camera/get_image failed: Unknown command." }

Confirmed: HA has NO WebSocket command for camera image fetch. The
canonical path is REST `/api/camera_proxy/<entity_id>`, which internally
calls the camera integration's `async_camera_image()` method.

The user's actual problem isn't a missing path — it's that the camera
entity's `async_camera_image()` is returning Reolink's login HTML
instead of a frame, because the entity comes from HA's ONVIF integration
with auth that doesn't reach the snapshot URL. This is a config issue on
the HA side that Kukii-Home can't fix from inside the add-on.

Changes:

- HAClient.fetch_camera_snapshot: removed the dead WS path entirely.
  Pure REST + content-type validation, with a docstring documenting
  what HA-side fix is needed when the validation rejects a response.
- HACameraLoop.\_capture_and_alert: when the fetch fails, the error
  message now lands on CameraStreamStatus.last_error and is rendered
  inline on the Cameras card. So the user sees the actual diagnosis
  ("camera_proxy returned content-type='text/html'…") without
  needing to check logs.

User-facing fix paths for this specific situation:

1. In HA: add the camera via the official Reolink integration
   instead of ONVIF — that creates a camera entity whose
   async_camera_image() uses Reolink's REST API directly and works.
2. Switch this camera in Kukii-Home topology from `kind: ha-camera`
   to `kind: rtsp-direct` with the RTSP URL + creds — bypasses
   HA's image-fetch entirely.

## 0.3.8 — 2026-05-27

**Add on-demand debug endpoints + stamp add-on version into the image.**

The v0.3.7 diagnostic was inconclusive because: (a) no new motion event
had fired since the update (the old corrupt `.jpg` was still being
served from disk), and (b) the Web UI page version string shows the
Python package version (`v0.1.0`), not the add-on manifest version, so
I couldn't tell from outside which add-on version was actually running.

Two fixes for both:

GET /debug/test_snapshot?camera_entity=camera.X
Forces a fresh fetch_camera_snapshot() call right now, without
waiting for a real motion event. Returns:
{ success: bool, bytes: N, first_16_hex: "FF D8 FF ...",
looks_like: "jpeg" | "html" | "png" | "unknown",
error: <message if failed> }
This is how the dev cycle should have worked from the start.

GET /debug/version
Returns { package_version, addon_version }. The add-on version is
baked into the image at build time via:
Dockerfile: ARG ADDON_VERSION; RUN echo $ADDON_VERSION > /app/.kukiihome_addon_version
build.yaml: args: { ADDON_VERSION: '0.3.8' }
So next time we can curl /debug/version to confirm exactly which
add-on build is running.

## 0.3.7 — 2026-05-27

**Real fix for the "snapshot is corrupt" bug + clear error path when
HA's camera_proxy returns HTML.**

Live diagnosis from PowerShell on the dev box showed the
`/alerts/<id>/snapshot` file was 21,889 bytes of **HTML** (Reolink's
camera login page) with Content-Type incorrectly stamped as
`image/jpeg`. Root cause: HA's `/api/camera_proxy/<entity_id>` was
proxying to an ONVIF-configured still-image URL that requires camera-
side auth we don't have — the camera responded with its login HTML,
HA forwarded it, and our naive code wrote those bytes to `.jpg`.

Why the ONVIF integration: the camera entity name
`camera.front_south_camera_profile000_mainstream` has the
`_profile000_mainstream` signature of HA's ONVIF integration. The
Reolink integration (which provides this user's AI detection
binary_sensors) does NOT typically expose a still-image URL — only a
stream. ONVIF tried to fill that gap and failed.

Fix:

HAClient.fetch_camera_snapshot now tries TWO paths:

1. WebSocket `camera/get_image` command (NEW)
   - HA routes via the integration's async_camera_image() getter,
     which for Reolink uses the proper Reolink REST API
   - Returns base64 inside the WS result
   - Requires plumbing command-response routing through the existing
     WebSocket: new `_ws_pending` dict tracks msg_id → Future,
     \_ws_consume routes `result` messages with matching ids to the
     waiting future
   - On WS disconnect, in-flight futures get HAClientError so callers
     don't hang

2. REST `/api/camera_proxy/<entity_id>` (existing, now validated)
   - Used as fallback if the WS path returns None / fails
   - **Content-Type now validated**: if response isn't `image/*`,
     raise HAClientError with first 200 chars of the body preview
   - This way we NEVER write HTML masquerading as `.jpg` again

Net effect:

- For Reolink cameras: WS get_image returns proper JPEG bytes (HA's
  Reolink integration implements get_image correctly even when the
  ONVIF still-URL is broken)
- For cameras where both paths fail: a clear error in /logs telling
  you exactly what came back instead of an image
- Alerts still record without evidence_ref on failure — no data loss

If after v0.3.7 the snapshot STILL doesn't appear:

- `/logs?level=warning` will show either `snapshot_fetch_failed` (WS
  or REST raised) or `ws_get_image_failed` (only WS raised, REST
  fallback also failed)
- The error message will include the actual content-type / preview
  so we know whether it's an HA-side config issue or an Kukii-Home
  code path issue

## 0.3.6 — 2026-05-27

User reported the v0.3.5 snapshot thumbnail still didn't render. Live
diagnosis via /logs + /recent_alerts curls proved the backend works
perfectly: alert recorded, evidence_ref set, /alerts/<id>/snapshot
returns 21,889 bytes of valid JPEG, HTML emits correct relative URLs.
The miss has to be browser-side.

Defensive fixes:

- **`<base href="./">`** in the page head. Forces relative URLs to
  resolve against the document's directory regardless of whether HA
  Ingress preserves the trailing slash on the page URL. Eliminates the
  edge case where `/api/hassio_ingress/<token>` (no slash) would make
  relative paths resolve to the wrong base.
- **Request logging** on `/alerts/<id>/snapshot` — every fetch logs
  `snapshot.request alert_id=... user_agent=...` so the ring buffer
  shows whether the browser even attempts the fetch. If the log shows
  no GET when the page rendered, the issue is HTML/URL/cache. If it
  shows a GET that returned 200, the issue is purely browser display.

To rule out browser cache once and for all: hard-refresh
(Ctrl+Shift+R) the Web UI after updating to v0.3.6.

## 0.3.5 — 2026-05-27

**Fix the v0.3.4 thumbnail-not-rendering bug + ship debug endpoints
that close the dev loop.**

User reported the Snapshot column in the Recent alerts table was empty
even though the alert had `evidence_ref` set and the snapshot file
existed on disk. Diagnosed live by curl-ing the add-on's port 8765
from the dev machine: backend returned 21,889 bytes of valid JPEG for
both `/cameras/<id>/snapshot` and `/alerts/<id>/snapshot`. Backend
fine; the bug was in the HTML.

Root cause: the Web UI is served through HA Ingress at
`/api/hassio_ingress/<token>/`. The thumbnail HTML used absolute paths
(`<img src='/alerts/<id>/snapshot'>`). The browser resolved that to
the HA-Core root (`https://homeassistant-yellow:8123/alerts/...`),
which HA doesn't know about → 404 → `onerror` hides the broken image.

Fixes:

- Thumbnail/link URLs in the alerts + cameras cards switched to
  **relative paths** (no leading slash). Resolves correctly under both
  HA ingress AND direct port 8765 access.
- Footer API links also made relative.

**New debug endpoints** (idea from user — "host MCP-style debug
services inside the app, callable via curl from the dev machine"):

GET /logs?limit=100&level=warning
In-memory log ring buffer (500 entries max), JSON.
Structlog now flows every event through the ring buffer.
GET /alerts/<alert_id>
Full alert JSON payload (NOT the image bytes — use
/alerts/<alert_id>/snapshot for the JPEG).
GET /debug/topology
Currently-loaded Topology pydantic model as JSON. Verifies
config was parsed how the code thinks it was.

**New "Recent logs" card** on the Web UI showing the last 30 log
events live (Time / Level / Event / Fields), so debugging doesn't
require the add-on Log tab anymore. Warning/error rows highlighted.

Net effect: the dev loop closes. Next time something fails, the user
either screenshots the Web UI (Recent logs card visible) or I curl
`http://<HA-IP>:8765/logs` directly from PowerShell on their dev box.

## 0.3.4 — 2026-05-27

**Per-alert thumbnails + timestamps in the Recent alerts table.**

The Recent alerts table previously had just four columns (ID, Headline,
Tier, Status) — no visual record of what triggered the alert, no time
information. Hard to correlate with what you actually saw or remember.

Changes:

- **`recorded_at` ISO timestamp** auto-stamped on every alert in
  `AlertLog.record()`. Old alerts logged before v0.3.4 render `—` for
  the time column; new alerts show `HH:MM:SS`.
- **`AlertLog.get(alert_id)`** lookup method.
- **New endpoint `GET /alerts/<alert_id>/snapshot`** — serves the
  snapshot file for a specific alert (rather than the latest snapshot
  for its camera). Used by the Recent alerts table.
- **Recent alerts table** restructured to 5 columns:
  Snapshot · Time · Headline · Tier · Status
  Each thumbnail is clickable — opens the full-size snapshot in a new
  browser tab. Renders nothing for alerts whose `evidence_ref` is empty
  (e.g. a snapshot fetch that failed).

Includes the v0.3.3 snapshot fix (camera_proxy bytes → local
filesystem), so the thumbnails actually render this time.

## 0.3.3 — 2026-05-27

**Fix snapshot capture across the add-on / HA Core container boundary.**

After v0.3.2 successfully fired its first motion alert, the snapshot
URL (`/cameras/<id>/snapshot`) returned 404 — the snapshot file didn't
exist where Kukii-Home expected it.

Root cause: the previous implementation called HA's `camera.snapshot`
service with `filename=/data/kukiihome/snapshots/<file>.jpg`. That asks
**HA Core** to write the file at `/data/kukiihome/snapshots/...`, but:

- HA Core's `/data` is HA's config directory
- Kukii-Home's `/data` is the add-on's persistent storage
- These are **completely different mountpoints**

So the file either ended up somewhere in HA's filesystem (not visible
to Kukii-Home) or was silently rejected by HA's `allowlist_external_dirs`
gate. Kukii-Home's serving endpoint then couldn't find the file in its
own container's `/data`, returning 404.

Fix: switch to **HA's `/api/camera_proxy/<entity_id>` REST endpoint**.

- Kukii-Home's `HAClient.fetch_camera_snapshot(entity_id)` GETs the
  current frame as JPEG bytes via HTTP, using the same bearer-token
  auth we already have configured
- HACameraLoop writes those bytes to Kukii-Home's own filesystem at
  `/data/kukiihome/snapshots/<file>.jpg` — under Kukii-Home's actual
  control, no cross-container path confusion
- No HA `allowlist_external_dirs` requirement
- No file-write race condition (HA was writing while Kukii-Home read)

Logging is clearer too: `ha_camera_loop.snapshot_fetch_failed` if the
proxy GET fails, `ha_camera_loop.snapshot_write_failed` if the local
file write fails. Alerts still record without `evidence_ref` when
either step fails, rather than dropping the alert entirely.

## 0.3.2 — 2026-05-27

**Make `adapters` editable in the Configuration tab.**

v0.3.1 declared `adapters` as a loose `match(.+)?` regex and never
included it in the default `options:` block — so Supervisor's
Configuration tab had no form field for it at all, leaving users with
no way to actually wire up a camera through the UI.

Fixes:

- `adapters: []` is now a default option, so Supervisor renders it.
- The schema is now a structured array of objects: each adapter has
  `name`, `kind` (dropdown), and the type-specific fields
  (`camera_entity`, `motion_entities`, `snapshot_cooldown_seconds` for
  ha-camera; `url`/`username`/`password`/`mqtt_host` for others).
  Supervisor renders this as a repeatable form with an "Add Adapter"
  button + per-field inputs.
- The other nested sections (`bus`, `memory`, `vlm_router`, `notify`)
  remain as `match(.+)?` — most users won't touch them, and editing
  them via Supervisor's YAML mode (three-dot menu → "Edit in YAML")
  works for advanced setups.

After this lands, the configuration flow is: Configuration tab → click
**Add Adapter** under the adapters section → fill in the form → Save.
No YAML paste required.

## 0.3.1 — 2026-05-27

- **Better motion-sensor matching.** The v0.3.0 heuristic required the
  binary_sensor entity_id to start with the camera entity_id, but Dahua /
  ONVIF / Reolink integrations typically create camera entities with
  stream-name suffixes (`camera.dahua_pool_cam_main`, `_sub`,
  `_profile000_mainstream`) while motion sensors sit at the device level
  without those suffixes (`binary_sensor.dahua_pool_motion_alarm`). Result:
  every Dahua user got "none detected" for motion candidates.

  New matcher tokenizes both slugs, drops stream-name + entity-kind stop
  words (`main`, `sub`, `mainstream`, `profile000`, `camera`, `sensor`,
  …), and pairs when ≥1 meaningful token overlaps. Adds `intrusion` to
  motion keywords (Dahua's term for trip-wire / line-cross events).

- **"Unmatched motion sensors" section** on the discovery card lists
  every motion-like `binary_sensor.*` the heuristic couldn't pair with
  any camera — so even if auto-matching misses, you can see what's
  available and wire it manually.

- **Unavailable camera state highlighted in red** so misconfigured /
  offline cameras are visible at a glance.

- New API: `GET /ha_cameras` now returns `{cameras: [...], unmatched_motion_sensors: [...]}`
  (additive — `cameras` shape unchanged).

## 0.3.0 — 2026-05-27

**Ride on HA's camera integration instead of duplicating it.** New
`ha-camera` adapter kind that subscribes to a camera's motion / AI
binary sensors and snapshots on event — no RTSP credentials in topology,
no MOG2 false positives, no per-frame CPU.

### Web UI

- **"HA cameras detected"** card on the status page lists every
  `camera.*` entity HA exposes, with heuristically-matched motion
  sensors (`binary_sensor.<cam>_motion`, `binary_sensor.<cam>_person`,
  etc.) — so you can see what's available before configuring anything.
  Card includes a ready-to-paste YAML snippet.
- **Cameras** card (previously "no cameras configured") now renders
  inline snapshot thumbnails per camera, cache-busted on each new
  motion event. Subscribed adapters show `state=subscribed` instead
  of just `running`.

### New adapter kind: `ha-camera`

Paste into the add-on Configuration tab:

```yaml
adapters:
  - name: pool-cam
    kind: ha-camera
    camera_entity: camera.pool_cam
    motion_entities:
      - binary_sensor.pool_cam_motion
      - binary_sensor.pool_cam_person # Reolink/Dahua AI classification
      - binary_sensor.pool_cam_vehicle
    snapshot_cooldown_seconds: 30
```

Pipeline per camera:

1. ha-agent subscribes to state-changes on the listed motion entities
2. On `off → on` transition: call `camera.snapshot` HA service →
   write to `/data/kukiihome/snapshots/<camera>_<ts>.jpg`
3. Record alert in `AlertLog` with headline derived from the sensor's
   AI classification ("Person at pool cam", not just "Motion at pool cam")
4. Web UI shows the snapshot as an inline thumbnail in the Cameras card

### New endpoints

- `GET /ha_cameras` — list HA's camera + motion entities (JSON)
- `GET /cameras/<id>/snapshot` — latest captured snapshot bytes (jpg)

### Topology schema

`AdapterConfig` now accepts:

- `kind: "ha-camera"` (added to the Literal type)
- `camera_entity: str | None`
- `motion_entities: list[str]`
- `snapshot_cooldown_seconds: float = 30.0`

### Migration

The `rtsp-direct` adapter still works unchanged. Use `ha-camera` whenever
the camera is already in HA — significantly less config, no creds in YAML,
benefits from any AI classification the camera or its HA integration
provides.

## 0.2.0 — 2026-05-26

**First end-to-end runtime: cameras feed in, motion events surface in the
Web UI.** Minor version bump to mark the milestone.

- `services/ha-agent/camera_loop.py`: one `CameraLoop` task per
  `rtsp-direct` adapter in topology. Opens the RTSP stream via OpenCV in
  a thread executor (avoids blocking the aiohttp event loop), samples
  every 5th frame, runs it through the preprocessor's existing
  `MOG2MotionDetector`, debounces (30s cooldown per camera), and posts
  motion events to `AlertLog`.
- `CameraStreamStatus` per stream tracks state (starting / opening /
  running / error / stopped), frame count, motion count, last-frame
  time, last-motion time, error message — all rendered in a new
  "Cameras" card on the status page.
- Self-healing: on any read error or stream close, the loop sleeps 15s
  then re-opens. Status page reflects the state in near-real-time.
- ha-agent now depends on `kukiihome-preprocessor` (for the MOG2 module)
  and transitively on `opencv-python-headless` (cv2). Both already
  install cleanly via the debian base.

### How to configure a camera

Add to the add-on **Configuration** tab:

```yaml
adapters:
  - name: front-cam
    kind: rtsp-direct
    streams:
      - id: cam_front
        rtsp_url: rtsp://user:pass@192.168.1.50:554/stream
```

Restart the add-on; the Cameras card on the status page will show the
stream go `opening` → `running`. Wave at the camera and within 30s a
new entry appears in the "Recent alerts" table.

### What's NOT in v0.2.0 yet

- No NATS bus — the loop runs in-process inside ha-agent
- No VLM analysis on the frames — just motion → alert
- No rule engine — every motion event becomes an alert
- No identity recognition — "Motion at cam_front", not "Sarah at front door"
- No alerts surfaced to HA as entities — they live only in the Web UI

These wire in via Epic 10+ (identity), the NATS bus runtime, and the
custom integration's coordinator polling `/recent_alerts`.

## 0.1.12 — 2026-05-26

- Fix `401 Unauthorized` against `http://supervisor/core` after pasting a
  long-lived access token in the Configuration tab.
- Root cause: the Supervisor proxy only accepts the `SUPERVISOR_TOKEN`
  env var that Supervisor injects automatically. Long-lived access tokens
  from HA's user UI work against HA Core _directly_ (port 8123). Mixing
  the two paths gives 401.
- Fix in `topology._supervisor_options_to_topology`: when `ha_url` is the
  Supervisor proxy AND `SUPERVISOR_TOKEN` is present in env, always use
  the supervisor token — ignore whatever the user put in `ha_token`
  (which couldn't work there anyway). When `ha_url` points at HA Core
  directly, the user's long-lived token is used as before.
- The status page now updates on the next 10s refresh once SUPERVISOR_TOKEN
  takes over (no further user action needed).

For most users: clear the `ha_token` field in the Configuration tab,
restart the add-on, and SUPERVISOR_TOKEN auto-wires. To use a long-lived
token instead, ALSO set `ha_url` to e.g. `http://homeassistant.local:8123`.

## 0.1.11 — 2026-05-26

**Fix `s6-overlay-suexec: fatal: can only run as pid 1` restart loop.**

v0.1.10 installed packages cleanly (debian base, all wheels resolved)
but the container restart-looped with the suexec/PID-1 error. Two
fixes:

- Remove `CMD ["/init"]` from the Dockerfile. The HA base-debian image
  already sets `ENTRYPOINT ["/init"]`, so my CMD was being passed as an
  argument — Docker effectively ran `/init /init`. That broke PID 1
  semantics and triggered s6-overlay-suexec to fatal. Letting the base
  ENTRYPOINT run with no args is correct.
- Remove `tini` from the apt install set. It wasn't being used; only
  risk was Docker auto-injecting it as init (`--init` flag) ahead of s6.

- Reduce the s6 service set to just `ha-agent`. The other five
  (core / memory / notify / vlm-router / preprocessor) had only
  idle-and-sleep run scripts and contributed nothing v1-useful to the
  add-on runtime. They re-enter when the NATS bus runtime wires up
  (Epic 10+); for now they're noise. The in-process Python (RuleEvaluator,
  MemoryStore, dispatchers, etc.) is already usable by anything that
  imports the modules directly.

After this: the add-on should show one running service (ha-agent), the
Web UI button should open the status page, and the Kukii-Home sidebar
panel should be functional.

## 0.1.10 — 2026-05-26

**Switch base image from alpine to debian to fix the manylinux/musllinux
wheel-availability gap.**

v0.1.9 unblocked onnxruntime by skipping the detector service, but
opencv-python-headless (used by the preprocessor) had the same problem
on the next build attempt — it only ships manylinux wheels, not
musllinux. Continuing to whack-a-mole `--package` exclusions for every
glibc-only dep is the wrong path; the fix is at the base image level.

Changes:

- `build.yaml`: base image switched from
  ghcr.io/home-assistant/<arch>-base-python:3.12-alpine3.20
  to
  ghcr.io/home-assistant/<arch>-base-debian:bookworm
  (~80 MB heavier per arch but unlocks every manylinux wheel).
- `Dockerfile`: swap `apk add` → `apt-get install`; install python3 +
  pip + venv via apt; install uv as before.
- Revert the `--package` exclusion list from v0.1.9 — `uv sync
--all-packages` works cleanly now, including kukiihome-detector +
  onnxruntime + opencv.
- Future heavy ML deps (torch, etc.) will install without further base
  image work.

## 0.1.9 — 2026-05-26

- v0.1.8 build failed on aarch64 because `kukiihome-detector` depends on
  `onnxruntime`, which only ships glibc (manylinux) wheels. The HA
  base-python image is alpine (musllinux), so onnxruntime install was
  impossible without a source build (which fails too on the minimal
  alpine base).
- Detector is facade+stubs in v1 and not run by the s6 service set.
  v0.1.9 explicitly installs only the workspace members the add-on
  actually runs, skipping `kukiihome-detector` (and therefore avoiding
  onnxruntime entirely). When detector graduates to real ML inference,
  we'll switch to a debian base image — tracked as a future task.

## 0.1.8 — 2026-05-26

- `uv sync` needs `--all-packages` to actually install workspace members.
  Without it (silent default behavior), only the root project's
  dependencies are installed — and Kukii-Home's root project has no deps;
  it's a pure workspace shell. So /app/.venv had nothing in it, the
  build-time import check (added in 0.1.7) caught the regression and
  failed install loudly. v0.1.8 fixes the missing flag.
- The `InvalidDefaultArgInFrom` warning in build output is harmless;
  Supervisor passes BUILD_FROM via build.yaml + --build-arg before the
  default would be used. The warning can be ignored.

## 0.1.7 — 2026-05-26

**The actual root cause for v0.1.3-v0.1.6 "connection refused".**

The Dockerfile installs all Kukii-Home packages with `uv sync`, which
creates `/app/.venv/` — but the s6 run scripts invoked `python` (which
resolves to `/usr/local/bin/python`, the base image's system python, NOT
the venv). System python has no Kukii-Home packages on its path, so all
six services crash-looped on import with `No module named kukiihome_*`
forever. The Web UI port never bound because the ha-agent process never
got past the `from aiohttp import web` line.

Fixes:

- All six `rootfs/etc/services.d/*/run` scripts now exec
  `/app/.venv/bin/python` explicitly instead of `python`.
- Dockerfile adds a build-time import check:
  `RUN /app/.venv/bin/python -c "import kukiihome_ha_agent; ..."`. If any
  workspace member isn't installed, the BUILD fails — so this kind of
  packaging bug surfaces during install, not after start.

Apologies for the v0.1.3-v0.1.6 churn. None of those would have ever
worked.

## 0.1.6 — 2026-05-26

- **Switch the Web UI to HA Ingress.** This is the canonical pattern for
  add-on Web UIs and removes the entire port-publishing path: no `host:port`
  resolution, no firewall surface, no `host_network` interactions. The
  "OPEN WEB UI" button now opens an HA-proxied URL like
  `/api/hassio_ingress/<token>/` that goes through HA's own auth.
- The Kukii-Home panel also shows up in the HA sidebar (look for "Kukii-Home"
  with a CCTV icon below the standard panels).
- The direct port mapping at `8765/tcp` is kept (commented to keep, in
  fact) so the custom integration's coordinator can still poll
  `http://homeassistant.local:8765/snapshot` for the JSON API. Only the
  UI path moves to ingress.
- Belt-and-suspenders cache-busters: hardcoded ADD URL (some BuildKit
  versions don't interpolate ARGs in URLs reliably) plus a `CACHEBUST`
  ARG that the addon-build workflow passes the commit SHA into.

## 0.1.5 — 2026-05-26

- Fix "connection refused" on the Web UI button. Two changes:
  - **Drop `host_network: true`**: standard container networking with a
    NAT'd port (`8765/tcp: 8765`) is more reliable on HA OS. host_network
    caused the aiohttp bind to land on an interface the host port mapping
    didn't reach on some setups.
  - **HTTP server starts before topology / HA connection**: the ha-agent
    `__main__` now binds 0.0.0.0:8765 first and only then attempts to
    load the topology and connect to HA. Any failure in those later
    stages becomes a visible card on the status page (with the full
    traceback for topology errors) instead of a silent process crash.
  - The status page also shows the current bootstrap stage
    (`starting` → `topology_loaded` → `ha_connected` or `ha_failed`)
    so it's obvious where things broke.

## 0.1.4 — 2026-05-26

- Fix stale image: `RUN git clone` was being cached by Docker, so rebuilds
  served old source code (specifically, the v0.1.2 `NotImplementedError`
  stubs in service `__main__` files). The new `config.yaml` from v0.1.3
  was being read by Supervisor (showing the Web UI button) but the
  in-container code was the unfixed crash-looping version, so port 8765
  had nothing listening when you clicked the button.
- Adds an `ADD https://api.github.com/repos/.../commits/main` instruction
  before the git clone. Docker's `ADD` on a URL re-fetches if the
  response changes — so whenever `main` advances, the cache key flips
  and the clone re-runs.

If you previously installed v0.1.3 and got "couldn't load page" from the
Web UI: **Uninstall the add-on, then reinstall** (don't just Update).
Update may still hit Supervisor's image cache; Uninstall + Reinstall
forces a clean rebuild with the new cache-busting Dockerfile.

## 0.1.3 — 2026-05-26

- The add-on now has a Web UI (Supervisor surfaces an "OPEN WEB UI"
  button). The ha-agent service hosts an aiohttp server on port 8765
  serving a minimal HTML status page showing HA connection state,
  visible entity count, recent alerts, and capability domains. Also
  exposes the JSON API the custom integration polls (`/healthz`,
  `/snapshot`, `/capabilities`, `/recent_alerts`, `/service`,
  `/acknowledge_alert`).
- Replaced the `NotImplementedError`-on-startup stubs in all six service
  entry points with topology-loading idle loops. Previously each service
  crashed at startup and s6 restart-looped them indefinitely; now they
  cleanly idle until the bus runtime wires in (Epic 10+).
- Added `aiohttp` to the ha-agent dependency list.
- Bumped declared port from 8001 → 8765 (the actual port the API binds
  to) and added the `webui:` directive.

## 0.1.2 — 2026-05-26

- Fix install failure on ARM64 hosts (HA Yellow, Raspberry Pi, etc.):
  the Dockerfile hard-coded `BUILD_FROM` to the amd64 base image, so
  Supervisor on an aarch64 host ended up pulling an amd64 image and
  failing at the first apk call with "exec format error".
- Ship a `build.yaml` mapping each declared arch (amd64, aarch64) to its
  correct `home-assistant/<arch>-base-python` image. Supervisor reads
  this and passes the matching one as a `--build-arg`.
- Remove the BUILD_FROM default in the Dockerfile so a missing arch
  fails loud at build time instead of silently picking the wrong arch.

## 0.1.1 — 2026-05-26

- Fix install failure on Supervisor: removed the `image:` reference to an
  unpublished GHCR image so Supervisor builds locally from the Dockerfile.
- Restructure the Dockerfile to `git clone` the Kukii-Home workspace inside
  the image instead of `COPY`-ing from the repo root. Supervisor's build
  context is the add-on directory, not the repo, so the previous COPY
  pattern wouldn't have worked even if the GHCR pull had succeeded.

## 0.1.0 — 2026-05-26

Initial add-on packaging (Epic 08.6 / #281).

- Repository discoverable as an HA add-on source via `repository.json` at repo root
- Supervisor `config.yaml` with options schema mirroring `kukiihome_shared.topology`
- Multi-arch Dockerfile (`amd64`, `aarch64`) on `homeassistant/<arch>-base-python`
- s6-overlay supervises six long-running services: core, memory, ha-agent,
  notify, vlm-router, preprocessor
- `cont-init.d/10-bootstrap.sh` bridges `/data/options.json` to the
  Kukii-Home topology loader via `KUKIIHOME_CONFIG`
- CI builds the `amd64` image on every PR

This release ships the packaging skeleton — the add-on installs and
services boot, but functional HA integration requires Epic 09
(ha-agent + custom_components/kukiihome).
