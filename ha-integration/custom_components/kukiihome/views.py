"""HTTP views the Kukii-Home integration registers in HA Core.

Epic 10.8.3: solves the v0.3.15/17/20 401-on-tap problem.

The add-on's per-alert page (Epic 10.8.1) lives behind HA Ingress
at /api/hassio_ingress/<token>/alert/<id>. That token is bound to
the BROWSER ingress session — the HA Companion app doesn't carry
it, so a notification tap fetched from the app gets 401.

Fix: register HomeAssistantViews at /api/kukiihome/alert/<id>/*
in HA Core. HA's standard auth middleware handles bearer-token
auth for /api/* paths, which the Companion app DOES carry. Each
view proxies the request through to the add-on's existing endpoint
via the Kukii-HomeAPIClient. The browser never talks to the add-on
directly anymore (for the per-alert flow); it talks to HA Core,
which forwards to the add-on internally.

URL surface registered:

  GET  /api/kukiihome/alert/<event_id>           → HTML page
  GET  /api/kukiihome/alert/<event_id>/frame.jpg
  GET  /api/kukiihome/alert/<event_id>/annotated.jpg
  POST /api/kukiihome/alert/<event_id>/dismiss
  POST /api/kukiihome/alert/<event_id>/feedback

The HTML's relative URLs (``<id>/frame.jpg``, ``<id>/dismiss``,
etc.) resolve correctly under both the add-on's path and this
proxy path because RFC 3986 strips the page URL's last segment
before merging — so ``<id>/frame.jpg`` from
``/api/kukiihome/alert/<id>`` resolves to
``/api/kukiihome/alert/<id>/frame.jpg``. No HTML rewriting needed.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

# Epic 10.8.5: ``async_sign_path`` moved between HA versions.
# Modern HA (2024+): homeassistant.components.http.auth
# Legacy HA (pre-2024): homeassistant.helpers.network
# Try modern first, fall back to legacy. Logs the chosen import so a
# future "where did this come from" debug session has a breadcrumb.
try:
    from homeassistant.components.http.auth import (  # type: ignore[import-not-found]
        async_sign_path,
    )
except ImportError:
    from homeassistant.helpers.network import (  # type: ignore[import-not-found,no-redef]
        async_sign_path,
    )

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .api_client import Kukii-HomeAPIClient

_LOGGER = logging.getLogger(__name__)


# How long a signed alert URL stays tap-able after the notification
# fires. Long enough for "I'll check when I get home" but not
# permanent — if a phone is compromised days later, old notification
# URLs eventually expire.
_SIGN_EXPIRATION = timedelta(hours=24)


def register_alert_views(hass: HomeAssistant, client: Kukii-HomeAPIClient) -> None:
    """Register all per-alert HTTP views on HA's HTTP component.

    Idempotent enough for the integration's setup_entry flow — HA
    raises on duplicate registration, which we let propagate (a
    duplicate registration is a real bug, not something to swallow).
    """
    hass.http.register_view(SignURLView(hass))
    hass.http.register_view(AlertPageView(client))
    hass.http.register_view(AlertFrameView(client))
    hass.http.register_view(AlertAnnotatedView(client))
    hass.http.register_view(AlertDismissView(client))
    hass.http.register_view(AlertFeedbackView(client))


# ─── URL signing helper ─────────────────────────────────────────────


class SignURLView(HomeAssistantView):
    """Epic 10.8.5: returns a signed version of an /api/kukiihome/...
    path so notification taps work in the HA Companion app's webview.

    Problem: the Companion app's notification tap loads URLs in an
    in-app webview using SESSION COOKIES from the user's HA login.
    That cookie is bound to the browser session, not the mobile-app
    session, so /api/kukiihome/* requests fail auth (401 with
    "Login attempt failed").

    Fix: HA's signed-path mechanism. ``async_sign_path`` returns a
    URL with a ``?authSig=<jwt>`` query token that HA's auth
    middleware accepts in place of cookie/bearer auth. Same pattern
    /api/camera_proxy/ uses for its notification image attachments.

    Caller (the add-on's notifier) hits this view with its
    Supervisor token to obtain a signed URL, then embeds the result
    in the notification's ``data.url``. The mobile app fetches the
    signed URL; HA validates the JWT; no cookie needed.

    URL: GET /api/kukiihome/sign?path=/api/kukiihome/alert/<id>
    Returns: {"signed_url": "/api/kukiihome/alert/<id>?authSig=<jwt>"}
    """

    url = "/api/kukiihome/sign"
    name = "api:kukiihome:sign"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        path = request.query.get("path", "")
        # Sanity: only sign /api/kukiihome/* paths. Don't become a
        # general-purpose signer for arbitrary HA routes — that
        # would be a credential-elevation primitive.
        if not path.startswith("/api/kukiihome/"):
            return web.json_response(
                {"error": "path must start with /api/kukiihome/"}, status=400
            )
        try:
            signed = async_sign_path(
                self._hass, path, expiration=_SIGN_EXPIRATION
            )
        except Exception as e:
            _LOGGER.warning("kukiihome.sign_url_failed path=%s error=%s", path, e)
            return web.json_response({"error": str(e)}, status=500)
        return web.json_response({"signed_url": signed})


# ─── HTML page ──────────────────────────────────────────────────────


class AlertPageView(HomeAssistantView):
    """GET /api/kukiihome/alert/<event_id> → HTML."""

    url = "/api/kukiihome/alert/{event_id}"
    name = "api:kukiihome:alert"
    # requires_auth defaults to True — HA's auth middleware verifies
    # the bearer token before this handler runs. Companion app has
    # the token; browser sessions have the cookie. Both work.

    def __init__(self, client: Kukii-HomeAPIClient) -> None:
        self._client = client

    async def get(self, request: web.Request, event_id: str) -> web.Response:
        status, body, content_type = await self._client.alert_page_html(event_id)
        return web.Response(body=body, status=status, content_type=content_type)


# ─── Frames ─────────────────────────────────────────────────────────


class AlertFrameView(HomeAssistantView):
    url = "/api/kukiihome/alert/{event_id}/frame.jpg"
    name = "api:kukiihome:alert:frame"

    def __init__(self, client: Kukii-HomeAPIClient) -> None:
        self._client = client

    async def get(self, request: web.Request, event_id: str) -> web.Response:
        status, body, content_type = await self._client.alert_frame(event_id)
        return web.Response(body=body, status=status, content_type=content_type)


class AlertAnnotatedView(HomeAssistantView):
    url = "/api/kukiihome/alert/{event_id}/annotated.jpg"
    name = "api:kukiihome:alert:annotated"

    def __init__(self, client: Kukii-HomeAPIClient) -> None:
        self._client = client

    async def get(self, request: web.Request, event_id: str) -> web.Response:
        status, body, content_type = await self._client.alert_frame(
            event_id, annotated=True
        )
        return web.Response(body=body, status=status, content_type=content_type)


# ─── Action endpoints ───────────────────────────────────────────────


class AlertDismissView(HomeAssistantView):
    """POST /api/kukiihome/alert/<event_id>/dismiss.

    The add-on returns 303 with a relative Location (``../<id>?dismissed=1``)
    that resolves correctly under either URL scheme — no rewriting
    needed. The view forwards the redirect verbatim.
    """

    url = "/api/kukiihome/alert/{event_id}/dismiss"
    name = "api:kukiihome:alert:dismiss"

    def __init__(self, client: Kukii-HomeAPIClient) -> None:
        self._client = client

    async def post(self, request: web.Request, event_id: str) -> web.Response:
        status, location = await self._client.alert_dismiss(event_id)
        if status == 303 and location:
            raise web.HTTPSeeOther(location=location)
        return web.Response(status=status)


class AlertFeedbackView(HomeAssistantView):
    """POST /api/kukiihome/alert/<event_id>/feedback.

    Forwards the form body to the add-on. Same 303-redirect
    semantics as dismiss.
    """

    url = "/api/kukiihome/alert/{event_id}/feedback"
    name = "api:kukiihome:alert:feedback"

    def __init__(self, client: Kukii-HomeAPIClient) -> None:
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
