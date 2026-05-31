"""Tests for the CameraConfigSubscriber routing logic.

We don't run a real NATS broker here — we exercise the message
handlers directly with hand-built nats.Msg objects. The integration
flow through real NATS is covered by
``test_camera_config_integration.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from kukiihome_preprocessor.camera_config_subscriber import (
    CallbackApplier,
    CameraConfigSubscriber,
    NoOpApplier,
    SupervisorApplier,
)
from kukiihome_shared.preprocessor import (
    SUBJECT_CAMERA_CONFIGURED,
    SUBJECT_CAMERA_REMOVED,
    CameraConfigEvent,
)


@dataclass
class _FakeMsg:
    """Minimal stand-in for nats.aio.msg.Msg."""

    subject: str
    data: bytes


@dataclass
class _RecordingSupervisor:
    """Records add/remove calls without doing real work."""

    added: list[tuple[str, str]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    async def add(self, *, camera_id: str, rtsp_url: str) -> None:
        self.added.append((camera_id, rtsp_url))

    async def remove(self, camera_id: str) -> bool:
        self.removed.append(camera_id)
        return True


def _msg(subject: str, event: CameraConfigEvent) -> _FakeMsg:
    return _FakeMsg(subject=subject, data=event.model_dump_json().encode("utf-8"))


# ─── Routing ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_configured_event_routes_to_applier_on_configured():
    received: list[CameraConfigEvent] = []

    async def cb(ev: CameraConfigEvent) -> None:
        received.append(ev)

    sub = CameraConfigSubscriber("nats://unused", CallbackApplier(on_configured=cb))
    ev = CameraConfigEvent(
        action="configured",
        camera_id="cam_a",
        stream_url="rtsp://example/sub",
        stream_protocol="rtsp",
    )
    await sub._on_configured(_msg(SUBJECT_CAMERA_CONFIGURED, ev))  # type: ignore[arg-type]
    assert received == [ev]


@pytest.mark.asyncio
async def test_removed_event_routes_to_applier_on_removed():
    received: list[CameraConfigEvent] = []

    async def cb(ev: CameraConfigEvent) -> None:
        received.append(ev)

    sub = CameraConfigSubscriber("nats://unused", CallbackApplier(on_removed=cb))
    ev = CameraConfigEvent(action="removed", camera_id="cam_a")
    await sub._on_removed(_msg(SUBJECT_CAMERA_REMOVED, ev))  # type: ignore[arg-type]
    assert received == [ev]


# ─── Defensive paths ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_configured_event_without_stream_url_is_dropped():
    """Without a stream URL, the subscriber should log and skip
    rather than calling the applier (which would crash)."""
    received: list[CameraConfigEvent] = []

    async def cb(ev: CameraConfigEvent) -> None:
        received.append(ev)

    sub = CameraConfigSubscriber("nats://unused", CallbackApplier(on_configured=cb))
    # Bypass Pydantic by constructing a raw message — the actual
    # payload has no stream_url, which is allowed by the schema
    # for the removed case but not for the configured case.
    bad_event = CameraConfigEvent(action="configured", camera_id="cam_a")
    await sub._on_configured(_msg(SUBJECT_CAMERA_CONFIGURED, bad_event))  # type: ignore[arg-type]
    assert received == []


@pytest.mark.asyncio
async def test_malformed_payload_is_logged_not_raised():
    """A garbage payload mustn't take the subscription down."""
    received: list[Any] = []

    async def cb(ev: CameraConfigEvent) -> None:
        received.append(ev)

    sub = CameraConfigSubscriber("nats://unused", CallbackApplier(on_configured=cb))
    bad_msg = _FakeMsg(subject=SUBJECT_CAMERA_CONFIGURED, data=b"not json")
    # Should not raise; just logs the bad payload.
    await sub._on_configured(bad_msg)  # type: ignore[arg-type]
    assert received == []


# ─── SupervisorApplier ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supervisor_applier_translates_configured_to_add():
    sup = _RecordingSupervisor()
    applier = SupervisorApplier(sup)
    ev = CameraConfigEvent(
        action="configured",
        camera_id="cam_a",
        stream_url="rtsp://example/sub",
    )
    await applier.on_configured(ev)
    assert sup.added == [("cam_a", "rtsp://example/sub")]


@pytest.mark.asyncio
async def test_supervisor_applier_translates_removed_to_remove():
    sup = _RecordingSupervisor()
    applier = SupervisorApplier(sup)
    ev = CameraConfigEvent(action="removed", camera_id="cam_a")
    await applier.on_removed(ev)
    assert sup.removed == ["cam_a"]


# ─── NoOpApplier ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_noop_applier_swallows_both_actions():
    """Synthetic mode: applier is wired but does nothing — no side
    effects to observe, just verify it returns cleanly."""
    applier = NoOpApplier()
    await applier.on_configured(
        CameraConfigEvent(action="configured", camera_id="x", stream_url="rtsp://x/s")
    )
    await applier.on_removed(CameraConfigEvent(action="removed", camera_id="x"))


# ─── End-to-end routing through subscriber → supervisor ─────────────


@pytest.mark.asyncio
async def test_subscriber_wired_to_supervisor_via_applier():
    """The cross-class composition: a subscriber holds a
    SupervisorApplier, which wraps a (recording) supervisor.
    Verifies the end-to-end shape callers will use in __main__.py."""
    sup = _RecordingSupervisor()
    sub = CameraConfigSubscriber("nats://unused", SupervisorApplier(sup))

    ev_in = CameraConfigEvent(
        action="configured",
        camera_id="cam_a",
        stream_url="rtsp://example/sub",
    )
    await sub._on_configured(_msg(SUBJECT_CAMERA_CONFIGURED, ev_in))  # type: ignore[arg-type]

    ev_out = CameraConfigEvent(action="removed", camera_id="cam_a")
    await sub._on_removed(_msg(SUBJECT_CAMERA_REMOVED, ev_out))  # type: ignore[arg-type]

    assert sup.added == [("cam_a", "rtsp://example/sub")]
    assert sup.removed == ["cam_a"]
