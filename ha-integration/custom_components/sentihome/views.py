"""HTTP views the SentiHome integration registers in HA Core.

Epic 10.8.3: solves the v0.3.15/17/20 401-on-tap problem.

The add-on's per-alert page (Epic 10.8.1) lives behind HA Ingress
at /api/hassio_ingress/<token>/alert/<id>. That token is bound to
the BROWSER ingress session — the HA Companion app doesn't carry
it, so a notification tap fetched from the app gets 401.

Fix: register HomeAssistantViews at /api/sentihome/alert/<id>/*
in HA Core. HA's standard auth middleware handles bearer-token
auth for /api/* paths, which the Companion app DOES carry. Each
view proxies the request through to the add-on's existing endpoint
via the SentiHomeAPIClient. The browser never talks to the add-on
directly anymore (for the per-alert flow); it talks to HA Core,
which forwards to the add-on internally.

URL surface registered:

  GET  /api/sentihome/alert/<event_id>           → HTML page
  GET  /api/sentihome/alert/<event_id>/frame.jpg
  GET  /api/sentihome/alert/<event_id>/annotated.jpg
  POST /api/sentihome/alert/<event_id>/dismiss
  POST /api/sentihome/alert/<event_id>/feedback

The HTML's relative URLs (``<id>/frame.jpg``, ``<id>/dismiss``,
etc.) resolve correctly under both the add-on's path and this
proxy path because RFC 3986 strips the page URL's last segment
before merging — so ``<id>/frame.jpg`` from
``/api/sentihome/alert/<id>`` resolves to
``/api/sentihome/alert/<id>/frame.jpg``. No HTML rewriting needed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

if TYPE_CHECKING:
    from .api_client import SentiHomeAPIClient

_LOGGER = logging.getLogger(__name__)


def register_alert_views(hass, client: SentiHomeAPIClient) -> None:
    """Register all per-alert HTTP views on HA's HTTP component.

    Idempotent enough for the integration's setup_entry flow — HA
    raises on duplicate registration, which we let propagate (a
    duplicate registration is a real bug, not something to swallow).
    """
    hass.http.register_view(AlertPageView(client))
    hass.http.register_view(AlertFrameView(client))
    hass.http.register_view(AlertAnnotatedView(client))
    hass.http.register_view(AlertDismissView(client))
    hass.http.register_view(AlertFeedbackView(client))


# ─── HTML page ──────────────────────────────────────────────────────


class AlertPageView(HomeAssistantView):
    """GET /api/sentihome/alert/<event_id> → HTML."""

    url = "/api/sentihome/alert/{event_id}"
    name = "api:sentihome:alert"
    # requires_auth defaults to True — HA's auth middleware verifies
    # the bearer token before this handler runs. Companion app has
    # the token; browser sessions have the cookie. Both work.

    def __init__(self, client: SentiHomeAPIClient) -> None:
        self._client = client

    async def get(self, request: web.Request, event_id: str) -> web.Response:
        status, body, content_type = await self._client.alert_page_html(event_id)
        return web.Response(body=body, status=status, content_type=content_type)


# ─── Frames ─────────────────────────────────────────────────────────


class AlertFrameView(HomeAssistantView):
    url = "/api/sentihome/alert/{event_id}/frame.jpg"
    name = "api:sentihome:alert:frame"

    def __init__(self, client: SentiHomeAPIClient) -> None:
        self._client = client

    async def get(self, request: web.Request, event_id: str) -> web.Response:
        status, body, content_type = await self._client.alert_frame(event_id)
        return web.Response(body=body, status=status, content_type=content_type)


class AlertAnnotatedView(HomeAssistantView):
    url = "/api/sentihome/alert/{event_id}/annotated.jpg"
    name = "api:sentihome:alert:annotated"

    def __init__(self, client: SentiHomeAPIClient) -> None:
        self._client = client

    async def get(self, request: web.Request, event_id: str) -> web.Response:
        status, body, content_type = await self._client.alert_frame(
            event_id, annotated=True
        )
        return web.Response(body=body, status=status, content_type=content_type)


# ─── Action endpoints ───────────────────────────────────────────────


class AlertDismissView(HomeAssistantView):
    """POST /api/sentihome/alert/<event_id>/dismiss.

    The add-on returns 303 with a relative Location (``../<id>?dismissed=1``)
    that resolves correctly under either URL scheme — no rewriting
    needed. The view forwards the redirect verbatim.
    """

    url = "/api/sentihome/alert/{event_id}/dismiss"
    name = "api:sentihome:alert:dismiss"

    def __init__(self, client: SentiHomeAPIClient) -> None:
        self._client = client

    async def post(self, request: web.Request, event_id: str) -> web.Response:
        status, location = await self._client.alert_dismiss(event_id)
        if status == 303 and location:
            raise web.HTTPSeeOther(location=location)
        return web.Response(status=status)


class AlertFeedbackView(HomeAssistantView):
    """POST /api/sentihome/alert/<event_id>/feedback.

    Forwards the form body to the add-on. Same 303-redirect
    semantics as dismiss.
    """

    url = "/api/sentihome/alert/{event_id}/feedback"
    name = "api:sentihome:alert:feedback"

    def __init__(self, client: SentiHomeAPIClient) -> None:
        self._client = client

    async def post(self, request: web.Request, event_id: str) -> web.Response:
        form = await request.post()
        # multidict → plain dict[str, str]; the add-on expects simple
        # form fields, no multi-value or file uploads.
        form_dict = {k: str(v) for k, v in form.items()}
        status, location = await self._client.alert_feedback(event_id, form_dict)
        if status == 303 and location:
            raise web.HTTPSeeOther(location=location)
        return web.Response(status=status)
