"""Zeroconf / mDNS publisher so HA auto-discovers the SentiHome integration.

Epic 10.8.4: removes the "type the host + port into the integration
config flow" step. The add-on publishes a ``_sentihome._tcp.local.``
service on the local network; HA's zeroconf component sees it; the
SentiHome custom integration's manifest declares it picks up that
service; HA shows "Discovered: SentiHome" in Devices & Services with
a single Configure button.

The service payload carries:

* ``host`` + ``port`` — where ha-agent's HTTP API listens. The
  integration uses these to construct its SentiHomeAPIClient base URL.
* ``version`` — add-on version string, for debugging skew between
  the bundled integration files and the running add-on (they should
  always be in lockstep after Epic 10.8.4 but worth surfacing).

The publisher runs as a fire-and-forget background task — it
registers the service at boot and stays registered until the add-on
shuts down. mDNS broadcasts are repeated automatically by the
zeroconf library; we don't need to re-announce.

Note on container networking: HA OS add-ons run in a bridged
network. mDNS via the standard zeroconf library binds to all
interfaces and broadcasts on the LAN-facing one. If the host
firewall blocks UDP/5353, discovery silently fails and the user
falls back to manual host/port config — that path still works as a
backstop.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


# Standard mDNS service type for SentiHome. Picked deliberately — no
# existing service uses this; the integration's manifest pins this
# exact string.
_SERVICE_TYPE = "_sentihome._tcp.local."


@dataclass
class DiscoveryHandle:
    """Holds the zeroconf object so it doesn't get GC'd. Caller keeps
    this alive for the lifetime of the add-on; on shutdown, call
    :meth:`close` to unregister cleanly."""

    zc: object
    info: object

    def close(self) -> None:
        try:
            self.zc.unregister_service(self.info)  # type: ignore[attr-defined]
            self.zc.close()  # type: ignore[attr-defined]
        except Exception as e:
            logger.debug("discovery_publish.close_failed", error=str(e))


def publish_sentihome(
    *,
    port: int,
    version: str,
) -> DiscoveryHandle | None:
    """Register the mDNS service so HA's zeroconf discovery picks us up.

    Returns a handle the caller stashes for cleanup, or None if the
    zeroconf library isn't importable or registration failed. Either
    failure mode is non-fatal — the manual config flow remains.
    """
    try:
        from zeroconf import IPVersion, ServiceInfo, Zeroconf  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "discovery_publish.zeroconf_not_installed",
            hint="pip install zeroconf — discovery disabled, manual config still works",
        )
        return None

    hostname = socket.gethostname() or "sentihome"
    # ServiceInfo wants packed-bytes IPv4 addresses. We pick the
    # container's primary outbound interface IP via socket.gethostbyname.
    # Falls back to 127.0.0.1 if name resolution fails (HA still works
    # via the hostname property).
    try:
        ip = socket.gethostbyname(hostname)
    except OSError:
        ip = "127.0.0.1"

    properties = {
        b"host": hostname.encode("utf-8"),
        b"port": str(port).encode("utf-8"),
        b"version": version.encode("utf-8"),
        b"path": b"/healthz",
    }

    info = ServiceInfo(
        type_=_SERVICE_TYPE,
        name=f"SentiHome ({hostname})._sentihome._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties=properties,
        server=f"{hostname}.local.",
    )

    try:
        # ip_version=All works on both IPv4-only and dual-stack networks.
        zc = Zeroconf(ip_version=IPVersion.All)
        zc.register_service(info)
        logger.info(
            "discovery_publish.registered",
            service=_SERVICE_TYPE,
            host=hostname,
            ip=ip,
            port=port,
            version=version,
        )
        return DiscoveryHandle(zc=zc, info=info)
    except Exception as e:
        logger.warning(
            "discovery_publish.register_failed",
            error=str(e),
            hint="manual config flow still works as backstop",
        )
        return None
