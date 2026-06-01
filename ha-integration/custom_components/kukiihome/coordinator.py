"""DataUpdateCoordinator — polls the ha-agent for recent alerts + capabilities."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import KukiiHomeAPIClient
from .const import (
    EVENT_KUKIIHOME_ALERT,
    EVENT_KUKIIHOME_FEEDBACK_COMPLETE,
)

_LOGGER = logging.getLogger(__name__)


class KukiiHomeCoordinator(DataUpdateCoordinator):
    """Polls the ha-agent + fires HA events for new alerts."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        client: KukiiHomeAPIClient,
        poll_seconds: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="kukiihome",
            update_interval=timedelta(seconds=poll_seconds),
        )
        self._client = client
        self._seen_alert_ids: set[str] = set()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            alerts = await self._client.recent_alerts(limit=50)
            caps = await self._client.capabilities()
        except Exception as e:
            raise UpdateFailed(f"ha-agent unreachable: {e}") from e

        # Fire events for newly-seen alerts.
        for alert in alerts:
            alert_id = alert.get("alert_id")
            if not alert_id or alert_id in self._seen_alert_ids:
                continue
            self._seen_alert_ids.add(alert_id)
            self.hass.bus.async_fire(EVENT_KUKIIHOME_ALERT, alert)
            if alert.get("acknowledged"):
                self.hass.bus.async_fire(EVENT_KUKIIHOME_FEEDBACK_COMPLETE, alert)

        return {
            "alerts": alerts,
            "capabilities": caps,
            "latest_alert": alerts[-1] if alerts else None,
        }

    @property
    def client(self) -> KukiiHomeAPIClient:
        return self._client
