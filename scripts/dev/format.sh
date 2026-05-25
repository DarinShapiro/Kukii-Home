#!/usr/bin/env bash
# scripts/dev/format.sh — Apply formatters across the monorepo.
#
# Runs:
#   - ruff format       (Python)
#   - prettier --write  (TypeScript, JSON, Markdown, YAML)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

blue "▶ Formatting Python (ruff)..."
uv run ruff format services/ adapters/ shared/lib-python/ scripts/dev/sync-issues.py 2>&1 | tail -5
green "  Python formatted."

blue "▶ Formatting TypeScript / JSON / Markdown / YAML (prettier)..."
pnpm format 2>&1 | tail -5
green "  Frontend formatted."

green "✓ Formatting complete."
