"""Live reconciliation of HACameraLoops to a target DiscoverySpec set.

The old flow was: at boot, iterate ``topology.adapters`` and spawn one
:class:`HACameraLoop` per entry. There was no way to add/remove cameras
without restarting the add-on.

This module replaces that with a long-lived :class:`Reconciler` that
holds the running loops, accepts a fresh target list at any time, and
diffs to figure out what to start / stop / restart.

The Web UI's ``/discovery/*`` POST endpoints (toggle Enabled, change
stream / motion / cooldown) call :meth:`Reconciler.apply` after writing
to the overrides file — so a user click takes effect immediately, no
restart needed.

Lifecycle rules:

  * **Start** when a device is in the target list but not running.
  * **Stop** when a device is running but no longer in the target list
    (user disabled it, or auto-disabled by health check).
  * **Restart** when the device's :class:`DiscoverySpec` has changed
    (stream entity, motion set, or cooldown differ from the running
    loop's config).

A "restart" stops the old loop and starts a fresh one — simpler than
mutating live state and avoids race conditions in the loop's debounce
clock.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from kukiihome_ha_agent.camera_config_publisher import CameraConfigPublisher
    from kukiihome_ha_agent.camera_loop import CameraLoopRegistry, HACameraLoop
    from kukiihome_ha_agent.client import HAClient
    from kukiihome_ha_agent.discovery import DiscoverySpec
    from kukiihome_ha_agent.http_api import AlertLog

logger = structlog.get_logger(__name__)


@dataclass
class _RunningLoop:
    """One live HACameraLoop + the asyncio.Task running it."""

    spec: DiscoverySpec
    loop: HACameraLoop
    task: asyncio.Task


@dataclass
class ReconcileDiff:
    """What changed in the last :meth:`Reconciler.apply` call.

    Surfaced for logging + tests. The UI doesn't read it directly —
    it just re-renders from the post-apply state of /ha_cameras.
    """

    started: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    restarted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


class Reconciler:
    """Owns the set of running HACameraLoops and reconciles to a target.

    Single-instance per add-on. Created at boot, fed initial specs from
    :func:`.discovery.build_decisions`, then re-fed whenever the
    overrides file or the underlying HA camera list changes.
    """

    def __init__(
        self,
        *,
        client: HAClient,
        alert_log: AlertLog,
        registry: CameraLoopRegistry,
        camera_publisher: CameraConfigPublisher | None = None,
    ) -> None:
        self._client = client
        self._alert_log = alert_log
        self._registry = registry
        self._camera_publisher = camera_publisher
        """Optional NATS publisher (Epic 10.1.6.2). When provided,
        every reconcile diff fans out as CameraConfigEvents to the
        preprocessor — start/restart -> configured, stop -> removed
        — so the preprocessor's camera set tracks the Web UI Enable
        toggle in real time without an add-on restart. ``None``
        (default / tests / standalone-without-preprocessor): events
        aren't published; the Reconciler still manages HACameraLoops
        as before."""
        self._running: dict[str, _RunningLoop] = {}
        self._apply_lock = asyncio.Lock()

    @property
    def running_device_ids(self) -> set[str]:
        return set(self._running.keys())

    async def apply(self, target_specs: list[DiscoverySpec]) -> ReconcileDiff:
        """Bring the running loop set into line with ``target_specs``.

        Concurrency-safe — wrapped in a lock so overlapping POST
        requests can't double-start or double-stop a loop.
        """
        async with self._apply_lock:
            return await self._apply_locked(target_specs)

    async def _apply_locked(self, target_specs: list[DiscoverySpec]) -> ReconcileDiff:
        # Import here to break the camera_loop ↔ reconciler import cycle
        # at module-load time. Both modules are stable; the late import
        # has no runtime cost after first call.
        from kukiihome_ha_agent.camera_loop import HACameraLoop

        diff = ReconcileDiff()
        target_by_id = {s.device_id: s for s in target_specs}

        # ─── stop loops no longer in target ────────────────────────
        for device_id in list(self._running.keys()):
            if device_id not in target_by_id:
                await self._stop_one(device_id)
                diff.stopped.append(device_id)

        # ─── start / restart ──────────────────────────────────────
        for device_id, spec in target_by_id.items():
            running = self._running.get(device_id)
            if running is None:
                # Brand new.
                self._start_one(spec, HACameraLoop)
                diff.started.append(device_id)
            elif _specs_differ(running.spec, spec):
                # Changed — restart with the new config.
                await self._stop_one(device_id)
                self._start_one(spec, HACameraLoop)
                diff.restarted.append(device_id)
            else:
                diff.unchanged.append(device_id)

        # Fan diff out to the preprocessor via NATS (best-effort —
        # one camera's publish failure must not abort the loop
        # lifecycle changes we just made locally).
        if self._camera_publisher is not None:
            for device_id in diff.started + diff.restarted:
                spec = target_by_id[device_id]
                try:
                    await self._camera_publisher.publish_configured(spec)
                except Exception as e:
                    logger.warning(
                        "reconciler.publish_configured_failed",
                        device_id=device_id,
                        error=str(e),
                    )
            for device_id in diff.stopped:
                try:
                    await self._camera_publisher.publish_removed(device_id)
                except Exception as e:
                    logger.warning(
                        "reconciler.publish_removed_failed",
                        device_id=device_id,
                        error=str(e),
                    )

        logger.info(
            "reconciler.applied",
            started=diff.started,
            stopped=diff.stopped,
            restarted=diff.restarted,
            unchanged=len(diff.unchanged),
        )
        return diff

    def _start_one(
        self,
        spec: DiscoverySpec,
        ha_camera_loop_cls: type[HACameraLoop],
    ) -> None:
        """Spawn a fresh :class:`HACameraLoop` for ``spec``."""
        loop = ha_camera_loop_cls(
            camera_id=spec.device_id,
            camera_entity=spec.camera_entity,
            motion_entities=list(spec.motion_entities),
            camera_name=spec.friendly_name,
            client=self._client,
            alert_log=self._alert_log,
            registry=self._registry,
            cooldown_seconds=spec.cooldown_seconds,
        )
        task = asyncio.create_task(loop.run(), name=f"ha_camera_{spec.device_id}")
        task.add_done_callback(
            lambda t: (
                logger.warning(
                    "reconciler.loop_task_exception",
                    device_id=spec.device_id,
                    error=str(t.exception()),
                )
                if not t.cancelled() and t.exception()
                else None
            )
        )
        self._running[spec.device_id] = _RunningLoop(spec=spec, loop=loop, task=task)
        logger.info(
            "reconciler.started",
            device_id=spec.device_id,
            camera_entity=spec.camera_entity,
            motion_entities=list(spec.motion_entities),
            source=spec.source,
        )

    async def _stop_one(self, device_id: str) -> None:
        running = self._running.pop(device_id, None)
        if running is None:
            return
        await running.loop.stop()
        # Give the task a brief moment to unwind from `await
        # _stop_event.wait()`. If it doesn't, cancel — the loop's only
        # side-effect after stop() is logging, so cancellation is safe.
        try:
            await asyncio.wait_for(running.task, timeout=2.0)
        except TimeoutError:
            running.task.cancel()
            try:
                await running.task
            except (asyncio.CancelledError, Exception) as e:
                # Cancellation is the expected path. Any other exception
                # here is from the loop's own teardown — log it but
                # don't propagate, since the loop is going away anyway.
                if not isinstance(e, asyncio.CancelledError):
                    logger.debug("reconciler.cancel_unwound_with_error", error=str(e))
        # The registry still has the status entry from when the loop
        # started — drop it so the UI doesn't show a ghost. The
        # registry is small (one entry per device) so a direct pop is
        # cheaper than iterating.
        self._registry.by_camera_id.pop(device_id, None)
        logger.info("reconciler.stopped", device_id=device_id)


def _specs_differ(a: DiscoverySpec, b: DiscoverySpec) -> bool:
    """True iff the spec change is significant enough to restart."""
    return (
        a.camera_entity != b.camera_entity
        or a.motion_entities != b.motion_entities
        or a.cooldown_seconds != b.cooldown_seconds
    )
