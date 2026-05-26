"""Sensors — latest alert headline, recent count, capability totals."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SentiHomeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SentiHomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            LatestAlertSensor(coordinator),
            RecentAlertCountSensor(coordinator),
            CapabilityCountSensor(coordinator),
        ]
    )


class LatestAlertSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "SentiHome latest alert"
    _attr_unique_id = "sentihome_latest_alert"
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
    _attr_name = "SentiHome recent alerts"
    _attr_unique_id = "sentihome_recent_alert_count"
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "alerts"

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("alerts") or [])


class CapabilityCountSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "SentiHome HA capabilities"
    _attr_unique_id = "sentihome_capability_count"
    _attr_icon = "mdi:home-assistant"

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("capabilities") or [])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        caps = (self.coordinator.data or {}).get("capabilities") or []
        return {"domains": [c.get("domain") for c in caps]}
