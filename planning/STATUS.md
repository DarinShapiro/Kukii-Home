# SentiHome Implementation Status

> **Resumption document** for agents continuing implementation work. This is a snapshot of where the code is, what's done, what's next, and the conventions established so far.

**Last updated:** 2026-05-25 (Epic 7 closed)
**Branch:** `main`
**CI status:** ✅ green
**Tests:** 229 unit passing (Python) + 4 (TypeScript) + integration test suite scaffolded

---

## Quick orientation

```bash
git clone https://github.com/DarinShapiro/SentiHome.git
cd SentiHome
./scripts/setup/install.sh          # first-time setup (prereqs, uv sync, pnpm install)
./scripts/dev/test.sh                # run unit tests (Python + TypeScript)
./scripts/dev/lint.sh                # ruff + prettier + eslint + tsc
```

**Read first:**

1. [`README.md`](../README.md) — project overview
2. [`docs/architecture/README.md`](../docs/architecture/README.md) — architecture index (23 sections)
3. [`docs/onboarding.md`](../docs/onboarding.md) — dev environment + conventions
4. [`planning/README.md`](README.md) — how epics + issues are organized
5. This file — current implementation state

---

## Progress summary

| Status                       | Count             |
| ---------------------------- | ----------------- |
| Epics closed                 | 7 of 16 (44%)     |
| Sub-issues closed            | 116 of 264 (~44%) |
| Architecture sections stable | 23 of 23 (100%)   |
| Foundation infrastructure    | Complete          |

### Closed epics

| #   | Epic                                | Sub-issues | Key deliverables                                                                                                                                                                                      |
| --- | ----------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| #1  | Project Foundation & Infrastructure | 14/14      | uv workspace, pnpm workspaces, docker-compose dev+prod, CI/integration/nightly workflows, shared Python + TS libraries, schema codegen pipeline, onboarding guide                                     |
| #16 | Event Bus & Messaging               | 9/9        | NATS JetStream config (4 streams + 9 consumers), `sentihome_shared.bus.Bus` with schema-validated pub/sub + trace propagation, triage worker with dedup + tiered routing + backpressure load shedding |
| #26 | NVR Adapter Layer                   | 19/19      | `NVRAdapter` ABC, 7 platform adapters (rtsp-direct, agent-dvr, frigate fully fleshed; blueiris partially; synology, qnap, unifi as v1.x skeletons), `AdapterRegistry` with env-driven bootstrap       |
| #46 | Preprocessing & Detection           | 21/21      | Real OpenCV MOG2 motion detection with temporal/size/zone/environmental filtering, on-camera AI corroboration, in-memory metadata cache, `Detector` facade with stubbed ML model registry             |
| #68 | VLM Router & Inference              | 13/13      | Multi-backend router (Ollama/vLLM/Cloud OpenAI-compatible), routing policy with privacy enforcement + affinity + cost/latency scoring, 3-state circuit breaker, fallback chain, telemetry, response repair |
| #82 | Memory & Storage                    | 22/22      | SQLAlchemy ORM (11 tables across 5 memory layers), Alembic migration tooling, MemoryStore facade (sessions, hybrid rule retrieval, episodic, visit ledger, identity), retention + soft-delete + grace |
| #105 | Rule Engine & Conversational Rule Creation | 11/11 | RuleEvaluator (all §10 conditions + temporal), ConflictResolver (scope+severity+suppression), heuristic NL RuleParser, DEFAULT_RULE_PACK with Tier-1 safety |

### Open epics (in dependency order)

