#!/usr/bin/env bash
# scripts/setup/install.sh — First-time SentiHome dev environment setup.
#
# Checks prerequisites, installs Python + TypeScript dependencies, brings the
# docker-compose dev stack up, and reports next steps.
#
# Idempotent: safe to re-run.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
blue()   { printf '\033[34m%s\033[0m\n' "$*"; }

check_cmd() {
  local cmd="$1"
  local hint="${2:-}"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    red "  ✗ $cmd not found"
    [[ -n "$hint" ]] && yellow "    → $hint"
    return 1
  fi
  green "  ✓ $cmd"
  return 0
}

# ────────────────────────────────────────────────────────────────
# Prerequisites
# ────────────────────────────────────────────────────────────────
blue "▶ Checking prerequisites..."
errors=0

check_cmd git || ((errors++))
check_cmd python3 "Install Python 3.12+: https://www.python.org/" || \
  check_cmd python "Install Python 3.12+: https://www.python.org/" || ((errors++))
check_cmd uv "Install uv: https://docs.astral.sh/uv/" || ((errors++))
check_cmd node "Install Node 22+: https://nodejs.org/" || ((errors++))
check_cmd pnpm "Install pnpm: npm install -g pnpm" || ((errors++))
check_cmd docker "Install Docker Desktop: https://www.docker.com/" || ((errors++))

if (( errors > 0 )); then
  red "✗ $errors missing prerequisite(s). Install them and re-run."
  exit 1
fi
green "All prerequisites present."
echo

# ────────────────────────────────────────────────────────────────
# Python workspace
# ────────────────────────────────────────────────────────────────
blue "▶ Syncing Python workspace (uv)..."
uv sync --all-packages
green "Python workspace synced."
echo

# ────────────────────────────────────────────────────────────────
# TypeScript workspace
# ────────────────────────────────────────────────────────────────
blue "▶ Installing TypeScript dependencies (pnpm)..."
pnpm install
green "TypeScript workspace installed."
echo

# ────────────────────────────────────────────────────────────────
# Environment file
# ────────────────────────────────────────────────────────────────
ENV_FILE="infrastructure/docker/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  blue "▶ Creating $ENV_FILE from .env.example..."
  cp infrastructure/docker/.env.example "$ENV_FILE"
  yellow "  → Edit $ENV_FILE to set HA_TOKEN and other credentials."
fi
echo

# ────────────────────────────────────────────────────────────────
# Pre-commit (optional, but recommended)
# ────────────────────────────────────────────────────────────────
if command -v pre-commit >/dev/null 2>&1; then
  blue "▶ Installing pre-commit hooks..."
  pre-commit install --install-hooks 2>/dev/null || yellow "  (pre-commit hooks not configured yet)"
fi

# ────────────────────────────────────────────────────────────────
# Done
# ────────────────────────────────────────────────────────────────
green "✓ Setup complete."
echo
blue "Next steps:"
echo "  1. Edit infrastructure/docker/.env with your HA token + cloud keys"
echo "  2. Bring up the dev stack:  ./scripts/dev/up.sh"
echo "  3. Run tests:               uv run pytest && pnpm test"
echo "  4. Browse the architecture: docs/architecture/README.md"
echo "  5. Pick an issue to work:   https://github.com/DarinShapiro/SentiHome/issues"
