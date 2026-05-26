"""Binary sensors — alert active / system online."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
            SentiHomeOnlineSensor(coordinator),
            SentiHomeAlertActiveSensor(coordinator),
        ]
    )


class SentiHomeOnlineSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_name = "SentiHome online"
    _attr_unique_id = "sentihome_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class SentiHomeAlertActiveSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_name = "SentiHome alert active"
    _attr_unique_id = "sentihome_alert_active"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data or {}
        latest = data.get("latest_alert")
        return bool(latest and not latest.get("acknowledged"))
