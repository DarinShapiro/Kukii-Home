"""Tests for the camera-loop reconciler (live start/stop logic)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock

from sentihome_ha_agent.camera_loop import CameraLoopRegistry
from sentihome_ha_agent.discovery import DiscoverySpec
from sentihome_ha_agent.reconciler import Reconciler, _specs_differ


def _spec(
    device_id: str = "x",
    *,
    camera_entity: str = "camera.x_main",
    motion_entities: tuple[str, ...] = ("binary_sensor.x_person",),
    cooldown_seconds: float = 10.0,
    source: str = "auto",
    friendly_name: str | None = None,
) -> DiscoverySpec:
    return DiscoverySpec(
        device_id=device_id,
        camera_entity=camera_entity,
        friendly_name=friendly_name or device_id,
        motion_entities=motion_entities,
        cooldown_seconds=cooldown_seconds,
        source=source,
    )


# ─── _specs_differ ────────────────────────────────────────────────────


def test_specs_differ_returns_false_when_identical():
    a = _spec()
    b = _spec()
    assert not _specs_differ(a, b)


def test_specs_differ_detects_stream_change():
    a = _spec(camera_entity="camera.x_main")
    b = _spec(camera_entity="camera.x_sub")
    assert _specs_differ(a, b)


def test_specs_differ_detects_motion_change():
    a = _spec(motion_entities=("binary_sensor.x_person",))
    b = _spec(motion_entities=("binary_sensor.x_person", "binary_sensor.x_vehicle"))
    assert _specs_differ(a, b)


def test_specs_differ_detects_cooldown_change():
    a = _spec(cooldown_seconds=10.0)
    b = _spec(cooldown_seconds=30.0)
    assert _specs_differ(a, b)


def test_specs_differ_ignores_source_change():
    """source flips auto↔override on a no-op clear — must not restart."""
    a = _spec(source="auto")
    b = _spec(source="override")
    assert not _specs_differ(a, b)


# ─── Reconciler ──────────────────────────────────────────────────────


@dataclass
class _FakeLoop:
    """Stand-in for HACameraLoop — records start + stop without doing
    real HA subscription work."""

    camera_id: str
    camera_entity: str
    motion_entities: list[str]
    camera_name: str
    client: object
    alert_log: object
    registry: object
    cooldown_seconds: float
    snapshot_dir: str = "/tmp/snap"
    started: bool = False
    stopped: bool = False
    _stop_event: asyncio.Event = None  # type: ignore[assignment]

    def __post_init__(self):
        self._stop_event = asyncio.Event()
        # Mirror HACameraLoop: register a status object so the
        # reconciler's cleanup-path (pop from registry) has something
        # to remove.
        from sentihome_ha_agent.camera_loop import CameraStreamStatus

        self.registry.register(
            CameraStreamStatus(camera_id=self.camera_id, rtsp_url=self.camera_entity)
        )

    async def run(self):
        self.started = True
        try:
            await self._stop_event.wait()
        finally:
            self.stopped = True

    async def stop(self):
        self._stop_event.set()


def _make_reconciler() -> Reconciler:
    """Reconciler with mock client + alert_log + a real registry."""
    return Reconciler(
        client=MagicMock(),
        alert_log=MagicMock(record=MagicMock()),
        registry=CameraLoopRegistry(),
    )


async def _apply_with_fake_loop(rec: Reconciler, specs: list[DiscoverySpec]):
    """Patch in _FakeLoop instead of HACameraLoop for tests that don't
    need the real camera plumbing."""
    import sentihome_ha_agent.camera_loop as cl

    real_cls = cl.HACameraLoop
    cl.HACameraLoop = _FakeLoop  # type: ignore[assignment, misc]
    try:
        return await rec.apply(specs)
    finally:
        cl.HACameraLoop = real_cls  # type: ignore[assignment]


async def test_reconciler_starts_loops_for_new_specs():
    rec = _make_reconciler()
    specs = [_spec(device_id="a"), _spec(device_id="b", camera_entity="camera.b_main")]
    diff = await _apply_with_fake_loop(rec, specs)
    assert sorted(diff.started) == ["a", "b"]
    assert diff.stopped == []
    assert rec.running_device_ids == {"a", "b"}


async def test_reconciler_stops_loops_when_target_shrinks():
    rec = _make_reconciler()
    await _apply_with_fake_loop(
        rec,
        [_spec(device_id="a"), _spec(device_id="b", camera_entity="camera.b_main")],
    )
    diff = await _apply_with_fake_loop(rec, [_spec(device_id="a")])
    assert diff.started == []
    assert diff.stopped == ["b"]
    assert rec.running_device_ids == {"a"}


async def test_reconciler_restarts_when_stream_changes():
    rec = _make_reconciler()
    await _apply_with_fake_loop(rec, [_spec(device_id="a", camera_entity="camera.a_main")])
    diff = await _apply_with_fake_loop(rec, [_spec(device_id="a", camera_entity="camera.a_sub")])
    assert diff.restarted == ["a"]
    assert diff.started == []
    assert diff.stopped == []


async def test_reconciler_restarts_when_motion_changes():
    rec = _make_reconciler()
    await _apply_with_fake_loop(
        rec, [_spec(device_id="a", motion_entities=("binary_sensor.a_person",))]
    )
    diff = await _apply_with_fake_loop(
        rec,
        [
            _spec(
                device_id="a", motion_entities=("binary_sensor.a_person", "binary_sensor.a_vehicle")
            )
        ],
    )
    assert diff.restarted == ["a"]


async def test_reconciler_unchanged_when_specs_match():
    rec = _make_reconciler()
    await _apply_with_fake_loop(rec, [_spec(device_id="a")])
    diff = await _apply_with_fake_loop(rec, [_spec(device_id="a")])
    assert diff.started == []
    assert diff.stopped == []
    assert diff.restarted == []
    assert diff.unchanged == ["a"]


async def test_reconciler_empty_target_stops_everything():
    rec = _make_reconciler()
    await _apply_with_fake_loop(rec, [_spec(device_id="a"), _spec(device_id="b")])
    diff = await _apply_with_fake_loop(rec, [])
    assert sorted(diff.stopped) == ["a", "b"]
    assert rec.running_device_ids == set()


async def test_reconciler_drops_registry_entry_on_stop():
    """When a loop stops, its CameraStreamStatus should disappear from
    the registry so the /ha_cameras UI doesn't show a ghost."""
    rec = _make_reconciler()
    await _apply_with_fake_loop(rec, [_spec(device_id="a")])
    assert "a" in rec._registry.by_camera_id
    await _apply_with_fake_loop(rec, [])
    assert "a" not in rec._registry.by_camera_id


async def test_reconciler_concurrent_applies_serialize():
    """Two simultaneous apply() calls must not double-start or race —
    the internal lock should serialise them."""
    rec = _make_reconciler()
    # Two specs with the same device_id — second call should be no-op
    # because the first already started it.
    spec = _spec(device_id="a")

    async def _patched_apply(specs):
        return await _apply_with_fake_loop(rec, specs)

    diffs = await asyncio.gather(_patched_apply([spec]), _patched_apply([spec]))
    # One started, the other saw it as unchanged.
    started_total = sum(len(d.started) for d in diffs)
    unchanged_total = sum(len(d.unchanged) for d in diffs)
    assert started_total == 1
    assert unchanged_total == 1
