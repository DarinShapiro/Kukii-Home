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
LLM_MODEL="$(jq -r '.llm_model // "gpt-oss-120b"' "$OPTIONS_FILE")"
echo "$LLM_URL"     > /var/run/s6/container_environment/KUKIIHOME_LLM_URL
echo "$LLM_API_KEY" > /var/run/s6/container_environment/KUKIIHOME_LLM_API_KEY
echo "$LLM_MODEL"   > /var/run/s6/container_environment/KUKIIHOME_LLM_MODEL

# Sanity log — confirms wiring without leaking the secret.
if [ -n "$LLM_URL" ] && [ -n "$LLM_API_KEY" ]; then
    echo "[bootstrap] LLM dispatcher: ${LLM_URL} (model=${LLM_MODEL}, api_key=set)"
else
    echo "[bootstrap] LLM dispatcher: not configured (heuristic-only)"
fi

# Epic 10.2: memory graph (Neo4j sidecar). Only export KUKIIHOME_NEO4J_URL
# when neo4j_enabled is true — an empty URL makes the agent's graph factory
# use the in-process in-memory backend (the dual-write seam still runs,
# just non-persistent). When enabled, the s6 neo4j service starts the
# bundled sidecar and the agent connects at neo4j_url. The agent falls
# back to in-memory if the sidecar isn't reachable, so this never blocks
# boot. We never log the password value — only whether the graph is on.
NEO4J_ENABLED="$(jq -r '.neo4j_enabled // false' "$OPTIONS_FILE")"
NEO4J_URL="$(jq -r '.neo4j_url // "bolt://localhost:7687"' "$OPTIONS_FILE")"
NEO4J_USER="$(jq -r '.neo4j_user // "neo4j"' "$OPTIONS_FILE")"
NEO4J_PASSWORD="$(jq -r '.neo4j_password // "kukiihome"' "$OPTIONS_FILE")"
if [ "$NEO4J_ENABLED" = "true" ]; then
    echo "$NEO4J_URL"      > /var/run/s6/container_environment/KUKIIHOME_NEO4J_URL
    echo "$NEO4J_USER"     > /var/run/s6/container_environment/KUKIIHOME_NEO4J_USER
    echo "$NEO4J_PASSWORD" > /var/run/s6/container_environment/KUKIIHOME_NEO4J_PASSWORD
    # The neo4j s6 service reads these to set the initial admin password +
    # data dir. Written regardless of the agent-side vars above.
    echo "$NEO4J_PASSWORD" > /var/run/s6/container_environment/NEO4J_INITIAL_PASSWORD
    echo "[bootstrap] memory graph: Neo4j sidecar ENABLED (${NEO4J_URL}, password=set)"
else
    # Empty URL -> agent uses in-memory graph. Explicitly clear it so a
    # stale value from a prior run can't leak in.
    echo "" > /var/run/s6/container_environment/KUKIIHOME_NEO4J_URL
    echo "[bootstrap] memory graph: in-memory (Neo4j sidecar disabled)"
fi

# When Supervisor injects a token, expose it under both the HA_TOKEN and
# SUPERVISOR_TOKEN names so the topology loader and the ha-agent both
# pick it up regardless of which one they look for.
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    echo "$SUPERVISOR_TOKEN" > /var/run/s6/container_environment/HA_TOKEN
fi

echo "$KUKIIHOME_CONFIG" > /var/run/s6/container_environment/KUKIIHOME_CONFIG

echo "[bootstrap] Kukii-Home topology config: $KUKIIHOME_CONFIG (log_level=$LOG_LEVEL)"
