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

# Iter 3 / Part X §35: LLM endpoint for the conversational dispatcher.
# Empty url OR empty api_key -> drawer falls back to heuristic placement
# (still functional, just less nuanced). Default model is the
# llama-3.3-70b that Cerebras hosts; users can override per provider.
# We never log the key value — only whether it's set or not.
LLM_URL="$(jq -r '.llm_url // ""' "$OPTIONS_FILE")"
LLM_API_KEY="$(jq -r '.llm_api_key // ""' "$OPTIONS_FILE")"
LLM_MODEL="$(jq -r '.llm_model // "llama-3.3-70b"' "$OPTIONS_FILE")"
echo "$LLM_URL"     > /var/run/s6/container_environment/KUKIIHOME_LLM_URL
echo "$LLM_API_KEY" > /var/run/s6/container_environment/KUKIIHOME_LLM_API_KEY
echo "$LLM_MODEL"   > /var/run/s6/container_environment/KUKIIHOME_LLM_MODEL

# Sanity log — confirms wiring without leaking the secret.
if [ -n "$LLM_URL" ] && [ -n "$LLM_API_KEY" ]; then
    echo "[bootstrap] LLM dispatcher: ${LLM_URL} (model=${LLM_MODEL}, api_key=set)"
else
    echo "[bootstrap] LLM dispatcher: not configured (heuristic-only)"
fi

# When Supervisor injects a token, expose it under both the HA_TOKEN and
# SUPERVISOR_TOKEN names so the topology loader and the ha-agent both
# pick it up regardless of which one they look for.
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    echo "$SUPERVISOR_TOKEN" > /var/run/s6/container_environment/HA_TOKEN
fi

echo "$KUKIIHOME_CONFIG" > /var/run/s6/container_environment/KUKIIHOME_CONFIG

echo "[bootstrap] Kukii-Home topology config: $KUKIIHOME_CONFIG (log_level=$LOG_LEVEL)"
