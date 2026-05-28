"""Publishes CameraConfigEvents to the preprocessor over NATS.

The preprocessor's CameraConfigSubscriber (Epic 10.1.6) accepts
``configured`` / ``removed`` events on canonical subjects and starts
or stops RTSP capture tasks dynamically. This module is the producer
side, living in the HA-side ha-agent service.

Wiring shape::

    publisher = CameraConfigPublisher(
        nats_url="nats://nats:4222",
        creds=ChainProvider([
            StreamSourceAttrProvider(ha_client),
            JsonFileProvider(Path("/data/sentihome/camera_rtsp_credentials.json")),
        ]),
    )
    await publisher.connect()
    # Reconciler.apply() then calls:
    await publisher.publish_configured(spec)
    await publisher.publish_removed(camera_id)

The credentials provider is pluggable so we can land the simple
JSON-file source first and add HA-config-entry scraping later
without changing this module.

See ``planning/epics/10.1.6.2-ha-agent-camera-publisher.md`` for the
full design + sources of credentials + rollout plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote as urlquote

import structlog
from nats.aio.client import Client as NATS
from sentihome_shared.preprocessor import (
    SUBJECT_CAMERA_CONFIGURED,
    SUBJECT_CAMERA_REMOVED,
    CameraConfigEvent,
)

if TYPE_CHECKING:
    from sentihome_ha_agent.discovery import DiscoverySpec

logger = structlog.get_logger(__name__)


# ─── Credentials provider (pluggable) ────────────────────────────────


class CredentialsProvider(Protocol):
    """Returns the raw-RTSP URL for a given device_id, or None if it
    can't resolve one. Implementations chain (try the cheapest first;
    fall back through HA attribute scrape -> config-entry parse ->
    operator-supplied JSON file)."""

    async def get_rtsp_url(
        self, *, device_id: str, vendor: str | None
    ) -> str | None: ...


# ─── Vendor URL templates ───────────────────────────────────────────


_VENDOR_TEMPLATES: dict[str, dict[str, str]] = {
    "reolink": {
        "sub": "rtsp://{user}:{password}@{ip}:554/h264Preview_01_sub",
        "main": "rtsp://{user}:{password}@{ip}:554/h265Preview_01_main",
    },
    "dahua": {
        "sub": "rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel=1&subtype=1",
        "main": "rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel=1&subtype=0",
    },
    # unifi / hikvision / amcrest land here as we encounter them.
}


def construct_rtsp_url(
    *,
    vendor: str,
    ip: str,
    user: str,
    password: str,
    stream: str = "sub",
) -> str:
    """Build a raw RTSP URL from credentials + a vendor pattern.

    The password is URL-encoded so embedded ``%``, ``@``, ``/`` etc.
    don't break the URL parser (we saw real cameras with ``%`` in
    the password during dev testing).
    """
    template_set = _VENDOR_TEMPLATES.get(vendor)
    if template_set is None:
        raise ValueError(
            f"Unknown vendor {vendor!r}; supported: {sorted(_VENDOR_TEMPLATES)}"
        )
    template = template_set.get(stream)
    if template is None:
        raise ValueError(
            f"Unknown stream {stream!r} for vendor {vendor!r}; "
            f"supported: {sorted(template_set)}"
        )
    return template.format(
        user=urlquote(user, safe=""),
        password=urlquote(password, safe=""),
        ip=ip,
    )


# ─── JSON file credentials source (v0 fallback) ─────────────────────


class JsonFileProvider:
    """Reads device credentials from a local JSON file.

    File schema (see planning doc for the canonical version)::

        {
          "<device_id>": {
            "ip": "192.168.x.x",
            "user": "admin",
            "password": "...",
            "vendor": "reolink" | "dahua" | ...,
            "stream": "sub" | "main"   # optional, defaults to "sub"
          }
        }

    Missing file -> always returns None. Useful for environments
    where credentials aren't in the file system yet (CI / unit
    tests / first-boot before any operator input).
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    async def get_rtsp_url(
        self, *, device_id: str, vendor: str | None
    ) -> str | None:
        import json

        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(
                "camera_creds.read_failed", path=str(self._path), error=str(e)
            )
            return None

        entry = data.get(device_id)
        if not entry:
            return None

        try:
            return construct_rtsp_url(
                vendor=entry.get("vendor") or vendor or "",
                ip=entry["ip"],
                user=entry["user"],
                password=entry["password"],
                stream=entry.get("stream", "sub"),
            )
        except (KeyError, ValueError) as e:
            logger.warning(
                "camera_creds.malformed_entry",
                device_id=device_id,
                error=str(e),
            )
            return None


