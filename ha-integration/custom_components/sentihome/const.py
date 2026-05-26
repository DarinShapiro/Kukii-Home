"""Constants for the SentiHome HA custom integration."""

from __future__ import annotations

DOMAIN = "sentihome"
DEFAULT_HOST = "homeassistant.local"
DEFAULT_PORT = 8765
DEFAULT_POLL_SECONDS = 10

CONF_HOST = "host"
CONF_PORT = "port"
CONF_POLL_SECONDS = "poll_seconds"

EVENT_SENTIHOME_ALERT = "sentihome_alert"
EVENT_SENTIHOME_FEEDBACK_COMPLETE = "sentihome_feedback_complete"
EVENT_SENTIHOME_ANOMALY_DETECTED = "sentihome_anomaly_detected"

SERVICE_ACKNOWLEDGE_ALERT = "acknowledge_alert"
SERVICE_RUN_OPTIMIZATION = "run_optimization"
SERVICE_LABEL_PERSON = "label_person"
