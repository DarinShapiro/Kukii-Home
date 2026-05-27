"""Fan alerts out to HA's notify.* services.

Subscribed via :meth:`AlertLog.add_on_record` at bootstrap. Every alert
that lands in the log gets pushed to every configured notify service
(e.g. ``notify.mobile_app_pixel_8``, ``notify.alexa_media``).

Payload shape — what each notify service sees:

  - ``title``  — the alert headline ("Person at Pool Cam")
  - ``message`` — sensor classification + timestamp + camera friendly
    name + area (when known)
  - ``data.url`` — link to the SentiHome status page (so tapping the
    notification opens the Web UI in the HA app)
  - ``data.image`` — link to the alert's snapshot (when one was
    captured). The HA Companion app fetches this via the SentiHome
    Ingress URL — reachable from inside HA's network.

Why a callback + ``asyncio.create_task`` rather than awaiting in
``AlertLog.record``: the recording path is synchronous (called from
camera loops with no async-await chain back to the I/O layer). Notify
is I/O-bound — bouncing off ``create_task`` keeps the alert path
non-blocking and lets failures be logged without affecting the alert
record.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from sentihome_ha_agent.client import HAClient

logger = structlog.get_logger(__name__)


@dataclass
class AlertNotifier:
    """Pushes each :class:`AlertLog` alert to one or more notify.* services.

    Construct with the list of notify services + the HA client. Wire
    its :meth:`on_alert` into :meth:`AlertLog.add_on_record` at boot.
    """

    client: HAClient
    notify_services: list[str]
    """Full HA service names like ``notify.mobile_app_pixel_8``.
    Empty list = no-op (the notifier is still installed but does
    nothing on every alert). Useful for testing without spamming
    your phone."""

    sentihome_ingress_base: str = ""
    """Optional base URL for the SentiHome ingress prefix
    (e.g. ``/api/hassio_ingress/<token>/``). When set, image + url
    payloads are absolute paths under this prefix so the HA app can
    fetch them via its existing auth. Empty = pass relative paths
    and let the app's URL-resolution figure it out."""

    _pending_tasks: set[asyncio.Task] = field(default_factory=set)
    """Holds task references so create_task doesn't lose them to GC."""

    def on_alert(self, alert: dict[str, Any]) -> None:
        """Synchronous entry point — bridge to async notify call.

        Called from :meth:`AlertLog.record` (sync). Fires-and-forgets
        the actual HA service call so the recording path stays fast.
        """
        if not self.notify_services:
            return
        task = asyncio.create_task(self._dispatch(alert))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _dispatch(self, alert: dict[str, Any]) -> None:
        title, message, data = self._render(alert)
        # Send to each service concurrently — if one fails, others
        # still go through. asyncio.gather with return_exceptions so
        # one bad service doesn't break the rest.
        results = await asyncio.gather(
            *(self._send_one(svc, title, message, data) for svc in self.notify_services),
            return_exceptions=True,
        )
        for svc, result in zip(self.notify_services, results, strict=True):
            if isinstance(result, Exception):
                logger.warning(
                    "notifier.send_failed",
                    service=svc,
                    error=str(result),
                    alert_id=alert.get("alert_id"),
                )

    async def _send_one(self, service: str, title: str, message: str, data: dict[str, Any]) -> None:
        # service is "notify.mobile_app_X"; HA service call API takes
        # the dotted form as one string when invoked via call_service.
        if "." not in service:
            raise ValueError(f"notify service must be in 'notify.X' form, got {service!r}")
        domain, svc = service.split(".", 1)
        body: dict[str, Any] = {
            "title": title,
            "message": message,
        }
        if data:
            body["data"] = data
        await self.client.call_service(domain, svc, data=body)

    def _render(self, alert: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        """Build (title, message, data) for the HA notify payload."""
        headline = alert.get("headline") or "Motion alert"
        camera_name = alert.get("camera_id") or "camera"
        recorded_at = alert.get("recorded_at", "")
        sensor_kind = alert.get("sensor_classification") or ""
        area = alert.get("area") or ""  # populated in a future epic
        # Pretty timestamp: HH:MM:SS from the ISO recorded_at.
        time_str = ""
        if recorded_at:
            from datetime import datetime

            try:
                time_str = datetime.fromisoformat(recorded_at).strftime("%H:%M:%S")
            except (ValueError, TypeError):
                time_str = recorded_at[:8]

        bits = [sensor_kind.capitalize() or "Motion"]
        if camera_name:
            bits.append(f"at {camera_name}")
        if area:
            bits.append(f"({area})")
        if time_str:
            bits.append(f"— {time_str}")
        message = " ".join(b for b in bits if b)

        # Build click-through + image URLs. When SentiHome is reached
        # via HA Ingress, the URLs must include the ingress prefix.
        alert_id = alert.get("alert_id") or ""
        if self.sentihome_ingress_base:
            base = self.sentihome_ingress_base.rstrip("/")
            url = base + "/"
            image = f"{base}/alerts/{alert_id}/snapshot" if alert_id else ""
        else:
            # No ingress prefix configured — pass relative paths and
            # let the HA app's URL resolver handle it. (Direct LAN
            # access via http://<host>:8765 also works here.)
            url = "/"
            image = f"/alerts/{alert_id}/snapshot" if alert_id else ""

        data: dict[str, Any] = {"url": url}
        if image and alert.get("evidence_ref"):
            # Only include image when a snapshot file actually exists.
            # The HA Companion app falls back gracefully if image fetch
            # fails, but spamming broken image URLs is rude.
            data["image"] = image

        return headline, message, data
