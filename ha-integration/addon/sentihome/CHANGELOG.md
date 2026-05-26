# Changelog

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
