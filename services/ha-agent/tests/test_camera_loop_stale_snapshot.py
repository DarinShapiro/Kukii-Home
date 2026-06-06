"""Stale-snapshot retry for cloud cameras (Ring/Nest).

Cloud cameras serve a CACHED snapshot via HA's camera_proxy — it lags the
motion event and repeats across consecutive alerts, so the saved frame is
identical and pre-event ("doesn't show what the alert claimed"). The loop
detects a byte-identical fetch and retries after a short delay so the
cloud can push the event image. Live cameras return distinct frames and
never retry (zero added latency).
"""

from __future__ import annotations

import hashlib

from kukiihome_ha_agent.camera_loop import CameraLoopRegistry, HACameraLoop
from kukiihome_ha_agent.http_api import AlertLog


class _ScriptedClient:
    """fetch_camera_snapshot returns each queued payload in turn (last one
    repeats once exhausted). Counts calls so retries are observable."""

    def __init__(self, payloads: list[bytes | Exception]) -> None:
        self._payloads = payloads
        self.calls = 0

    def on_state_change(self, handler) -> None:  # unused here
        pass

    def remove_state_change_handler(self, handler) -> None:  # unused here
        pass

    async def fetch_camera_snapshot(self, _entity: str) -> bytes:
        i = min(self.calls, len(self._payloads) - 1)
        self.calls += 1
        item = self._payloads[i]
        if isinstance(item, Exception):
            raise item
        return item


def _loop(client, tmp_path, **kw) -> HACameraLoop:
    return HACameraLoop(
        camera_id="ring_front",
        camera_entity="camera.front_door",
        motion_entities=["binary_sensor.front_door_motion"],
        camera_name="Front Door",
        client=client,
        alert_log=AlertLog(),
        registry=CameraLoopRegistry(),
        cooldown_seconds=0.0,
        snapshot_dir=str(tmp_path),
        # delay 0 so the test doesn't actually sleep
        stale_snapshot_retry_delay_s=0.0,
        stale_snapshot_max_retries=1,
        **kw,
    )


A = b"\xff\xd8\xff\xd9AAAA"
B = b"\xff\xd8\xff\xd9BBBB"


async def test_first_snapshot_no_retry(tmp_path):
    """No prior baseline → first fetch is accepted as-is, single call."""
    client = _ScriptedClient([A])
    loop = _loop(client, tmp_path)
    out = await loop._fetch_snapshot_skipping_stale()
    assert out == A
    assert client.calls == 1


async def test_stale_then_fresh_retries_and_returns_fresh(tmp_path):
    """Prior baseline == A; camera_proxy first returns the stale A, then
    the cloud pushes the real event image B. We retry and return B."""
    client = _ScriptedClient([A, B])
    loop = _loop(client, tmp_path)
    loop._last_snapshot_hash = hashlib.sha1(A, usedforsecurity=False).hexdigest()
    out = await loop._fetch_snapshot_skipping_stale()
    assert out == B
    assert client.calls == 2  # one retry happened


async def test_persistently_stale_gives_up_after_retries(tmp_path):
    """No Ring Protect → the cloud never pushes a fresh image. We retry
    the configured number of times then accept the duplicate rather than
    spin forever (better a stale frame than no alert)."""
    client = _ScriptedClient([A, A, A])
    loop = _loop(client, tmp_path)
    loop._last_snapshot_hash = hashlib.sha1(A, usedforsecurity=False).hexdigest()
    out = await loop._fetch_snapshot_skipping_stale()
    assert out == A
    assert client.calls == 2  # initial + 1 retry (max_retries=1), then give up


async def test_live_camera_distinct_frames_never_retry(tmp_path):
    """Live camera: each fetch differs from the last, so no retry — the
    cloud-camera mitigation adds zero latency for real streams."""
    client = _ScriptedClient([B])
    loop = _loop(client, tmp_path)
    loop._last_snapshot_hash = hashlib.sha1(A, usedforsecurity=False).hexdigest()
    out = await loop._fetch_snapshot_skipping_stale()
    assert out == B
    assert client.calls == 1


async def test_fetch_failure_returns_none(tmp_path):
    client = _ScriptedClient([RuntimeError("camera_proxy 502")])
    loop = _loop(client, tmp_path)
    out = await loop._fetch_snapshot_skipping_stale()
    assert out is None
    assert "snapshot fetch failed" in loop._status.last_error


async def test_baseline_updates_so_next_event_compares_against_latest(tmp_path):
    """After accepting B, the baseline becomes B — so a later genuine B
    repeat is what triggers the next retry, not the long-gone A."""
    client = _ScriptedClient([B])
    loop = _loop(client, tmp_path)
    await loop._fetch_snapshot_skipping_stale()
    assert loop._last_snapshot_hash == hashlib.sha1(B, usedforsecurity=False).hexdigest()
