#!/usr/bin/env bash
# scripts/dev/lint.sh — Lint + type-check across the monorepo.
#
# Runs:
#   - ruff check        (Python lint)
#   - mypy              (Python types)
#   - prettier --check  (TS/JSON/MD/YAML formatting)
#   - eslint            (TypeScript lint)
#   - tsc --noEmit      (TypeScript types)
#
# Exits non-zero on any failure.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

errors=0

blue "▶ ruff check (Python lint)..."
if uv run ruff check services/ adapters/ shared/lib-python/; then
  green "  ruff check: OK"
else
  red "  ruff check: FAILED"
  ((errors++))
fi

blue "▶ ruff format --check (Python format)..."
if uv run ruff format --check services/ adapters/ shared/lib-python/ 2>&1 | tail -3; then
  green "  ruff format: OK"
else
  red "  ruff format: FAILED — run ./scripts/dev/format.sh"
  ((errors++))
fi

blue "▶ mypy (Python types) — skipping until services have real implementations"
# TODO: enable once services have real code (currently just stubs)
# uv run mypy services/ adapters/ shared/lib-python/ || ((errors++))

blue "▶ prettier --check (TS/JSON/MD/YAML)..."
if pnpm format:check 2>&1 | tail -5; then
  green "  prettier: OK"
else
  red "  prettier: FAILED — run ./scripts/dev/format.sh"
  ((errors++))
fi

blue "▶ eslint (TypeScript)..."
if pnpm lint 2>&1 | tail -5; then
  green "  eslint: OK"
else
  red "  eslint: FAILED"
  ((errors++))
fi

blue "▶ tsc --noEmit (TypeScript types)..."
if pnpm typecheck 2>&1 | tail -5; then
  green "  typecheck: OK"
else
  red "  typecheck: FAILED"
  ((errors++))
fi

if (( errors > 0 )); then
  red "✗ $errors lint failure(s)."
  exit 1
fi

green "✓ All linters pass."
