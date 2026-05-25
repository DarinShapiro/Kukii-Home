#!/usr/bin/env bash
# scripts/dev/test.sh — Run unit tests across the monorepo.
#
# Usage:
#   ./scripts/dev/test.sh            # all unit tests
#   ./scripts/dev/test.sh python     # Python only
#   ./scripts/dev/test.sh typescript # TS only
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

target="${1:-all}"
errors=0

if [[ "$target" == "all" || "$target" == "python" ]]; then
  blue "▶ Python unit tests (pytest)..."
  if uv run pytest services/ adapters/ shared/lib-python/ -q -m "not slow and not integration and not e2e"; then
    green "  pytest: OK"
  else
    red "  pytest: FAILED"
    ((errors++))
  fi
fi

if [[ "$target" == "all" || "$target" == "typescript" ]]; then
  blue "▶ TypeScript unit tests (vitest)..."
  if pnpm -r test 2>&1 | tail -10; then
    green "  vitest: OK"
  else
    red "  vitest: FAILED"
    ((errors++))
  fi
fi

if (( errors > 0 )); then
  red "✗ $errors test failure(s)."
  exit 1
fi

green "✓ All tests pass."
