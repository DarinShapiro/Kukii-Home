"""Config flow — UI walks the user through pointing HA at the SentiHome add-on."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import SentiHomeAPIClient
from .const import (
    CONF_HOST,
    CONF_POLL_SECONDS,
    CONF_PORT,
    DEFAULT_HOST,
    DEFAULT_POLL_SECONDS,
    DEFAULT_PORT,
    DOMAIN,
)


class SentiHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """SentiHome config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            session = async_get_clientsession(self.hass)
            client = SentiHomeAPIClient(host=host, port=port, session=session)
            if not await client.healthz():
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"SentiHome ({host}:{port})", data=user_input
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_POLL_SECONDS, default=DEFAULT_POLL_SECONDS): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
