# Changelog

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
