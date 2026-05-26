# Changelog

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
