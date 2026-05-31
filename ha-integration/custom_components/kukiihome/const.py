"""Constants for the Kukii-Home HA custom integration."""

from __future__ import annotations

DOMAIN = "kukiihome"
DEFAULT_HOST = "homeassistant.local"
DEFAULT_PORT = 8765
DEFAULT_POLL_SECONDS = 10

CONF_HOST = "host"
CONF_PORT = "port"
CONF_POLL_SECONDS = "poll_seconds"

EVENT_KUKIIHOME_ALERT = "kukiihome_alert"
EVENT_KUKIIHOME_FEEDBACK_COMPLETE = "kukiihome_feedback_complete"
EVENT_KUKIIHOME_ANOMALY_DETECTED = "kukiihome_anomaly_detected"

SERVICE_ACKNOWLEDGE_ALERT = "acknowledge_alert"
SERVICE_RUN_OPTIMIZATION = "run_optimization"
SERVICE_LABEL_PERSON = "label_person"
