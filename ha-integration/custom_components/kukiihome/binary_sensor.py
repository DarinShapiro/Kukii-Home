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
from .coordinator import Kukii-HomeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: Kukii-HomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            Kukii-HomeOnlineSensor(coordinator),
            Kukii-HomeAlertActiveSensor(coordinator),
        ]
    )


class Kukii-HomeOnlineSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_name = "Kukii-Home online"
    _attr_unique_id = "kukiihome_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class Kukii-HomeAlertActiveSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_name = "Kukii-Home alert active"
    _attr_unique_id = "kukiihome_alert_active"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data or {}
        latest = data.get("latest_alert")
        return bool(latest and not latest.get("acknowledged"))
