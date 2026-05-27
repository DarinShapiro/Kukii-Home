"""Thin client for the Home Assistant Supervisor REST API.

The Supervisor runs at ``http://supervisor`` from inside an add-on
container, auth'd via the ``SUPERVISOR_TOKEN`` env var. We only need
one endpoint today — ``GET /addons/self/info`` returns this add-on's
own metadata including ``ingress_url``, which is what tap-actions in
push notifications and image attachments need to resolve correctly
through HA Ingress.

Keeping this in its own module (rather than folding into
:class:`HAClient`) because the Supervisor API and the HA Core API are
different services with different base URLs and different auth
semantics — even when both happen to accept the same token.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_SUPERVISOR_BASE = "http://supervisor"


async def get_addon_self_info() -> dict[str, Any] | None:
    """Fetch ``GET /addons/self/info`` and return ``data``.

    Returns None (and logs) on any failure — callers should fall back
    gracefully (e.g. notifications use relative URLs without the
    ingress prefix).
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        logger.info("supervisor.no_token", hint="not running under Supervisor; skipping")
        return None
    try:
        async with httpx.AsyncClient(
            base_url=_SUPERVISOR_BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        ) as http:
            resp = await http.get("/addons/self/info")
            if resp.status_code >= 400:
                logger.warning(
                    "supervisor.addons_self_info_failed",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                return None
            body = resp.json()
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, dict):
                return None
            return data
    except Exception as e:
        logger.warning("supervisor.addons_self_info_exception", error=str(e))
        return None


async def get_ingress_url_prefix() -> str:
    """Return the add-on's HA Ingress URL prefix, or empty string.

    Example return value: ``/api/hassio_ingress/abc123def.../``

    Used by :class:`AlertNotifier` to build tap-action + image URLs
    that route through HA Ingress (so the HA Companion app can fetch
    them with its existing auth session).

    Returns empty string when not running under Supervisor or when
    the call fails — callers should treat that as "no ingress, use
    relative URLs."
    """
    info = await get_addon_self_info()
    if not info:
        return ""
    raw = info.get("ingress_url")
    if not isinstance(raw, str) or not raw:
        return ""
    # Supervisor returns "/api/hassio_ingress/<token>/" with trailing
    # slash usually but normalise anyway so callers can append safely.
    if not raw.endswith("/"):
        raw = raw + "/"
    return raw