| #    | Epic                                       | Sub-issues | Notes                                                                                                                                                                 |
| ---- | ------------------------------------------ | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| #117 | Action Dispatch & Alerting                 | #118-#133  | **Start here.** Tier 0–4 escalation, quiet hours, occupancy routing, ask flow, policy gate, remediation registry, deeper-assessment loop. Rule engine already complete.                                                              |
| #134 | Home Assistant Integration                 | #135-#156  | `services/ha-agent` (read+write MCP), `ha-integration/` custom HA component (entities, services, events).                                                             |
| #157 | Identity & Recognition                     | #158-#174  | Multi-modal identity, multi-camera fusion, stereo verification, temporal evidence accumulation, retroactive re-eval.                                                  |
| #175 | Feedback-Driven Optimization               | #176-#192  | Variant generator + replay engine + 4-phase rollout (silent → shadow → gradual → full) + rollback triggers.                                                           |
| #193 | Observability & Diagnostics                | #194-#207  | Metrics taxonomy, time-series storage, distributed tracing, AI synthesis layer, replay tooling.                                                                       |
| #208 | Privacy & Governance                       | #209-#222  | Privacy tier enforcement, scrubbing pipeline, retention, right-to-forget, multi-resident consent, GDPR/CCPA docs.                                                     |
| #223 | Calibration & Spatial Model                | #224-#237  | Camera/area/zone records, calibration UX flows, ground plane, stereo, PTZ presets.                                                                                    |
| #238 | Failure Modes & Resilience                 | #239-#253  | Watchdog + 10 documented failure modes (F1-F10) + safe defaults matrix + chaos testing.                                                                               |
| #254 | Documentation & Onboarding                 | #255-#264  | User install guide, NVR setup, first-rule walkthrough, troubleshooting, HACS packaging.                                                                               |

**Suggested epic order:** 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16 (the order in `planning/epics/`)

Epic 9 (HA Integration) can run in parallel with 6+7 after Epic 5 is done.

---

## Repo layout (current state)

```
SentiHome/
├── README.md, CONTRIBUTING.md, LICENSE, CODEOWNERS
├── pyproject.toml          uv workspace (16 members)
├── package.json            pnpm workspace (3 members)
├── pnpm-workspace.yaml, tsconfig.base.json, .prettierrc.json, .npmrc
├── eslint.config.mjs       Flat ESLint config
├── uv.lock, pnpm-lock.yaml committed for reproducible builds
│
├── docs/
│   ├── architecture/        23 sections, all stable
│   ├── ARCHITECTURE-CLARIFICATION.md  SentiHome vs HA boundary
│   └── onboarding.md
│
├── services/                7 Python services
│   ├── core/                Triage worker + adapter registry + rule engine  (REAL)
│   ├── preprocessor/        MOG2 motion + corroboration + cache  (REAL)
│   ├── detector/            Model facade (YOLO/face/reid/pose stubs)  (FACADE+STUBS)
│   ├── vlm-router/          Multi-backend router + policy + breaker + telemetry  (REAL)
│   ├── memory/              ORM + Alembic + MemoryStore facade  (REAL)
│   ├── ha-agent/            Skeleton  (SKELETON)
│   └── notify/              Skeleton  (SKELETON)
│
├── adapters/                7 NVR adapters
│   ├── nvr-rtsp-direct/     Direct RTSP, frame buffer protocol  (REAL)
│   ├── nvr-agent-dvr/       OpenAPI client + webhook receiver + adapter  (REAL)
│   ├── nvr-frigate/         REST client + MQTT payload normalizer + adapter  (REAL)
│   ├── nvr-blueiris/        Adapter shape + config  (PARTIAL)
│   ├── nvr-synology/        Skeleton conforming to contract  (V1.X SKELETON)
│   ├── nvr-qnap/            Skeleton conforming to contract  (V1.X SKELETON)
│   └── nvr-unifi/           Skeleton conforming to contract  (V1.X SKELETON)
│
├── ha-integration/          HA custom integration  (SKELETON; manifest.json + scaffold)
│
├── frontend/                TypeScript packages
│   ├── ha-cards/            Lit + Vite scaffold  (SKELETON)
│   └── operator-dashboard/  React + Vite scaffold  (SKELETON)
│
├── shared/
│   ├── schemas/             10 JSON schemas (events, common types)
│   │   ├── common/          privacy-tier, severity, nvr-capability, frame-window
│   │   └── events/          trigger, enriched, vlm-request, vlm-response, action
│   ├── protos/              (empty, populated as MCP contracts firm up)
│   ├── lib-python/          (REAL — logging, tracing, config, bus, mcp, adapter base)
│   │   └── src/sentihome_shared/
│   │       ├── adapter/     NVRAdapter ABC + dataclasses (Epic 3 contract)
│   │       ├── bus.py       Bus class — schema-validated NATS pub/sub
│   │       ├── config.py    pydantic + env-var config loader
│   │       ├── logging.py   structlog setup with trace ID context
│   │       ├── mcp.py       MCPError + PolicyGateError
│   │       ├── tracing.py   trace_context() + ID generators
│   │       └── generated/   pydantic models from schemas/ (DO NOT EDIT)
│   └── lib-typescript/      (SKELETON)
│       └── src/generated/   TS types from schemas/ (DO NOT EDIT)
│
├── infrastructure/
│   ├── docker/              dev.yml, prod.yml, python-service.Dockerfile, .env.example
│   ├── nats/                streams.yaml (4 streams + 9 consumers declared)
│   └── db-migrations/       (empty, populated by Epic 6)
│
├── tests/
│   ├── integration/         Real-NATS bus round-trip test + adapter contract conformance
│   ├── e2e/                 (empty)
│   └── fixtures/            (empty, populated as Epic 11 + e2e tests need them)
│
├── scripts/
│   ├── setup/install.sh
│   └── dev/                 up.sh, down.sh, restart.sh, logs.sh, format.sh, lint.sh,
│                            test.sh, regenerate-schemas.sh, sync-issues.py
│
├── planning/
│   ├── README.md            How epics + issues are organized
│   ├── STATUS.md            This file
│   └── epics/               16 markdown files (source of truth for GitHub Issues)
│
└── .github/
    ├── ISSUE_TEMPLATE/      epic.md, feature.md, bug.md
    └── workflows/           ci.yml, integration.yml, nightly-e2e.yml
```

