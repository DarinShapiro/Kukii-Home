#!/usr/bin/with-contenv bash
# Epic 10.8.4: bundle the SentiHome custom integration with the add-on
# and auto-install it to /config/custom_components/sentihome/ on every
# boot. Eliminates the two-component install dance that caused the
# v0.3.15/17/20/23 notification-tap failures (add-on emitted URLs the
# integration didn't yet handle).
#
# Behavior:
# 1. Compute a hash over the bundled integration files in /app/.
# 2. Compare to the previous install's hash stamped in /config.
# 3. If different (or no prior install): rsync the integration in,
#    write the new hash stamp, and request an HA Core restart so the
#    new integration code takes effect.
# 4. If unchanged: no-op (idempotent — common path on every boot).
#
# Restart is requested via the Supervisor REST API. SUPERVISOR_TOKEN
# is automatic when the add-on declares homeassistant_api: true.
# A failed restart request just logs — the user can restart manually,
# and HA will pick up the new code on next reload either way.

set -euo pipefail

SRC=/app/ha-integration/custom_components/sentihome
DST=/config/custom_components/sentihome
STAMP=/config/.sentihome_integration_version

if [ ! -d "$SRC" ]; then
    echo "[integration-install] source not bundled at $SRC — skipping"
    exit 0
fi

# Hash all .py + manifest.json files in the bundled integration.
# sha256sum is in the base image (coreutils). awk strips filenames so
# the hash only reflects content, not paths.
NEW_HASH="$(find "$SRC" -type f \( -name '*.py' -o -name '*.json' \) -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    | awk '{print $1}' \
    | sha256sum \
    | awk '{print $1}')"

PREV_HASH=""
if [ -f "$STAMP" ]; then
    PREV_HASH="$(cat "$STAMP")"
fi

if [ "$NEW_HASH" = "$PREV_HASH" ] && [ -d "$DST" ]; then
    echo "[integration-install] integration up-to-date (hash $NEW_HASH); no-op"
    exit 0
fi

echo "[integration-install] installing/refreshing integration at $DST"
mkdir -p "$DST"
# rsync would be cleaner but isn't in the base image. cp -a + rm -rf
# the stale dir gives the same effect.
rm -rf "$DST"
mkdir -p "$DST"
cp -a "$SRC"/. "$DST/"
echo "$NEW_HASH" > "$STAMP"
echo "[integration-install] wrote integration files; new hash $NEW_HASH"

# Surface a PERSISTENT NOTIFICATION asking the user to restart HA
# Core when they're ready. We deliberately do NOT auto-restart —
# that would interrupt every other integration, drop Z-Wave / Zigbee
# meshes, pause automations, and surprise the user (an add-on update
# should not bring down the whole smart home). The user restarts on
# their schedule via Settings → System → Power.
#
# The notification has a stable notification_id so re-running this
# script on subsequent boots replaces the existing notification
# rather than stacking duplicates. When the user restarts, HA clears
# all persistent_notifications, so the message naturally goes away
# at exactly the right moment.

if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    NOTIFY_MSG="SentiHome companion integration was updated. Restart Home Assistant to load it: Settings → System → Power → Restart Home Assistant. Tap-to-open-alert and FP feedback won't work until you restart."
    if curl --silent --show-error --fail \
            -X POST \
            -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"message\":\"$NOTIFY_MSG\",\"title\":\"SentiHome: restart needed\",\"notification_id\":\"sentihome_restart_required\"}" \
            http://supervisor/core/api/services/persistent_notification/create > /dev/null 2>&1; then
        echo "[integration-install] persistent notification posted — user will restart at their convenience"
    else
        echo "[integration-install] WARN: could not post persistent notification."
        echo "[integration-install] Please restart Home Assistant manually:"
        echo "[integration-install]   Settings -> System -> Power -> Restart Home Assistant"
    fi
else
    echo "[integration-install] WARN: no SUPERVISOR_TOKEN — skipping persistent notification."
    echo "[integration-install] Please restart Home Assistant manually."
fi
