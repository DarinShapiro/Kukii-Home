# scripts/

Operational scripts: first-time setup, day-to-day dev tooling, release automation.

## Layout

| Folder                 | Purpose                                                                                                   |
| ---------------------- | --------------------------------------------------------------------------------------------------------- |
| [`setup/`](setup/)     | First-time installer: dependencies, docker compose, DB migrations, NATS streams                           |
| [`dev/`](dev/)         | Day-to-day dev workflow: regenerate schemas, format/lint everything, seed test data, run focused services |
| [`release/`](release/) | Versioning, changelog generation, image tagging, GitHub release creation                                  |

## Conventions

- **Shell:** bash for portability; PowerShell variants where Windows-specific behavior matters
- **Python tooling:** scripts that need Python use `uv run` so they don't depend on a global environment
- **Idempotency:** every script should be safe to re-run; never assume clean state
- **Documentation:** every script has a `--help` summary and a top-of-file comment explaining purpose

## Common workflows

```bash
# First-time setup
./scripts/setup/install.sh
./scripts/setup/migrate.sh

# Day-to-day
./scripts/dev/up.sh                  # start full stack
./scripts/dev/down.sh                # stop full stack
./scripts/dev/regenerate-schemas.sh  # after editing shared/schemas/
./scripts/dev/seed-fixtures.sh       # load test data into the running stack
./scripts/dev/format.sh              # run all formatters (black, prettier, etc.)
./scripts/dev/lint.sh                # run all linters

# Release (run by maintainers)
./scripts/release/version.sh patch   # bump version
./scripts/release/changelog.sh       # regenerate CHANGELOG.md from commits
./scripts/release/publish.sh         # tag, build images, publish
```
