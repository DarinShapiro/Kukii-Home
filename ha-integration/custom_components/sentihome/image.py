"""Image entity — latest alert frame.

v1 surfaces the URI from the alert payload as an attribute; full image
streaming through HA's image platform lands when the object store grows
an HTTP read endpoint.
"""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SentiHomeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SentiHomeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LatestAlertImage(hass, coordinator)])


class LatestAlertImage(CoordinatorEntity, ImageEntity):
    _attr_name = "SentiHome latest alert frame"
    _attr_unique_id = "sentihome_latest_alert_image"
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, coordinator: SentiHomeCoordinator) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)

    @property
    def image_url(self) -> str | None:
        latest = (self.coordinator.data or {}).get("latest_alert") or {}
        return latest.get("evidence_ref")

    @property
    def image_last_updated(self):
        return dt_util.utcnow()
