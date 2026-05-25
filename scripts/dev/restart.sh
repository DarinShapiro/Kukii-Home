#!/usr/bin/env bash
# scripts/dev/restart.sh — Restart a service (or the whole stack).
#
# Usage:
#   ./scripts/dev/restart.sh           # restart all
#   ./scripts/dev/restart.sh core      # restart just core
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

exec docker compose -f infrastructure/docker/dev.yml restart "$@"
