"""ha-agent service entry point.

Loads the topology, opens an :class:`HAClient` to Home Assistant, and
binds an aiohttp server on ``0.0.0.0:8765`` that:

* hosts the JSON API consumed by ``custom_components/sentihome/`` (see
  :mod:`http_api`), and
* renders a minimal HTML status page at ``/`` that Supervisor surfaces
  as the add-on's Web UI (declared via ``webui:`` in config.yaml).

The HTML page is intentionally small + dependency-free so it loads and
renders even when the rest of the SentiHome stack is warming up.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import structlog
from aiohttp import web
from sentihome_shared.topology import load_topology

from sentihome_ha_agent.client import HAClient, HAClientSettings
from sentihome_ha_agent.config import HAAgentSettings
from sentihome_ha_agent.http_api import AlertLog, HAAgentAPI
from sentihome_ha_agent.mcp_tools import HATools

logger = structlog.get_logger(__name__)


LISTEN_HOST = "0.0.0.0"  # noqa: S104 — listen on all interfaces inside the container
LISTEN_PORT = 8765


_STATUS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SentiHome</title>
<meta http-equiv="refresh" content="10">
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
         max-width: 720px; margin: 2rem auto; padding: 0 1rem; color: #222; }
  h1 { font-weight: 600; }
  .card { border: 1px solid #e1e4e8; border-radius: 8px; padding: 1rem 1.25rem;
          margin-bottom: 1rem; background: #fafbfc; }
  .ok { color: #28a745; font-weight: 600; }
  .bad { color: #d73a49; font-weight: 600; }
  .muted { color: #6a737d; font-size: 0.9rem; }
  table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
  th, td { text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid #eaecef; }
  th { font-weight: 600; color: #586069; font-size: 0.85rem; }
  code { background: #f6f8fa; padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.9rem; }
  .footer { color: #6a737d; font-size: 0.85rem; margin-top: 2rem; }
  a { color: #0366d6; text-decoration: none; }
  a:hover { text-decoration: underline; }
</style>
</head>
<body>
<h1>SentiHome</h1>
<p class="muted">v__VERSION__ &middot; auto-refresh every 10 s</p>

<div class="card">
<h3>Connection to Home Assistant</h3>
<p>Status: <span class="__HA_CLASS__">__HA_STATUS__</span></p>
<p class="muted">URL: <code>__HA_URL__</code></p>
<p class="muted">Entities visible: <strong>__ENTITY_COUNT__</strong></p>
</div>

<div class="card">
<h3>Recent alerts</h3>
__ALERTS_TABLE__
</div>

<div class="card">
<h3>Capabilities</h3>
<p class="muted">Domains SentiHome can act on in your HA:</p>
<p>__CAPABILITIES__</p>
</div>

<div class="footer">
<p>Next step: install the
<a href="https://github.com/DarinShapiro/SentiHome/blob/main/docs/install.md">SentiHome custom integration</a>
in HA so entities populate. Then point at least one camera adapter at a stream.</p>
<p>API: <a href="/healthz">/healthz</a> &middot; <a href="/snapshot">/snapshot</a> &middot;
<a href="/capabilities">/capabilities</a> &middot; <a href="/recent_alerts">/recent_alerts</a></p>
</div>
</body>
</html>
"""


async def _render_status(tools: HATools | None, alert_log: AlertLog, ha_url: str) -> str:
    from sentihome_ha_agent import __version__

    ha_status = "Connecting..."
    ha_class = "muted"
    entity_count: int | str = "—"
    capabilities = "—"
    if tools is not None:
        try:
            states = await tools.get_snapshot()
            entity_count = len(states)
            caps = await tools.list_capabilities()
            ha_status = "OK"
            ha_class = "ok"
            capabilities = ", ".join(f"{c.domain} ({c.entity_count})" for c in caps) or "none"
        except Exception as e:
            ha_status = f"unreachable — {e}"
            ha_class = "bad"
    else:
        ha_status = "ha_token not set — open the Configuration tab"
        ha_class = "bad"

    alerts = alert_log.recent(10)
    if alerts:
        rows = "".join(
            f"<tr><td>{a.get('alert_id', '?')}</td><td>{a.get('headline', '')}</td>"
            f"<td>{a.get('tier', '')}</td>"
            f"<td>{'ack' if a.get('acknowledged') else 'open'}</td></tr>"
            for a in reversed(alerts)
        )
        alerts_table = (
            "<table><tr><th>ID</th><th>Headline</th><th>Tier</th>"
            f"<th>Status</th></tr>{rows}</table>"
        )
    else:
        alerts_table = (
            '<p class="muted">No alerts yet. Trigger a detection from one of '
            "your cameras to see one appear here.</p>"
        )

    return (
        _STATUS_PAGE.replace("__VERSION__", __version__)
        .replace("__HA_CLASS__", ha_class)
        .replace("__HA_STATUS__", ha_status)
        .replace("__HA_URL__", ha_url)
        .replace("__ENTITY_COUNT__", str(entity_count))
        .replace("__ALERTS_TABLE__", alerts_table)
        .replace("__CAPABILITIES__", capabilities)
    )


def _build_app(*, tools: HATools | None, alert_log: AlertLog, ha_url: str) -> web.Application:
    api = HAAgentAPI(tools=tools, alert_log=alert_log)

    async def status_page(_request: web.Request) -> web.Response:
        body = await _render_status(tools, alert_log, ha_url)
        return web.Response(text=body, content_type="text/html")

    async def api_get(request: web.Request) -> web.Response:
        body: dict = dict(request.rel_url.query)
        status, payload = await api.dispatch(method="GET", path=request.path, body=body)
        return web.json_response(payload, status=status)

    async def api_post(request: web.Request) -> web.Response:
        try:
            body = await request.json() if request.body_exists else {}
        except json.JSONDecodeError:
            body = {}
        status, payload = await api.dispatch(method="POST", path=request.path, body=body)
        return web.json_response(payload, status=status)

    app = web.Application()
    app.router.add_get("/", status_page)
    for path in ("/healthz", "/snapshot", "/capabilities", "/recent_alerts"):
        app.router.add_get(path, api_get)
    for path in ("/service", "/acknowledge_alert"):
        app.router.add_post(path, api_post)
    return app


async def _run() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    topology = load_topology()
    alert_log = AlertLog()

    client: HAClient | None = None
    tools: HATools | None = None
    ha_url = topology.ha_agent.ha_url
    try:
        settings = HAAgentSettings.from_topology(topology)
        client = HAClient(
            HAClientSettings(
                ha_url=settings.ha_url,
                ha_token=settings.ha_token,
                websocket=settings.websocket,
            )
        )
        await client.start()
        tools = HATools(client)
        logger.info("ha_agent.connected", ha_url=ha_url)
    except Exception as e:
        logger.warning(
            "ha_agent.no_ha_connection",
            error=str(e),
            hint="set ha_token in add-on options, or wait for SUPERVISOR_TOKEN bootstrap",
        )

    app = _build_app(tools=tools, alert_log=alert_log, ha_url=ha_url)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
    await site.start()
    logger.info("ha_agent.listening", host=LISTEN_HOST, port=LISTEN_PORT)

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        if client is not None:
            await client.stop()


def main() -> None:
    """Service entry point."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
