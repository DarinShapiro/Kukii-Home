#!/usr/bin/env bash
# scripts/dev/logs.sh — Stream logs from the dev stack.
#
# Usage:
#   ./scripts/dev/logs.sh           # all services
#   ./scripts/dev/logs.sh core      # specific service
#   ./scripts/dev/logs.sh -f core   # follow
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Default to follow with last 100 lines if no args
if [[ $# -eq 0 ]]; then
  exec docker compose -f infrastructure/docker/dev.yml logs -f --tail=100
else
  exec docker compose -f infrastructure/docker/dev.yml logs "$@"
fi
