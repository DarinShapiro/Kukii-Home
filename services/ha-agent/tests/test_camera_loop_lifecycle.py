"""Lifecycle tests for HACameraLoop motion-handler registration.

Regression coverage for the "alerts won't stop after disabling a
camera" bug: the loop registered a state-change handler on the shared
HAClient but never unregistered it on stop, so a disabled camera kept
firing alerts forever (and toggling multiplied the handlers).
"""

from __future__ import annotations

import asyncio

from sentihome_ha_agent.camera_loop import CameraLoopRegistry, HACameraLoop
from sentihome_ha_agent.client import HAState
from sentihome_ha_agent.http_api import AlertLog


class _FakeClient:
    """Minimal stand-in for HAClient's handler registry + snapshot fetch.

    Mirrors the real on_state_change / remove_state_change_handler
    semantics (idempotent add, tolerant remove) so the loop's
    register/unregister behavior is exercised against a faithful copy.
    """

    def __init__(self) -> None:
        self._handlers: list = []

    def on_state_change(self, handler) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    def remove_state_change_handler(self, handler) -> None:
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass

    async def fetch_camera_snapshot(self, _entity: str) -> bytes:
        return b"\xff\xd8\xff\xd9JPEG"

    async def dispatch(self, new: HAState, old: HAState | None = None) -> None:
        for h in list(self._handlers):
            await h(new, old)


def _make_loop(client: _FakeClient, alert_log: AlertLog, tmp_path) -> HACameraLoop:
    return HACameraLoop(
        camera_id="poolcam",
        camera_entity="camera.poolcam",
        motion_entities=["binary_sensor.poolcam_motion"],
        camera_name="Pool Cam",
        client=client,
        alert_log=alert_log,
        registry=CameraLoopRegistry(),
        cooldown_seconds=0.0,  # no debounce — isolate the lifecycle behavior
        snapshot_dir=str(tmp_path),
    )


def _motion_on() -> HAState:
    return HAState(entity_id="binary_sensor.poolcam_motion", state="on")


async def _run_until_subscribed(loop: HACameraLoop) -> asyncio.Task:
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)  # let run() register its handler
    return task


async def test_stop_unregisters_motion_handler(tmp_path):
    """After stop(), the loop's handler is gone from the client, so a
    motion event records no alert — the core fix for the flood."""
    client = _FakeClient()
    alert_log = AlertLog()
    loop = _make_loop(client, alert_log, tmp_path)

    task = await _run_until_subscribed(loop)
    assert len(client._handlers) == 1  # subscribed

    await loop.stop()
    await asyncio.wait_for(task, timeout=2.0)
    assert len(client._handlers) == 0  # unsubscribed on stop

    # A motion event now reaches nobody → no alert.
    await client.dispatch(_motion_on())
    assert alert_log.recent(10) == []


async def test_handler_noops_after_stop_even_if_still_registered(tmp_path):
    """Defense in depth: even if a stale registration lingers, the
    handler must not fire once the loop is stopped."""
    client = _FakeClient()
    alert_log = AlertLog()
    loop = _make_loop(client, alert_log, tmp_path)

    task = await _run_until_subscribed(loop)
    await loop.stop()
    await asyncio.wait_for(task, timeout=2.0)

    # Force a stale registration and dispatch anyway.
    client.on_state_change(loop._on_state_change)
    await client.dispatch(_motion_on())
    assert alert_log.recent(10) == []  # stop-event guard suppressed it


async def test_restart_does_not_accumulate_handlers(tmp_path):
    """Stop + start (a reconcile restart) must leave exactly one handler,
    not two — otherwise each toggle doubles alerts per motion event."""
    client = _FakeClient()
    alert_log = AlertLog()

    loop1 = _make_loop(client, alert_log, tmp_path)
    t1 = await _run_until_subscribed(loop1)
    await loop1.stop()
    await asyncio.wait_for(t1, timeout=2.0)

    loop2 = _make_loop(client, alert_log, tmp_path)
    t2 = await _run_until_subscribed(loop2)
    assert len(client._handlers) == 1  # not 2

    # One motion event → exactly one alert (not doubled).
    await client.dispatch(_motion_on())
    assert len(alert_log.recent(10)) == 1

    await loop2.stop()
    await asyncio.wait_for(t2, timeout=2.0)


async def test_live_loop_still_alerts_on_motion(tmp_path):
    """Sanity: a running (not-stopped) loop DOES alert — the fix must
    not break the normal path."""
    client = _FakeClient()
    alert_log = AlertLog()
    loop = _make_loop(client, alert_log, tmp_path)

    task = await _run_until_subscribed(loop)
    await client.dispatch(_motion_on())
    assert len(alert_log.recent(10)) == 1

    await loop.stop()
    await asyncio.wait_for(task, timeout=2.0)
