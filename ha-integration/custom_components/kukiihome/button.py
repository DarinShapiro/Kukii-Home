"""Button entities — run optimization, retrain identity (stubs for now)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
            RunOptimizationButton(coordinator),
            RetrainIdentityButton(coordinator),
        ]
    )


class RunOptimizationButton(CoordinatorEntity, ButtonEntity):
    _attr_name = "Kukii-Home run optimization"
    _attr_unique_id = "kukiihome_run_optimization"
    _attr_icon = "mdi:tune"

    async def async_press(self) -> None:
        # Wired to a real ha-agent endpoint in Epic 11.
        await self.hass.services.async_call("kukiihome", "run_optimization", {})


class RetrainIdentityButton(CoordinatorEntity, ButtonEntity):
    _attr_name = "Kukii-Home retrain identity"
    _attr_unique_id = "kukiihome_retrain_identity"
    _attr_icon = "mdi:account-arrow-up"

    async def async_press(self) -> None:
        await self.hass.services.async_call("kukiihome", "label_person", {})
