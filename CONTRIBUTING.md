# Contributing to Kukii-Home

Thanks for your interest. This is a pre-implementation project — the architecture is documented, scaffolding is in place, and we're working through the GitHub epics that represent the v1 implementation.

## Getting started

```bash
# Clone
git clone https://github.com/DarinShapiro/Kukii-Home.git
cd Kukii-Home

# Read the architecture
open docs/architecture/README.md   # start here

# Browse the implementation roadmap
open planning/README.md
```

## Development setup

> **Note:** Dev environment scripts are part of the foundation epic and not yet implemented. The flow below describes the target end state.

```bash
# First-time setup (installs dependencies, prepares docker compose)
./scripts/setup/install.sh

# Bring up the dev stack
./scripts/dev/up.sh

# Run tests
./scripts/dev/test.sh
```

## How to contribute

1. **Pick an issue.** Browse [open issues](https://github.com/DarinShapiro/Kukii-Home/issues). Issues are grouped under epics (`type:epic` label).
2. **Comment on the issue** to claim it before starting work.
3. **Branch from `main`.** Branch naming: `feat/<short-description>`, `fix/<short-description>`, `docs/<short-description>`.
4. **Open a draft PR early.** Link the issue with `Closes #N` in the description.
5. **Tests and docs go with the change.** No "tests later" PRs.
6. **Request review** when ready. Maintainer review required before merge.

## Code conventions

### Python (services, adapters, ha-integration)

- Python 3.12+, `uv` for dependency management
- `ruff` for linting + formatting (PEP 8, with project overrides)
- `mypy --strict` for type checking
- `pytest` + `pytest-asyncio` for tests
- Async by default for I/O paths

### TypeScript (frontend)

- TypeScript strict mode
- `eslint` + `prettier`
- `vitest` for unit tests, `playwright` for e2e
- Lit for HA cards (interop with HA frontend), React for standalone dashboard

### Commit messages

- Imperative mood, short subject line (<72 chars)
- Reference issue number when applicable: `feat(core): add triage worker (#42)`
- Conventional Commits prefixes: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`

## Architecture decisions

If you're proposing something that changes the architecture (vs. implementing what's documented), open an issue with the `type:adr` label first. Architecture changes are captured in [`docs/architecture/20-open-questions.md`](docs/architecture/20-open-questions.md).

## Code of conduct

Be kind. Critique ideas, not people. We're building something that lives inside other people's homes — treat that responsibility seriously.

## Questions?

Open an issue with the `type:question` label.
