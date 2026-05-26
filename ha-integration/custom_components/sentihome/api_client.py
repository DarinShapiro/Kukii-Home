"""Thin async HTTP client the integration uses to talk to the ha-agent.

Runs inside HA Core's restricted Python sandbox; only aiohttp + stdlib.
"""

from __future__ import annotations

from typing import Any

import aiohttp


class SentiHomeAPIError(Exception):
    """Raised on transport / 5xx failures from the ha-agent."""


class SentiHomeAPIClient:
    """Minimal client over the ha-agent HTTP API (see services/ha-agent/http_api.py)."""

    def __init__(self, *, host: str, port: int, session: aiohttp.ClientSession) -> None:
        self._base = f"http://{host}:{port}"
        self._session = session

    async def healthz(self) -> bool:
        try:
            async with self._session.get(f"{self._base}/healthz", timeout=5) as r:
                return r.status == 200
        except aiohttp.ClientError:
            return False

    async def snapshot(self) -> dict[str, Any]:
        async with self._session.get(f"{self._base}/snapshot", timeout=10) as r:
            r.raise_for_status()
            return await r.json()

    async def capabilities(self) -> list[dict[str, Any]]:
        async with self._session.get(f"{self._base}/capabilities", timeout=10) as r:
            r.raise_for_status()
            body = await r.json()
            return body.get("capabilities", [])

    async def recent_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self._session.get(
            f"{self._base}/recent_alerts", params={"limit": limit}, timeout=10
        ) as r:
            r.raise_for_status()
            body = await r.json()
            return body.get("alerts", [])

    async def acknowledge_alert(self, alert_id: str, *, feedback: str = "correct") -> None:
        async with self._session.post(
            f"{self._base}/acknowledge_alert",
            json={"alert_id": alert_id, "feedback": feedback},
            timeout=10,
        ) as r:
            r.raise_for_status()
