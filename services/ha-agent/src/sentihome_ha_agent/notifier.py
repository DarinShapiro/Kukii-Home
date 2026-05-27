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

    def set_services(self, services: list[str]) -> None:
        """Swap the notify service list at runtime (v0.3.13+).

        Called from the Notifications card's POST handler after the
        user changes their selection — no add-on restart needed.
        """
        self.notify_services = list(services)
        logger.info("notifier.services_updated", services=list(services))

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
        results = await self._dispatch_capture(alert)
        for r in results:
            if not r["ok"]:
                logger.warning(
                    "notifier.send_failed",
                    service=r["service"],
                    error=r["error"],
                    alert_id=alert.get("alert_id"),
                )

    async def _dispatch_capture(self, alert: dict[str, Any]) -> list[dict[str, Any]]:
        """Dispatch + return per-service results.

        Used by both the fire-and-forget on_alert path (which logs
        failures) and the test_send diagnostic path (which surfaces
        them in the UI). Shared so the two paths can't diverge.
        """
        title, message, data = self._render(alert)
        # Concurrent fan-out — one slow/failing service can't block
        # another. return_exceptions so we collect rather than raise.
        results = await asyncio.gather(
            *(self._send_one(svc, title, message, data) for svc in self.notify_services),
            return_exceptions=True,
        )
        return [
            {
                "service": svc,
                "ok": not isinstance(r, Exception),
                "error": str(r) if isinstance(r, Exception) else None,
            }
            for svc, r in zip(self.notify_services, results, strict=True)
        ]

    async def test_send(self, alert: dict[str, Any]) -> list[dict[str, Any]]:
        """Send a notification synchronously and return per-service results.

        Powers the "Send test notification" button on the Web UI's
        Notifications card. Returns a list of
        ``{service, ok, error}`` dicts so the UI can render
        success/failure per service inline — invaluable when the user
        is setting up notifications and a service silently fails (e.g.
        HA Companion app not logged in on the target device).
        """
        return await self._dispatch_capture(alert)

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
        """Build (title, message, data) for the HA notify payload.

        Title is the alert headline ("Person at Front Door"). Message
        is a one-line summary including classification, friendly name,
        area (when known), and time. The data dict carries the
        HA-Companion-specific fields: ``url`` (tap-action), ``image``
        (inline attachment), and ``tag`` (per-camera dedup).

        URLs are computed against ``sentihome_ingress_base`` when set
        so they route through HA Ingress (visible to the user as the
        SentiHome Web UI, with HA Companion's auth). When the prefix
        is unknown (no Supervisor), falls back to a stable HA path
        that redirects to the current ingress URL.
        """
        # ─── pull alert fields, with defaults ────────────────────
        camera_id = alert.get("camera_id") or "camera"
        # Prefer the human-readable camera name if the alert carries
        # it (HACameraLoop now stamps friendly_name; older alerts
        # might not have it).
        camera_label = (
            alert.get("camera_name")
            or alert.get("friendly_name")
            or camera_id.replace("_", " ").title()
        )
        recorded_at = alert.get("recorded_at", "")
        sensor_kind = alert.get("sensor_classification") or ""
        area = alert.get("area") or ""  # populated by a future epic
        is_test = (alert.get("source") or "").startswith(("notify_test", "camera_test"))

        # Pretty timestamp: HH:MM:SS from the ISO recorded_at.
        time_str = ""
        if recorded_at:
            from datetime import datetime

            try:
                time_str = datetime.fromisoformat(recorded_at).strftime("%H:%M:%S")
            except (ValueError, TypeError):
                time_str = recorded_at[:8]

        # ─── title ───────────────────────────────────────────────
        # Use the explicit headline when present (HACameraLoop already
        # formats nicely like "Person at Pool Cam"). Otherwise build:
        #   "Person at Pool Cam" / "Motion at Pool Cam"
        # Prefix with [TEST] for diagnostic alerts so the user knows
        # what they're looking at on the phone.
        kind_word = sensor_kind.capitalize() if sensor_kind else "Motion"
        if alert.get("headline"):
            title = alert["headline"]
        else:
            title = f"{kind_word} at {camera_label}"
        if is_test and not title.startswith("[TEST]"):
            title = f"[TEST] {title}"

        # ─── message ─────────────────────────────────────────────
        # Two-line message: classification + camera/area on first
        # implied line, time on second. HA Companion renders this as
        # the notification body; phones strip newlines but keep
        # readability.
        message_bits: list[str] = []
        if sensor_kind:
            message_bits.append(f"{kind_word} detected")
        else:
            message_bits.append("Motion detected")
        if camera_label and camera_label.lower() not in title.lower():
            message_bits.append(f"on {camera_label}")
        if area:
            message_bits.append(f"in {area}")
        if time_str:
            message_bits.append(f"at {time_str}")
        message = " ".join(message_bits)

        # ─── data: url, image, tag ───────────────────────────────
        # v0.3.17 — URLs must be auth'd by the mobile app's session, not
        # by the add-on's server-side ingress token. The /api/hassio_ingress/
        # <addon_token>/ URLs (which we tried in v0.3.15) 401 from the
        # phone because that token is for browser ingress sessions, not
        # mobile Companion sessions.
        #
        # Correct strategy:
        #   url   → /hassio/ingress/sentihome   (HA frontend route;
        #                                        per-user redirect)
        #   image → /api/camera_proxy/<entity>  (HA's own image endpoint;
        #                                        served with mobile
        #                                        session auth)
        #
        # Trade-off on image: it's the CURRENT camera frame, not the
        # at-alert-time snapshot. For security alerts this is arguably
        # MORE useful — "what's happening right now" — and it just
        # works without us trying to thread a SentiHome-served path
        # through HA's auth.
        url = "/hassio/ingress/sentihome"
        camera_entity = alert.get("camera_entity") or ""
        image_url = f"/api/camera_proxy/{camera_entity}" if camera_entity else ""

        data: dict[str, Any] = {
            "url": url,
            "clickAction": url,  # Android Companion uses this name
        }
        if image_url:
            data["image"] = image_url
        # Tag per camera so sequential alerts from the same camera
        # collapse on the phone instead of stacking. Users who WANT
        # one notification per event can change this later in the
        # alert config UI (when that ships).
        if camera_id:
            data["tag"] = f"sentihome_{camera_id}"

        # v0.3.18 — high-priority delivery flags. SentiHome's portion
        # of notify latency is ~300ms (LAN → HA → return); the user-
        # perceived delay is dominated by APNs/FCM delivery. Defaults
        # are NORMAL priority, which on iOS gets deferred when the
        # device is in low-power mode, Focus mode, screen-off, etc.
        # We're a security/presence app — alerts are time-sensitive.
        #
        # iOS (apns headers + iOS 15+ interruption-level):
        #   apns-priority: 10 → immediate delivery
        #   apns-push-type: alert → not background, surface NOW
        #   interruption-level: time-sensitive → bypasses Focus modes
        # Android (FCM):
        #   priority: high → bypasses Doze / App Standby
        data["priority"] = "high"
        data["apns_headers"] = {
            "apns-priority": "10",
            "apns-push-type": "alert",
        }
        data["push"] = {
            "interruption-level": "time-sensitive",
        }

        return title, message, data
