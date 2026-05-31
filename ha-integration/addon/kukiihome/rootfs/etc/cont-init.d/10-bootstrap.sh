#!/usr/bin/with-contenv bash
# Runs once before s6 starts the long-running services.
# - Hands Supervisor's /data/options.json to the Kukii-Home topology loader
#   by exposing it via KUKIIHOME_CONFIG.
# - Surfaces SUPERVISOR_TOKEN under HA_TOKEN as a convenience.
# - Picks the log level out of options.json and exports LOG_LEVEL.
set -euo pipefail

OPTIONS_FILE="/data/options.json"
if [ ! -f "$OPTIONS_FILE" ]; then
    echo "[bootstrap] no /data/options.json present; falling back to defaults"
    exit 0
fi

export KUKIIHOME_CONFIG="$OPTIONS_FILE"

LOG_LEVEL="$(jq -r '.log_level // "INFO"' "$OPTIONS_FILE")"
echo "$LOG_LEVEL" > /var/run/s6/container_environment/LOG_LEVEL

# Epic 10.9: preprocessor (inference box) URL. When set, the ha-agent
# enriches each alert with the preprocessor's recognition. Empty/absent
# -> enrichment is simply skipped (alerts keep the HA snapshot + rule).
PREPROCESSOR_URL="$(jq -r '.preprocessor_url // ""' "$OPTIONS_FILE")"
echo "$PREPROCESSOR_URL" > /var/run/s6/container_environment/KUKIIHOME_PREPROCESSOR_URL

# When Supervisor injects a token, expose it under both the HA_TOKEN and
# SUPERVISOR_TOKEN names so the topology loader and the ha-agent both
# pick it up regardless of which one they look for.
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    echo "$SUPERVISOR_TOKEN" > /var/run/s6/container_environment/HA_TOKEN
fi

echo "$KUKIIHOME_CONFIG" > /var/run/s6/container_environment/KUKIIHOME_CONFIG

echo "[bootstrap] Kukii-Home topology config: $KUKIIHOME_CONFIG (log_level=$LOG_LEVEL)"
