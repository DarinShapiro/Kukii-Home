"""Thin async HTTP client the integration uses to talk to the ha-agent.

Runs inside HA Core's restricted Python sandbox; only aiohttp + stdlib.
"""

from __future__ import annotations

from typing import Any

import aiohttp


class KukiiHomeAPIError(Exception):
    """Raised on transport / 5xx failures from the ha-agent."""


class KukiiHomeAPIClient:
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

    # ─── Epic 10.8.3: per-alert page proxy ──────────────────────────
    #
    # These methods fetch from the add-on's /alert/<id>/* endpoints
    # so the integration's HomeAssistantView can re-serve the same
    # content under /api/kukiihome/alert/<id>/* where HA's bearer-
    # token auth applies (the path Companion app can authenticate
    # against). Each returns (status, body, content_type) so the view
    # can faithfully reproduce 200 / 404 / 303 etc.

    async def alert_page_html(self, event_id: str) -> tuple[int, bytes, str]:
        """GET /alert/<id> on the add-on. Returns (status, body, ct).

        The HTML uses relative URLs like ``<id>/frame.jpg`` so it
        renders correctly served from either the add-on directly or
        the integration's proxy path."""
        async with self._session.get(
            f"{self._base}/alert/{event_id}", timeout=10
        ) as r:
            body = await r.read()
            return (
                r.status,
                body,
                r.headers.get("Content-Type", "text/html"),
            )

    async def alert_frame(
        self, event_id: str, *, annotated: bool = False
    ) -> tuple[int, bytes, str]:
        suffix = "annotated.jpg" if annotated else "frame.jpg"
        async with self._session.get(
            f"{self._base}/alert/{event_id}/{suffix}", timeout=10
        ) as r:
            body = await r.read()
            return (
                r.status,
                body,
                r.headers.get("Content-Type", "image/jpeg"),
            )

    async def alert_dismiss(self, event_id: str) -> tuple[int, str | None]:
        """POST /alert/<id>/dismiss. Returns (status, location). The
        add-on returns 303 with a relative Location; the caller
        translates that into the integration's URL space."""
        async with self._session.post(
            f"{self._base}/alert/{event_id}/dismiss",
            allow_redirects=False,
            timeout=10,
        ) as r:
            return r.status, r.headers.get("Location")

    async def alert_feedback(
        self, event_id: str, form: dict[str, str]
    ) -> tuple[int, str | None]:
        async with self._session.post(
            f"{self._base}/alert/{event_id}/feedback",
            data=form,
            allow_redirects=False,
            timeout=10,
        ) as r:
            return r.status, r.headers.get("Location")
