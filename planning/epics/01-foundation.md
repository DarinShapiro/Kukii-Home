# Epic 01: Project Foundation & Infrastructure

**Architecture refs:** §02
**Components:** infrastructure, shared, scripts
**Priority:** P0
**Blocks:** all other epics

## Description

The bedrock the rest of the project sits on: monorepo conventions, dev environment, CI pipeline, shared libraries, schema codegen, and the docker-compose stack that lets every other epic run end-to-end locally. Nothing else can ship until these foundations work.

## Definition of done

- New contributor can clone the repo, run `./scripts/setup/install.sh && ./scripts/dev/up.sh`, and have a working dev stack with all services running
- CI runs lint + tests on every PR
- Schemas regenerate on PR and fail CI if generated artifacts are stale
- Repo has consistent code style enforced by automation

## Issues

1. **chore(repo): set up `uv` workspace and Python project layout** — root `pyproject.toml`, per-service `pyproject.toml` files, `uv` workspace config. (labels: `epic:foundation`, `component:infrastructure`, `priority:p0`)
2. **chore(repo): set up TypeScript monorepo with pnpm workspaces** — root `package.json`, per-frontend-project configs, shared TS config. (labels: `epic:foundation`, `component:frontend`, `priority:p0`)
3. **chore(infra): docker-compose dev stack** — NATS, postgres, qdrant, all services, hot-reload mounted, single command up/down. (labels: `epic:foundation`, `component:infrastructure`, `priority:p0`)
4. **chore(infra): docker-compose prod stack** — production overrides, persistent volumes, healthchecks. (labels: `epic:foundation`, `component:infrastructure`, `priority:p1`)
5. **chore(scripts): `scripts/setup/install.sh`** — checks prerequisites (docker, uv, pnpm), pulls images, bootstraps. (labels: `epic:foundation`, `component:scripts`, `priority:p0`)
6. **chore(scripts): `scripts/dev/up.sh`, `down.sh`, `restart.sh`, `logs.sh`** — daily-driver scripts. (labels: `epic:foundation`, `component:scripts`, `priority:p0`)
7. **chore(scripts): `scripts/dev/format.sh` and `lint.sh`** — run ruff, mypy, prettier, eslint across the monorepo. (labels: `epic:foundation`, `component:scripts`, `priority:p0`)
8. **chore(ci): GitHub Actions for lint + unit tests on PR** — Python lint/test matrix, TS lint/test matrix, schema validation. (labels: `epic:foundation`, `component:infrastructure`, `priority:p0`)
9. **chore(ci): GitHub Actions for integration tests on PR** — bring up docker-compose test stack, run `tests/integration/`. (labels: `epic:foundation`, `component:infrastructure`, `priority:p1`)
10. **chore(ci): GitHub Actions for nightly e2e tests** — `tests/e2e/` with recorded fixtures. (labels: `epic:foundation`, `component:infrastructure`, `priority:p2`)
11. **feat(shared): `shared/lib-python` skeleton** — event bus client wrapper, MCP helpers, structured logging, tracing primitives. (labels: `epic:foundation`, `component:shared`, `priority:p0`)
12. **feat(shared): `shared/lib-typescript` skeleton** — API client primitives, event types, formatting helpers. (labels: `epic:foundation`, `component:shared`, `priority:p1`)
13. **feat(shared): schema codegen pipeline** — JSON Schema → Python types (datamodel-code-generator), JSON Schema → TypeScript types (json-schema-to-typescript); script `scripts/dev/regenerate-schemas.sh`. (labels: `epic:foundation`, `component:shared`, `priority:p0`)
14. **docs: developer onboarding guide** — getting from `git clone` to first PR in <30 min. (labels: `epic:foundation`, `component:docs`, `priority:p1`)