---

## What "REAL" vs "STUB" means in the table above

- **REAL** — Actual logic, tested with real inputs (or external dependency mocked at boundaries). Production-bound code path.
- **PARTIAL** — Conforms to contract, has working pieces, but missing significant features.
- **SKELETON** — Subclass + entry point exists, methods raise `AdapterError` / `NotImplementedError`. The contract is satisfied so dependents can compile/test against it.
- **V1.X SKELETON** — Same as SKELETON but explicitly deferred per epic priority (e.g., adapters whose platform client research wasn't worth blocking v1).

---

## Conventions established so far (don't drift)

### Python

- **Python 3.12+**, `uv` for everything, PEP 695 generics (`def foo[T: BaseModel](...)`)
- `from __future__ import annotations` at the top of every module
- Async throughout for I/O paths
- Structured logging via `sentihome_shared.logging.get_logger(__name__)`
- Trace context: `from sentihome_shared.tracing import trace_context, new_trace_id`
- Per-package src layout: `src/<package_name>/...`, tests in `tests/`
- Test files named `test_<unique>.py` (not just `test_import.py` — pytest collection collides)
- Tests in `tests/` have NO `__init__.py` (avoids cross-package name collision)
- Ruff strict + format with line length 100; `pyproject.toml` per-file-ignores for stubs (ARG002)
- Mypy disabled in CI for now (re-enable once services have real implementations, not just stubs)
- Pydantic v2 for all schemas; generated models live under `shared/lib-python/src/sentihome_shared/generated/`

### TypeScript

- Strict mode everywhere; tsconfig extends `tsconfig.base.json`
- Lit for HA cards (interop with HA frontend); React for standalone dashboard
- `@sentihome/shared` exposes source via `main: src/index.ts` for zero-build workspace dev; `publishConfig` overrides to `dist/` for actual publishing

### Git workflow

- Commit message: `feat(epic-name): Epic N — description (#X, #Y, #Z)` and `Closes #N` lines for each sub-issue
  - **Note:** GitHub doesn't always auto-close all referenced issues when multiple are on `Closes:` lines. After commit, verify and manually `gh issue close N --reason completed` for any that stayed open.
- Each epic finishes with the parent epic issue closed via `gh issue close <epic-number> --reason completed --comment "..."`
- Squash-merge would be fine but we've been pushing directly to `main` for speed during scaffolding

### Schemas

- JSON Schema 2020-12, `$id` URI with version segment
- Run `./scripts/dev/regenerate-schemas.sh` after editing schemas
- Generated artifacts are committed
- Cross-schema `$ref` is brittle with `datamodel-code-generator` per-file mode; for now, inline shared enums in event schemas. Better $ref resolution is a deferred concern.
- Underscore Python module names (kebab-case `.schema.json` → snake_case `.py`)

### NVR Adapter Layer

- Every adapter inherits `sentihome_shared.adapter.NVRAdapter`
- Required: `name`, `mode`, `list_cameras`, `get_frame_window`, `subscribe_motion_events`
- Optional (raise `UnsupportedCapability` by default): `enrich_frame`, `get_stream_url`, `slew_ptz`, `switch_profile`
- Lifecycle: `start()` / `stop()` (default no-ops)
- Service-mode adapters delegate frame retrieval to the preprocessor; built-in adapters consume pre-enriched data; direct adapters are SentiHome's own preprocessing

### Event Bus

- All bus messages are pydantic `BaseModel` subclasses (validated on publish + receive)
- `trace_id` propagates via NATS message headers; received messages bind `trace_context` for handler logging
- Triage pattern: `dedup → score → shed → publish`
- Backpressure is cascading: urgent → normal → background → drop; `sensor.bypass` never sheds

---

## CI quirks to know about

- **pnpm install:** use `--ignore-scripts` flag (suppresses `[ERR_PNPM_IGNORED_BUILDS]` for `esbuild` which doesn't need native build)
- **Prettier:** do NOT pass `--ignore-path .gitignore` — let it use `.prettierignore` (which correctly excludes `pnpm-lock.yaml`)
- **Generated files:** both `shared/lib-typescript/src/generated/**` and `shared/lib-python/src/sentihome_shared/generated/**` are in `.prettierignore`; ruff `extend-exclude` covers Python generated
- **Qdrant in integration.yml:** no healthcheck (image lacks wget/curl); tests are responsible for waiting on readiness via the qdrant client
- **`docker` not installed in dev shell:** Validate compose YAML via Python `yaml.safe_load` instead of `docker compose config`
- **`@eslint/js`:** must be in `package.json` devDependencies (the flat config imports it)

---

## Resumption recipe for the next agent

```text
1. Read this file end-to-end.
2. Pick the next epic from the "Open epics" table — start with #68 (VLM Router).
3. List sub-issues:
     gh issue list --repo DarinShapiro/SentiHome --label "epic:vlm-router" --state open
4. Open the source-of-truth markdown for the epic:
     planning/epics/05-vlm-router.md
5. For each sub-issue:
     - Implement (real where possible, skeleton when an external dep is heavy)
     - Add tests
     - Update relevant generated artifacts if schemas changed
     - Commit with `Closes #N` per sub-issue
     - Verify GitHub state: `gh issue view N` — if not auto-closed, manually close
6. After the last sub-issue in the epic closes, close the epic parent:
     gh issue close <epic-num> --reason completed --comment "..."
7. Update this file's progress summary + closed-epics table.
8. Run the full quality gate before pushing:
     ./scripts/dev/test.sh
     ./scripts/dev/lint.sh
     uv run pytest services/ adapters/ shared/ -q
9. Push. Wait for CI:
     RUN_ID=$(gh run list --repo DarinShapiro/SentiHome --workflow=CI --limit 1 --json databaseId --jq '.[0].databaseId')
     gh run watch "$RUN_ID" --repo DarinShapiro/SentiHome --exit-status
10. Move to the next epic.
```

---

## Things deliberately deferred (don't accidentally do these)

- **ONNX-based real ML inference** in `services/detector/` — facade + stubs are enough until VLM router (Epic 5) is done; real model integration happens after a few epics have shaken out the contract surface
- **Native Agent DVR plugin** — service-mode is the v1 baseline; native mode is a v2 optimization
- **Stereo calibration full implementation** — Epic 14 ships the data model; full UX is real-world-data-dependent (see `docs/architecture/18-hardware-sizing.md` "preliminary" status)
- **SentiHome Plugin API open spec** — far-future, after first native plugin ships
- **Hardware sizing refinement** — preliminary until maintainer's household deployment produces real data

These are captured in `docs/architecture/20-open-questions.md`; don't re-litigate them without strong evidence.

---

## Open questions / hand-off notes

- **Mypy:** is currently disabled in CI (line 56 of `.github/workflows/ci.yml` is commented out) because the service stubs would generate noise. Once Epics 5-9 land real implementations, re-enable mypy strict and clean up any type errors that surface.
- **Integration test invocation:** `tests/integration/test_bus_roundtrip.py` connects to `nats://localhost:4222` (overridable via `NATS_URL` env var). The `integration.yml` workflow provides NATS as a service container. Locally: `./scripts/dev/up.sh nats && uv run pytest tests/integration`.
- **Schema `$ref` workaround:** `trigger-event.schema.json` inlines the `privacy_tier` enum rather than `$ref`-ing `common/privacy-tier.schema.json` because `datamodel-code-generator` per-file mode doesn't resolve cross-file refs cleanly. When Epic 5+ wants more sophisticated cross-schema references, either:
  - Switch to a $ref-aware codegen tool (e.g., `quicktype`), or
  - Use the directory-walk mode of datamodel-codegen and filter out non-schema files

---

## Test counts by epic

| Epic              | Test files                                                                           | Tests passing                    |
| ----------------- | ------------------------------------------------------------------------------------ | -------------------------------- |
| 1 (Foundation)    | smoke imports + shared lib                                                           | 17                               |
| 2 (Event Bus)     | triage worker                                                                        | 23 + integration                 |
| 3 (NVR Adapters)  | rtsp-direct, agent-dvr, frigate, blueiris, synology, qnap, unifi, registry, contract | 53                               |
| 4 (Preprocessing) | motion, corroboration, cache, detector facade                                        | 31                               |
| 5 (VLM Router)    | breaker, telemetry, policy, router, response_repair                                  | 39                               |
| 6 (Memory)        | ORM models (all 11 tables), retention policy, MemoryStore facade                     | 30                               |
| 7 (Rule Engine)   | RuleEvaluator (12), ConflictResolver (6), RuleParser NL (7), default pack (2), lifecycle (4) | 32                         |
| **Total**         |                                                                                      | **229 unit + integration suite** |

---

## Commands cheat sheet

```bash
# Lint + test cycle
./scripts/dev/format.sh && ./scripts/dev/lint.sh && ./scripts/dev/test.sh

# Just Python tests
uv run pytest services/ adapters/ shared/lib-python/ -q

# Just one package
uv run pytest services/core/tests -q

# Regenerate schemas after editing shared/schemas/
./scripts/dev/regenerate-schemas.sh

# Bring up dev stack (when docker is available)
./scripts/dev/up.sh

# Sync any new epic issues from planning/epics/*.md to GitHub
python scripts/dev/sync-issues.py

# Inspect issue status
gh issue list --repo DarinShapiro/SentiHome --label "epic:<slug>" --state open
gh issue view <num> --repo DarinShapiro/SentiHome

# Watch CI
RUN_ID=$(gh run list --repo DarinShapiro/SentiHome --workflow=CI --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN_ID" --repo DarinShapiro/SentiHome --exit-status
```

---

**Next session — pick up at Epic 5 (#68 VLM Router & Inference).** Good luck.
