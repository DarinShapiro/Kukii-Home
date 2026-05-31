"""Config flow — UI walks the user through pointing HA at the Kukii-Home add-on.

Two entry points:

* User-initiated (``async_step_user``): the classic "Settings → Add
  integration → Kukii-Home" path. Manual host/port entry.
* Zeroconf-discovered (``async_step_zeroconf``): the add-on
  broadcasts ``_kukiihome._tcp.local.`` on the LAN; HA picks it up
  automatically; the user sees a "Discovered: Kukii-Home" card and
  just clicks Configure. Epic 10.8.4.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import Kukii-HomeAPIClient
from .const import (
    CONF_HOST,
    CONF_POLL_SECONDS,
    CONF_PORT,
    DEFAULT_HOST,
    DEFAULT_POLL_SECONDS,
    DEFAULT_PORT,
    DOMAIN,
)


class Kukii-HomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Kukii-Home config flow."""

    VERSION = 1

    def __init__(self) -> None:
        # When async_step_zeroconf fires, we stash the discovered
        # host + port here and forward to async_step_confirm — the
        # user sees a confirmation card with the pre-filled values
        # rather than an empty form.
        self._discovered_host: str | None = None
        self._discovered_port: int | None = None

    # ─── user-initiated flow (manual) ───────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            session = async_get_clientsession(self.hass)
            client = Kukii-HomeAPIClient(host=host, port=port, session=session)
            if not await client.healthz():
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Kukii-Home ({host}:{port})", data=user_input
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_POLL_SECONDS, default=DEFAULT_POLL_SECONDS): int,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    # ─── zeroconf flow (auto-discovery) — Epic 10.8.4 ───────────────

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
        """Triggered when HA's zeroconf component sees the add-on's
        ``_kukiihome._tcp.local.`` broadcast.

        The add-on (services/ha-agent/discovery_publish.py) puts the
        host + port in the TXT properties. We pull them out, sanity-
        check by hitting /healthz, then show a one-button "Configure"
        confirmation to the user.
        """
        # discovery_info.properties is a dict[str, str|None] (HA
        # normalizes the bytes-keyed TXT records).
        props = discovery_info.properties or {}
        host = props.get("host") or discovery_info.hostname.rstrip(".")
        port_str = props.get("port") or str(discovery_info.port or DEFAULT_PORT)
        try:
            port = int(port_str)
        except (TypeError, ValueError):
            return self.async_abort(reason="invalid_discovery_info")

        # Dedupe — if the user already has an entry for this host:port
        # combo, don't prompt them again. The published unique_id
        # matches what async_step_user uses, so the two flows can't
        # produce duplicate entries.
        await self.async_set_unique_id(f"{host}:{port}")
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: host, CONF_PORT: port}
        )

        # Sanity check before showing the confirm card. If the add-on
        # isn't actually reachable at the advertised host/port (e.g.
        # weird container networking), don't promise the user
        # something we can't deliver.
        session = async_get_clientsession(self.hass)
        client = Kukii-HomeAPIClient(host=host, port=port, session=session)
        if not await client.healthz():
            return self.async_abort(reason="cannot_connect")

        self._discovered_host = host
        self._discovered_port = port
        # Surfaces in the "Discovered" card as the integration name.
        self.context["title_placeholders"] = {"name": f"{host}:{port}"}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """The user clicks Configure on the Discovered card. We
        already validated reachability in async_step_zeroconf, so
        this is a one-click confirm — no form."""
        assert self._discovered_host is not None
        assert self._discovered_port is not None

        if user_input is not None:
            return self.async_create_entry(
                title=f"Kukii-Home ({self._discovered_host}:{self._discovered_port})",
                data={
                    CONF_HOST: self._discovered_host,
                    CONF_PORT: self._discovered_port,
                    CONF_POLL_SECONDS: DEFAULT_POLL_SECONDS,
                },
            )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "host": self._discovered_host,
                "port": str(self._discovered_port),
            },
        )
