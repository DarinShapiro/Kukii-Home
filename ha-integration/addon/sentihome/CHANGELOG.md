# Changelog

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
