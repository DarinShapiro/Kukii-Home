#!/usr/bin/env bash
# scripts/dev/up.sh — Bring up the Kukii-Home dev stack.
#
# Usage:
#   ./scripts/dev/up.sh            # all services
#   ./scripts/dev/up.sh nats core  # specific services
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

exec docker compose -f infrastructure/docker/dev.yml up -d "$@"
