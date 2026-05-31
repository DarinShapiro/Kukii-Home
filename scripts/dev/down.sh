#!/usr/bin/env bash
# scripts/dev/down.sh — Stop the Kukii-Home dev stack.
#
# Usage:
#   ./scripts/dev/down.sh            # stop services, keep volumes
#   ./scripts/dev/down.sh --volumes  # also remove volumes (DB data, etc.)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [[ "${1:-}" == "--volumes" ]]; then
  exec docker compose -f infrastructure/docker/dev.yml down -v
else
  exec docker compose -f infrastructure/docker/dev.yml down
fi