# ─── Chain provider ─────────────────────────────────────────────────


class ChainProvider:
    """Tries each sub-provider in order; returns the first non-None
    result. Lets us layer cheap-and-easy on top of expensive-and-
    flaky without conditional plumbing at the call site."""

    def __init__(self, providers: list[CredentialsProvider]) -> None:
        self._providers = providers

    async def get_rtsp_url(
        self, *, device_id: str, vendor: str | None
    ) -> str | None:
        for p in self._providers:
            url = await p.get_rtsp_url(device_id=device_id, vendor=vendor)
            if url is not None:
                return url
        return None


# ─── The publisher itself ───────────────────────────────────────────


class CameraConfigPublisher:
    """Owns the NATS connection + emits CameraConfigEvents.

    Stateless beyond the NATS connection. Caller (the Reconciler) is
    responsible for deciding *when* to publish; this module just
    knows *how*.
    """

    def __init__(
        self, *, nats_url: str, creds: CredentialsProvider
    ) -> None:
        self._url = nats_url
        self._creds = creds
        self._nc: NATS | None = None

    async def connect(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            return
        nc = NATS()
        await nc.connect(servers=[self._url])
        self._nc = nc
        logger.info("camera_publisher.connected", url=self._url)

    async def close(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            await self._nc.drain()
        self._nc = None

    async def publish_configured(self, spec: DiscoverySpec) -> bool:
        """Resolve credentials for the spec, build the RTSP URL, and
        publish on SUBJECT_CAMERA_CONFIGURED. Returns True on success,
        False when credentials weren't resolvable (the caller can
        decide whether to log + skip, or surface to the operator)."""
        vendor = _vendor_from_spec(spec)
        stream_url = await self._creds.get_rtsp_url(
            device_id=spec.device_id, vendor=vendor
        )
        if stream_url is None:
            logger.warning(
                "camera_publisher.no_creds",
                device_id=spec.device_id,
                vendor=vendor,
                hint=(
                    "Add an entry to /data/sentihome/camera_rtsp_credentials.json "
                    "for this device_id, or upgrade the HA integration so "
                    "camera.X.stream_source is populated."
                ),
            )
            return False

        event = CameraConfigEvent(
            action="configured",
            camera_id=spec.device_id,
            stream_url=stream_url,
            stream_protocol="rtsp",
            vendor=vendor,
            sub_stream=True,
        )
        await self._publish(SUBJECT_CAMERA_CONFIGURED, event)
        logger.info(
            "camera_publisher.configured",
            camera_id=spec.device_id,
            vendor=vendor,
        )
        return True

    async def publish_removed(self, camera_id: str) -> None:
        """Tell the preprocessor to tear down the capture task for a
        camera. No credentials needed."""
        event = CameraConfigEvent(action="removed", camera_id=camera_id)
        await self._publish(SUBJECT_CAMERA_REMOVED, event)
        logger.info("camera_publisher.removed", camera_id=camera_id)

    async def _publish(self, subject: str, event: CameraConfigEvent) -> None:
        if self._nc is None or not self._nc.is_connected:
            raise RuntimeError(
                "CameraConfigPublisher.publish before connect"
            )
        await self._nc.publish(subject, event.model_dump_json().encode("utf-8"))


# ─── helpers ─────────────────────────────────────────────────────────


def _vendor_from_spec(spec: DiscoverySpec) -> str | None:
    """Best-effort vendor extraction from a DiscoverySpec.

    The DiscoverySpec doesn't currently carry vendor explicitly --
    discovery infers it from entity name patterns. For now we
    pattern-match the camera_entity / friendly_name; later the
    DiscoverySpec contract can grow a ``vendor`` field.
    """
    name = (spec.camera_entity + " " + spec.friendly_name).lower()
    if "reolink" in name:
        return "reolink"
    if "dahua" in name or "amcrest" in name:
        return "dahua"
    if "unifi" in name or "ubiquiti" in name:
        return "unifi"
    return None
