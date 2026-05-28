"""Subscribes to camera-config broadcasts from ha-agent.

Two subjects (see ``sentihome_shared.preprocessor.nats_subjects``):

* :data:`~sentihome_shared.preprocessor.SUBJECT_CAMERA_CONFIGURED`
* :data:`~sentihome_shared.preprocessor.SUBJECT_CAMERA_REMOVED`

The subscriber routes each event to a caller-supplied
:class:`CameraConfigApplier`. In RTSP mode the applier wraps the
:class:`RTSPCaptureSupervisor`'s add/remove. In synthetic mode the
applier is a no-op (synthetic backend has no per-camera state to
update).

Keeping the applier as an interface (instead of binding directly to
the supervisor) means tests can verify routing without spinning up
the full capture path, AND it lets phase 10.2's topology-source
flow plug into the same callback shape.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

import structlog
from nats.aio.client import Client as NATS
from nats.aio.msg import Msg
from sentihome_shared.preprocessor import (
    SUBJECT_CAMERA_CONFIGURED,
    SUBJECT_CAMERA_REMOVED,
    CameraConfigEvent,
)

logger = structlog.get_logger(__name__)


class CameraConfigApplier(Protocol):
    """Contract the subscriber calls on each (validated) event.

    Implementations:

    * :class:`SupervisorApplier` (production-ish; RTSP backend) —
      delegates to ``RTSPCaptureSupervisor.add`` / ``.remove``.
    * Lambda / no-op (synthetic backend, tests).
    """

    async def on_configured(self, event: CameraConfigEvent) -> None: ...
    async def on_removed(self, event: CameraConfigEvent) -> None: ...


class CameraConfigSubscriber:
    """NATS subscriber that turns camera-config broadcasts into
    applier calls."""

    def __init__(self, nats_url: str, applier: CameraConfigApplier) -> None:
        self._url = nats_url
        self._applier = applier
        self._nc: NATS | None = None

    async def connect(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            return
        nc = NATS()
        await nc.connect(servers=[self._url])
        self._nc = nc

        await nc.subscribe(SUBJECT_CAMERA_CONFIGURED, cb=self._on_configured)
        await nc.subscribe(SUBJECT_CAMERA_REMOVED, cb=self._on_removed)

        logger.info(
            "preprocessor.camera_config.subscribed",
            url=self._url,
            subjects=[SUBJECT_CAMERA_CONFIGURED, SUBJECT_CAMERA_REMOVED],
        )

    async def close(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            await self._nc.drain()
        self._nc = None

    # ─── handlers ───────────────────────────────────────────────────

    async def _on_configured(self, msg: Msg) -> None:
        event = _parse(msg, expected_action="configured")
        if event is None:
            return
        if not event.stream_url:
            logger.warning(
                "preprocessor.camera_config.no_stream_url",
                camera_id=event.camera_id,
            )
            return
        try:
            await self._applier.on_configured(event)
        except Exception as e:
            logger.warning(
                "preprocessor.camera_config.apply_failed",
                camera_id=event.camera_id,
                action="configured",
                error=str(e),
            )

    async def _on_removed(self, msg: Msg) -> None:
        event = _parse(msg, expected_action="removed")
        if event is None:
            return
        try:
            await self._applier.on_removed(event)
        except Exception as e:
            logger.warning(
                "preprocessor.camera_config.apply_failed",
                camera_id=event.camera_id,
                action="removed",
                error=str(e),
            )


def _parse(msg: Msg, *, expected_action: str) -> CameraConfigEvent | None:
    try:
        event = CameraConfigEvent.model_validate_json(msg.data)
    except Exception as e:
        logger.warning(
            "preprocessor.camera_config.bad_payload",
            subject=msg.subject,
            error=str(e),
        )
        return None
    if event.action != expected_action:
        # Soft-route mis-routed events. Most production-grade brokers
        # never get this wrong, but the defensive path makes test
        # mistakes obvious.
        logger.info(
            "preprocessor.camera_config.action_mismatch",
            subject=msg.subject,
            actual_action=event.action,
            expected_action=expected_action,
        )
    return event


# ─── Convenience applier impl: bind to the RTSP supervisor ──────────


class SupervisorApplier:
    """Default applier used in RTSP mode — translates events into
    ``RTSPCaptureSupervisor`` mutations. Imports the supervisor
    lazily to keep ``synthetic`` mode free of the av/cv2 dep chain
    in environments that don't need them."""

    def __init__(self, supervisor: object) -> None:
        # Typed loosely so synthetic-mode test paths can pass a stub.
        self._supervisor = supervisor

    async def on_configured(self, event: CameraConfigEvent) -> None:
        assert event.stream_url is not None  # validated upstream
        await self._supervisor.add(  # type: ignore[attr-defined]
            camera_id=event.camera_id,
            rtsp_url=event.stream_url,
        )

    async def on_removed(self, event: CameraConfigEvent) -> None:
        await self._supervisor.remove(event.camera_id)  # type: ignore[attr-defined]


# ─── Convenience applier impl: no-op (synthetic mode) ───────────────


class NoOpApplier:
    """Applier used in synthetic mode — logs events but doesn't
    mutate anything. Keeps the subscription wired so the system
    has uniform shape across backends, but the synthetic frame
    buffer is unaffected by camera config changes."""

    async def on_configured(self, event: CameraConfigEvent) -> None:
        logger.info(
            "preprocessor.camera_config.noop_configured",
            camera_id=event.camera_id,
            stream_protocol=event.stream_protocol,
        )

    async def on_removed(self, event: CameraConfigEvent) -> None:
        logger.info("preprocessor.camera_config.noop_removed", camera_id=event.camera_id)


# ─── Functional applier (tests) ──────────────────────────────────────


class CallbackApplier:
    """Applier that records calls — used by tests to verify routing
    without coupling to the supervisor."""

    def __init__(
        self,
        *,
        on_configured: Callable[[CameraConfigEvent], Awaitable[None]] | None = None,
        on_removed: Callable[[CameraConfigEvent], Awaitable[None]] | None = None,
    ) -> None:
        self._on_configured = on_configured
        self._on_removed = on_removed

    async def on_configured(self, event: CameraConfigEvent) -> None:
        if self._on_configured is not None:
            await self._on_configured(event)

    async def on_removed(self, event: CameraConfigEvent) -> None:
        if self._on_removed is not None:
            await self._on_removed(event)
