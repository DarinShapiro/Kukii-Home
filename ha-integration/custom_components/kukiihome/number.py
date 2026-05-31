"""Tunable thresholds exposed as HA number entities.

These are stubs in v1 — wiring them back to live Kukii-Home config is the
job of Epic 11 (optimization loop). For now they surface, accept changes,
and emit log lines so dashboards can be built against them.
"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Kukii-HomeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: Kukii-HomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ConfidenceThresholdNumber(coordinator)])


class ConfidenceThresholdNumber(CoordinatorEntity, NumberEntity):
    _attr_name = "Kukii-Home global confidence threshold"
    _attr_unique_id = "kukiihome_confidence_threshold"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 1.0
    _attr_native_step = 0.05
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:gauge"

    def __init__(self, coordinator: Kukii-HomeCoordinator) -> None:
        super().__init__(coordinator)
        self._value = 0.7

    @property
    def native_value(self) -> float:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        _LOGGER.info("kukiihome: confidence threshold set to %.2f", value)
