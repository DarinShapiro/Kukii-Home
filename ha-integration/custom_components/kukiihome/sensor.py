"""Sensors — latest alert headline, recent count, capability totals."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KukiiHomeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: KukiiHomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            LatestAlertSensor(coordinator),
            RecentAlertCountSensor(coordinator),
            CapabilityCountSensor(coordinator),
            SystemHealthSensor(coordinator),
        ]
    )


class LatestAlertSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "Kukii-Home latest alert"
    _attr_unique_id = "kukiihome_latest_alert"
    _attr_icon = "mdi:alert"

    @property
    def native_value(self) -> str | None:
        latest = (self.coordinator.data or {}).get("latest_alert")
        if not latest:
            return None
        return latest.get("headline") or latest.get("alert_id")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        latest = (self.coordinator.data or {}).get("latest_alert") or {}
        return {
            "alert_id": latest.get("alert_id"),
            "tier": latest.get("tier"),
            "confidence": latest.get("confidence"),
            "rules_fired": latest.get("rules_fired") or [],
            "evidence_ref": latest.get("evidence_ref"),
        }


class RecentAlertCountSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "Kukii-Home recent alerts"
    _attr_unique_id = "kukiihome_recent_alert_count"
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "alerts"

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("alerts") or [])


class CapabilityCountSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "Kukii-Home HA capabilities"
    _attr_unique_id = "kukiihome_capability_count"
    _attr_icon = "mdi:home-assistant"

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("capabilities") or [])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        caps = (self.coordinator.data or {}).get("capabilities") or []
        return {"domains": [c.get("domain") for c in caps]}


class SystemHealthSensor(CoordinatorEntity, SensorEntity):
    """Resilience system health (Epic 15) — overall + per-component.

    State is the §19 rollup: ``healthy`` / ``degraded`` / ``critical``
    (or ``unknown`` when the add-on hasn't reported /health yet). The
    enumerated device class gives HA a clean state set. Attributes carry
    the per-component breakdown + the degraded count, so a dashboard or
    automation can see *what* is wrong (e.g. ``home_assistant`` offline)
    without scraping logs."""

    _attr_name = "Kukii-Home system health"
    _attr_unique_id = "kukiihome_system_health"
    _attr_icon = "mdi:heart-pulse"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = ["healthy", "degraded", "critical", "unknown"]

    @property
    def native_value(self) -> str:
        health = (self.coordinator.data or {}).get("health") or {}
        return health.get("overall") or "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        health = (self.coordinator.data or {}).get("health") or {}
        components = health.get("components") or []
        return {
            "components": [
                {
                    "component": c.get("component"),
                    "status": c.get("status"),
                    "detail": c.get("detail"),
                    "critical": c.get("critical"),
                }
                for c in components
            ],
            "degraded_count": sum(1 for c in components if c.get("status") != "ok"),
        }
