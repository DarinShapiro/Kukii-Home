"""Kukii-Home custom integration for Home Assistant.

Bridges the Kukii-Home add-on (services/ha-agent HTTP API) into HA entities,
services, and events. Architecture: docs/architecture/07-tool-layer-mcp.md.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import Kukii-HomeAPIClient
from .const import (
    CONF_HOST,
    CONF_POLL_SECONDS,
    CONF_PORT,
    DEFAULT_POLL_SECONDS,
    DOMAIN,
    SERVICE_ACKNOWLEDGE_ALERT,
    SERVICE_LABEL_PERSON,
    SERVICE_RUN_OPTIMIZATION,
)
from .coordinator import Kukii-HomeCoordinator
from .views import register_alert_views

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.IMAGE,
    Platform.BUTTON,
    Platform.NUMBER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kukii-Home from a config entry."""
    session = async_get_clientsession(hass)
    client = Kukii-HomeAPIClient(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        session=session,
    )
    coordinator = Kukii-HomeCoordinator(
        hass,
        client=client,
        poll_seconds=entry.data.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Epic 10.8.3: register the per-alert page views so the
    # notification tap UX works through Companion app's bearer auth.
    # Idempotency: re-registering on subsequent entry setups would
    # raise; we only register on the first entry (a household will
    # typically have one Kukii-Home integration entry).
    if not hass.data[DOMAIN].get("_views_registered"):
        register_alert_views(hass, client)
        hass.data[DOMAIN]["_views_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Services
    async def acknowledge(call: ServiceCall) -> None:
        await client.acknowledge_alert(
            call.data["alert_id"], feedback=call.data.get("feedback", "correct")
        )
        await coordinator.async_request_refresh()

    async def run_optimization(_call: ServiceCall) -> None:
        _LOGGER.info("kukiihome.run_optimization: requested (wired in Epic 11)")

    async def label_person(_call: ServiceCall) -> None:
        _LOGGER.info("kukiihome.label_person: requested (wired in Epic 10)")

    hass.services.async_register(DOMAIN, SERVICE_ACKNOWLEDGE_ALERT, acknowledge)
    hass.services.async_register(DOMAIN, SERVICE_RUN_OPTIMIZATION, run_optimization)
    hass.services.async_register(DOMAIN, SERVICE_LABEL_PERSON, label_person)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
