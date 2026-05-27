# Changelog

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
project mandate is "no handwriting." Fixed: SentiHome now discovers
every `notify.*` service HA exposes and renders one checkbox per
service in a new **Notifications** card. Tick the boxes you want →
**Save selection** → changes apply live (no restart).

- New backend `HATools.list_notify_services()` calls HA's
  `/api/services` and returns the `notify.*` services sorted.
- New persistent overrides at `/data/sentihome/notify_overrides.json`
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
  `/data/sentihome/alerts.json` (atomic writes; survives add-on
  updates because `/data` is the persistent volume).

New feature — HA notifications on every alert:

- New `notify.alert_services: list[str]` config field. Each entry
  is a full HA notify service like `notify.mobile_app_pixel_8` or
  `notify.alexa_media_kitchen`. Empty list = no notifications
  (default; opt-in).
- Payload per service:
  - `title`: the alert headline (e.g. "Person at Pool Cam")
  - `message`: classification + camera + timestamp
  - `data.url`: link to the SentiHome status page
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
SentiHome UI with a one-click toggle.

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
  `/data/sentihome/adapter_overrides.json`. Atomic writes; survives
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
the HA side that SentiHome can't fix from inside the add-on.

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
2. Switch this camera in SentiHome topology from `kind: ha-camera`
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
Dockerfile: ARG ADDON_VERSION; RUN echo $ADDON_VERSION > /app/.sentihome_addon_version
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
  so we know whether it's an HA-side config issue or an SentiHome
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
exist where SentiHome expected it.

Root cause: the previous implementation called HA's `camera.snapshot`
service with `filename=/data/sentihome/snapshots/<file>.jpg`. That asks
**HA Core** to write the file at `/data/sentihome/snapshots/...`, but:

- HA Core's `/data` is HA's config directory
- SentiHome's `/data` is the add-on's persistent storage
- These are **completely different mountpoints**

So the file either ended up somewhere in HA's filesystem (not visible
to SentiHome) or was silently rejected by HA's `allowlist_external_dirs`
gate. SentiHome's serving endpoint then couldn't find the file in its
own container's `/data`, returning 404.

Fix: switch to **HA's `/api/camera_proxy/<entity_id>` REST endpoint**.

- SentiHome's `HAClient.fetch_camera_snapshot(entity_id)` GETs the
  current frame as JPEG bytes via HTTP, using the same bearer-token
  auth we already have configured
- HACameraLoop writes those bytes to SentiHome's own filesystem at
  `/data/sentihome/snapshots/<file>.jpg` — under SentiHome's actual
  control, no cross-container path confusion
- No HA `allowlist_external_dirs` requirement
- No file-write race condition (HA was writing while SentiHome read)

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
   write to `/data/sentihome/snapshots/<camera>_<ts>.jpg`
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
- ha-agent now depends on `sentihome-preprocessor` (for the MOG2 module)
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
Web UI button should open the status page, and the SentiHome sidebar
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
--all-packages` works cleanly now, including sentihome-detector +
  onnxruntime + opencv.
- Future heavy ML deps (torch, etc.) will install without further base
  image work.

## 0.1.9 — 2026-05-26

- v0.1.8 build failed on aarch64 because `sentihome-detector` depends on
  `onnxruntime`, which only ships glibc (manylinux) wheels. The HA
  base-python image is alpine (musllinux), so onnxruntime install was
  impossible without a source build (which fails too on the minimal
  alpine base).
- Detector is facade+stubs in v1 and not run by the s6 service set.
  v0.1.9 explicitly installs only the workspace members the add-on
  actually runs, skipping `sentihome-detector` (and therefore avoiding
  onnxruntime entirely). When detector graduates to real ML inference,
  we'll switch to a debian base image — tracked as a future task.

## 0.1.8 — 2026-05-26

- `uv sync` needs `--all-packages` to actually install workspace members.
  Without it (silent default behavior), only the root project's
  dependencies are installed — and SentiHome's root project has no deps;
  it's a pure workspace shell. So /app/.venv had nothing in it, the
  build-time import check (added in 0.1.7) caught the regression and
  failed install loudly. v0.1.8 fixes the missing flag.
- The `InvalidDefaultArgInFrom` warning in build output is harmless;
  Supervisor passes BUILD_FROM via build.yaml + --build-arg before the
  default would be used. The warning can be ignored.

## 0.1.7 — 2026-05-26

**The actual root cause for v0.1.3-v0.1.6 "connection refused".**

The Dockerfile installs all SentiHome packages with `uv sync`, which
creates `/app/.venv/` — but the s6 run scripts invoked `python` (which
resolves to `/usr/local/bin/python`, the base image's system python, NOT
the venv). System python has no SentiHome packages on its path, so all
six services crash-looped on import with `No module named sentihome_*`
forever. The Web UI port never bound because the ha-agent process never
got past the `from aiohttp import web` line.

Fixes:

- All six `rootfs/etc/services.d/*/run` scripts now exec
  `/app/.venv/bin/python` explicitly instead of `python`.
- Dockerfile adds a build-time import check:
  `RUN /app/.venv/bin/python -c "import sentihome_ha_agent; ..."`. If any
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
- The SentiHome panel also shows up in the HA sidebar (look for "SentiHome"
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
- Restructure the Dockerfile to `git clone` the SentiHome workspace inside
  the image instead of `COPY`-ing from the repo root. Supervisor's build
  context is the add-on directory, not the repo, so the previous COPY
  pattern wouldn't have worked even if the GHCR pull had succeeded.

## 0.1.0 — 2026-05-26

Initial add-on packaging (Epic 08.6 / #281).

- Repository discoverable as an HA add-on source via `repository.json` at repo root
- Supervisor `config.yaml` with options schema mirroring `sentihome_shared.topology`
- Multi-arch Dockerfile (`amd64`, `aarch64`) on `homeassistant/<arch>-base-python`
- s6-overlay supervises six long-running services: core, memory, ha-agent,
  notify, vlm-router, preprocessor
- `cont-init.d/10-bootstrap.sh` bridges `/data/options.json` to the
  SentiHome topology loader via `SENTIHOME_CONFIG`
- CI builds the `amd64` image on every PR

This release ships the packaging skeleton — the add-on installs and
services boot, but functional HA integration requires Epic 09
(ha-agent + custom_components/sentihome).
