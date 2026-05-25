# Developer Onboarding

Get from a fresh clone to your first PR in under 30 minutes.

---

## Prerequisites

| Tool | Version | Why | Install |
|------|---------|-----|---------|
| **git** | any recent | source control | system package manager |
| **Python** | 3.12+ | core services | [python.org](https://www.python.org/) |
| **uv** | 0.11+ | Python package manager | [docs.astral.sh/uv](https://docs.astral.sh/uv/) |
| **Node.js** | 22+ | frontend builds | [nodejs.org](https://nodejs.org/) |
| **pnpm** | 11+ | TypeScript monorepo | `npm install -g pnpm` |
| **Docker** | recent | dev stack (NATS, Postgres, Qdrant, Redis) | [Docker Desktop](https://www.docker.com/products/docker-desktop/) |

Check yours:

```bash
git --version
python3 --version       # need 3.12.0+
uv --version
node --version          # need v22+
pnpm --version
docker --version
```

---

## First-time setup

```bash
git clone https://github.com/DarinShapiro/SentiHome.git
cd SentiHome
./scripts/setup/install.sh
```

The installer:

1. Verifies prerequisites
2. `uv sync --all-packages` — installs Python workspace (services, adapters, shared library, ha-integration)
3. `pnpm install` — installs TypeScript workspace (shared lib, HA cards, operator dashboard)
4. Creates `infrastructure/docker/.env` from `.env.example`
5. Reports next steps

---

## Daily workflow

```bash
# Bring up the dev stack (NATS, Postgres, Qdrant, Redis, all services)
./scripts/dev/up.sh

# Tail logs
./scripts/dev/logs.sh             # all services, follow
./scripts/dev/logs.sh core        # just one service
./scripts/dev/logs.sh -f --tail 50 core

# Restart a service after editing
./scripts/dev/restart.sh core

# Stop everything
./scripts/dev/down.sh             # keeps volumes (DB data)
./scripts/dev/down.sh --volumes   # wipes everything

# Format
./scripts/dev/format.sh           # ruff format (Python) + prettier (everything else)

# Lint
./scripts/dev/lint.sh             # ruff check, ruff format --check, prettier --check,
                                  # eslint, tsc --noEmit

# Test (unit only — fast)
./scripts/dev/test.sh             # pytest + vitest
./scripts/dev/test.sh python      # just Python
./scripts/dev/test.sh typescript  # just TypeScript

# Regenerate schema bindings after editing shared/schemas/
./scripts/dev/regenerate-schemas.sh
```

---

## Repo layout

```
SentiHome/
├── docs/                      # Architecture (21 sections) + clarifications + this guide
├── services/                  # Python services (one folder each)
│   ├── core/                  # Orchestration brain (triage, rules, dispatch)
│   ├── preprocessor/          # Motion + frame markup for service-mode NVR adapters
│   ├── detector/              # YOLO, face recognition, re-ID models
│   ├── vlm-router/            # Multi-backend VLM (local Ollama + cloud fallback)
│   ├── memory/                # SQL + vector DB MCP server
│   ├── ha-agent/              # Bidirectional HA MCP server
│   └── notify/                # Push, TTS, ask dispatcher
├── adapters/                  # Pluggable NVR adapters
│   ├── nvr-rtsp-direct/       # No NVR — direct from cameras
│   ├── nvr-agent-dvr/         # Agent DVR (OpenAPI 2.0)
│   ├── nvr-frigate/           # Frigate (MQTT + REST, built-in mode)
│   ├── nvr-blueiris/          # Blue Iris (HA integration + RTSP)
│   ├── nvr-synology/          # Synology Surveillance Station
│   ├── nvr-qnap/              # QNAP QVR Pro
│   └── nvr-unifi/             # UniFi Protect
├── ha-integration/            # Home Assistant custom integration (Python)
├── frontend/
│   ├── ha-cards/              # Lit-based custom Lovelace cards
│   └── operator-dashboard/    # React standalone dashboard (optional)
├── shared/
│   ├── schemas/               # JSON Schema definitions (source of truth)
│   ├── protos/                # MCP protocol definitions
│   ├── lib-python/            # Shared Python utilities (bus, MCP, logging, tracing, schemas)
│   └── lib-typescript/        # Shared TS utilities + generated types
├── infrastructure/
│   ├── docker/                # docker-compose dev + prod stacks, Dockerfile
│   ├── nats/                  # JetStream stream + consumer config
│   └── db-migrations/         # Alembic migrations for postgres
├── tests/
│   ├── integration/           # Multi-service tests
│   ├── e2e/                   # Full-system tests (nightly)
│   └── fixtures/              # Recorded clips + ground truth
├── scripts/
│   ├── setup/install.sh
│   └── dev/                   # up, down, restart, logs, format, lint, test, regenerate-schemas, sync-issues
├── planning/                  # Epic + issue specs (source of truth for GitHub Issues)
│   └── epics/                 # One markdown file per epic
└── .github/
    ├── workflows/             # CI (PR + main), integration (PR), nightly e2e
    └── ISSUE_TEMPLATE/        # Epic, feature, bug templates
```

---

## Finding your first issue

```bash
# All open issues
gh issue list --repo DarinShapiro/SentiHome

# By epic
gh issue list --repo DarinShapiro/SentiHome --label epic:foundation
gh issue list --repo DarinShapiro/SentiHome --label epic:nvr-adapters

# By component
gh issue list --repo DarinShapiro/SentiHome --label component:core

# By priority
gh issue list --repo DarinShapiro/SentiHome --label priority:p0
```

Issues are organized as epics (`type:epic` label) with sub-issues linked from each epic's task list. Pick a sub-issue, comment to claim it, and open a draft PR.

---

## Architecture deep-dive

The architecture docs live in `docs/architecture/`. They're versioned and stable. Each section has a **Purpose** line and a **Status** label.

Start with:

1. [`docs/architecture/README.md`](./architecture/README.md) — index
2. [`docs/architecture/01-overview.md`](./architecture/01-overview.md) — vision, principles, glossary
3. [`docs/architecture/02-high-level-architecture.md`](./architecture/02-high-level-architecture.md) — component map
4. [`docs/ARCHITECTURE-CLARIFICATION.md`](./ARCHITECTURE-CLARIFICATION.md) — SentiHome vs. HA boundary

Then read the section relevant to the issue you're working:

- Working on an adapter? → §03.5 NVR Adapter Layer
- Working on preprocessing? → §08 Detection Pipeline
- Working on rule engine? → §10 + §10.5
- Working on identity? → §12 + §12.5
- Working on alerts? → §15
- Working on observability? → §17

---

## Conventions

### Python

- **Strict typing**: every function has type hints; mypy strict (re-enabled once stubs become real code)
- **Async first**: I/O paths are async by default
- **Logging**: `from sentihome_shared.logging import get_logger; log = get_logger(__name__)`
- **Tracing**: every event gets a `trace_id` propagated via `sentihome_shared.tracing`
- **Tests**: `pytest` with `pytest-asyncio` in mode="auto"; tests in `tests/` adjacent to source
- **Test markers**: `@pytest.mark.slow`, `@pytest.mark.integration`, `@pytest.mark.e2e` — fast tests run on PR, slow ones nightly

### TypeScript

- **Strict mode**: tsconfig.base.json has every strict flag on
- **Lit** for HA cards (interops with HA's frontend), **React** for the standalone dashboard
- **Generated types**: never edit `src/generated/` — regenerate from schemas instead

### Commits

- Conventional Commits prefixes: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`, `perf`
- Reference issue: `feat(core): add triage worker (#42)` or `Closes #42` in body
- Imperative mood

### PRs

- Branch from main: `feat/<short-description>`, `fix/<short-description>`, etc.
- Open draft early; mark ready when CI passes
- Squash-merge by default

---

## CI

Three workflows guard `main`:

| Workflow | Trigger | Time | What it does |
|----------|---------|------|--------------|
| **CI** | PR + push to main | ~5 min | ruff, prettier, eslint, tsc, pytest (fast), vitest |
| **Integration** | PR + push to main | ~10 min | Spins up NATS+Postgres+Qdrant+Redis, runs `tests/integration/` |
| **Nightly e2e** | 06:00 UTC | ~30 min | Full docker-compose stack + `tests/e2e/` |

CI cancels superseded PR runs automatically.

---

## Troubleshooting

### `uv sync` fails

```bash
rm -rf .venv uv.lock
uv sync --all-packages
```

### `pnpm install` fails with `ERR_PNPM_IGNORED_BUILDS`

```bash
pnpm install --ignore-scripts
```

(esbuild's optional native binary build script — we don't need it.)

### Tests fail with "ModuleNotFoundError"

You probably need to install workspace members:

```bash
uv sync --all-packages
```

### Docker compose says "service not found"

Check that you're invoking from the repo root:

```bash
docker compose -f infrastructure/docker/dev.yml ps
```

Or use the wrapper:

```bash
./scripts/dev/up.sh
```

### Schemas changed but generated types didn't update

```bash
./scripts/dev/regenerate-schemas.sh
git diff shared/lib-python/src/sentihome_shared/generated/
git diff shared/lib-typescript/src/generated/
```

CI fails if you forget to commit regenerated artifacts.

---

## Where to get help

- Open an issue with `type:question` label
- Architectural decisions: read `docs/architecture/20-open-questions.md`
- Code conventions or PR feedback: `CONTRIBUTING.md`
