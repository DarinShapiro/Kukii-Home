"""Unit tests for the camera-config publisher.

NATS pub side is exercised with a fake nc; credentials providers are
tested in isolation. The full publisher.connect() path is not run
here (would need a live broker — that's the preprocessor side's
integration test).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from kukiihome_ha_agent.camera_config_publisher import (
    CameraConfigPublisher,
    ChainProvider,
    JsonFileProvider,
    construct_rtsp_url,
)


# A minimal DiscoverySpec-shaped stand-in so we don't pull the full
# discovery module + its deps into the test runtime.
@dataclass(frozen=True)
class _SpecStub:
    device_id: str
    camera_entity: str
    friendly_name: str


# ─── URL construction ───────────────────────────────────────────────


def test_construct_rtsp_url_reolink_sub():
    url = construct_rtsp_url(
        vendor="reolink",
        ip="192.168.1.20",
        user="admin",
        password="SimplePass",
        stream="sub",
    )
    assert url == "rtsp://admin:SimplePass@192.168.1.20:554/h264Preview_01_sub"


def test_construct_rtsp_url_dahua_sub():
    url = construct_rtsp_url(
        vendor="dahua",
        ip="192.168.1.21",
        user="admin",
        password="Plain123",
        stream="sub",
    )
    assert url == "rtsp://admin:Plain123@192.168.1.21:554/cam/realmonitor?channel=1&subtype=1"


def test_construct_rtsp_url_escapes_password_special_chars():
    """Passwords with % and @ and / would break URL parsing if
    inserted raw. construct_rtsp_url URL-encodes them."""
    url = construct_rtsp_url(
        vendor="reolink",
        ip="192.168.1.20",
        user="admin",
        password="J9v%8emo",  # has a percent
        stream="sub",
    )
    # %25 = encoded %; the literal % in the password becomes %25
    # in the URL.
    assert "J9v%258emo" in url
    assert "%8e" not in url.split("@")[0]  # no raw %8 in userinfo


def test_construct_rtsp_url_main_stream_uses_main_template():
    url = construct_rtsp_url(
        vendor="reolink",
        ip="192.168.1.20",
        user="admin",
        password="x",
        stream="main",
    )
    assert "h265Preview_01_main" in url


def test_construct_rtsp_url_unknown_vendor_raises():
    with pytest.raises(ValueError, match="Unknown vendor"):
        construct_rtsp_url(
            vendor="hikvision",
            ip="192.168.1.20",
            user="admin",
            password="x",
            stream="sub",
        )


def test_construct_rtsp_url_unknown_stream_raises():
    with pytest.raises(ValueError, match="Unknown stream"):
        construct_rtsp_url(
            vendor="reolink",
            ip="192.168.1.20",
            user="admin",
            password="x",
            stream="ultra",
        )


# ─── JsonFileProvider ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_json_provider_returns_url_for_known_device(tmp_path: Path):
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(
        json.dumps(
            {
                "dev_reolink_front": {
                    "ip": "192.168.1.20",
                    "user": "admin",
                    "password": "ReoPass1",
                    "vendor": "reolink",
                    "stream": "sub",
                }
            }
        )
    )
    provider = JsonFileProvider(creds_path)
    url = await provider.get_rtsp_url(device_id="dev_reolink_front", vendor="reolink")
    assert url == "rtsp://admin:ReoPass1@192.168.1.20:554/h264Preview_01_sub"


@pytest.mark.asyncio
async def test_json_provider_returns_none_for_unknown_device(tmp_path: Path):
    creds_path = tmp_path / "creds.json"
    creds_path.write_text("{}")
    provider = JsonFileProvider(creds_path)
    assert await provider.get_rtsp_url(device_id="ghost", vendor="reolink") is None


@pytest.mark.asyncio
async def test_json_provider_missing_file_returns_none(tmp_path: Path):
    """First-boot case: file doesn't exist yet. Should silently
    return None rather than crashing."""
    provider = JsonFileProvider(tmp_path / "does-not-exist.json")
    assert await provider.get_rtsp_url(device_id="x", vendor="reolink") is None


@pytest.mark.asyncio
async def test_json_provider_malformed_entry_returns_none_with_log(
    tmp_path: Path,
):
    """Entry missing required field — log warning, return None,
    don't crash the publisher."""
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(
        json.dumps(
            {
                "dev_x": {
                    "ip": "192.168.1.20",
                    # missing user + password
                    "vendor": "reolink",
                }
            }
        )
    )
    provider = JsonFileProvider(creds_path)
    assert await provider.get_rtsp_url(device_id="dev_x", vendor="reolink") is None


@pytest.mark.asyncio
async def test_json_provider_uses_param_vendor_when_entry_lacks_one(
    tmp_path: Path,
):
    """If the file entry doesn't declare a vendor, fall back to the
    one the publisher passes in (inferred from spec)."""
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(
        json.dumps(
            {
                "dev_x": {
                    "ip": "192.168.1.20",
                    "user": "admin",
                    "password": "pw",
                }
            }
        )
    )
    provider = JsonFileProvider(creds_path)
    url = await provider.get_rtsp_url(device_id="dev_x", vendor="dahua")
    assert url is not None
    assert "/cam/realmonitor" in url  # dahua template


# ─── ChainProvider ──────────────────────────────────────────────────


class _FixedProvider:
    """Stand-in provider that returns a fixed URL (or None)."""

    def __init__(self, url: str | None) -> None:
        self._url = url

    async def get_rtsp_url(self, *, device_id: str, vendor: str | None) -> str | None:
        _ = device_id, vendor
        return self._url


@pytest.mark.asyncio
async def test_chain_returns_first_non_none():
    chain = ChainProvider(
        [
            _FixedProvider(None),
            _FixedProvider("rtsp://first/match"),
            _FixedProvider("rtsp://second/match"),
        ]
    )
    url = await chain.get_rtsp_url(device_id="x", vendor=None)
    assert url == "rtsp://first/match"


@pytest.mark.asyncio
async def test_chain_returns_none_when_all_providers_return_none():
    chain = ChainProvider([_FixedProvider(None), _FixedProvider(None)])
    url = await chain.get_rtsp_url(device_id="x", vendor=None)
    assert url is None


# ─── CameraConfigPublisher ──────────────────────────────────────────


class _FakeNATS:
    """Minimal stand-in for nats.aio.client.Client. Records published
    (subject, payload) tuples in publish_log."""

    def __init__(self) -> None:
        self.is_connected = True
        self.publish_log: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        self.publish_log.append((subject, payload))


@pytest.mark.asyncio
async def test_publish_configured_with_resolvable_creds():
    pub = CameraConfigPublisher(
        nats_url="nats://unused",
        creds=_FixedProvider("rtsp://admin:x@1.2.3.4:554/sub"),
    )
    pub._nc = _FakeNATS()  # bypass real connect

    spec = _SpecStub(
        device_id="dev_reolink_front",
        camera_entity="camera.reolink_front",
        friendly_name="Reolink Front",
    )
    ok = await pub.publish_configured(spec)
    assert ok is True
    log: list[tuple[str, Any]] = pub._nc.publish_log  # type: ignore[attr-defined]
    assert len(log) == 1
    subject, payload = log[0]
    assert subject == "kukiihome.ha.camera.configured"
    decoded = json.loads(payload)
    assert decoded["action"] == "configured"
    assert decoded["camera_id"] == "dev_reolink_front"
    assert decoded["stream_url"] == "rtsp://admin:x@1.2.3.4:554/sub"
    assert decoded["vendor"] == "reolink"
    assert decoded["stream_protocol"] == "rtsp"


@pytest.mark.asyncio
async def test_publish_configured_without_creds_returns_false_and_does_not_publish():
    pub = CameraConfigPublisher(nats_url="nats://unused", creds=_FixedProvider(None))
    pub._nc = _FakeNATS()

    spec = _SpecStub(
        device_id="dev_unknown",
        camera_entity="camera.unknown",
        friendly_name="Unknown",
    )
    ok = await pub.publish_configured(spec)
    assert ok is False
    assert pub._nc.publish_log == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_publish_removed_sends_removed_event():
    pub = CameraConfigPublisher(nats_url="nats://unused", creds=_FixedProvider(None))
    pub._nc = _FakeNATS()
    await pub.publish_removed("dev_reolink_front")
    log = pub._nc.publish_log  # type: ignore[attr-defined]
    assert len(log) == 1
    subject, payload = log[0]
    assert subject == "kukiihome.ha.camera.removed"
    decoded = json.loads(payload)
    assert decoded["action"] == "removed"
    assert decoded["camera_id"] == "dev_reolink_front"
    assert decoded.get("stream_url") is None


@pytest.mark.asyncio
async def test_publish_before_connect_raises():
    pub = CameraConfigPublisher(nats_url="nats://unused", creds=_FixedProvider("rtsp://x"))
    # _nc is None.
    spec = _SpecStub(device_id="x", camera_entity="camera.x", friendly_name="x")
    with pytest.raises(RuntimeError, match="before connect"):
        await pub.publish_configured(spec)


# ─── StreamSourceAttrProvider ───────────────────────────────────────


class _FakeHAState:
    def __init__(self, attributes: dict) -> None:
        self.attributes = attributes


class _FakeHAClient:
    """Minimal HAClient stand-in returning canned states by entity_id."""

    def __init__(self, states: dict) -> None:
        self._states = states  # entity_id -> _FakeHAState or None

    async def get_state(self, entity_id: str):
        return self._states.get(entity_id)


@pytest.mark.asyncio
async def test_stream_source_provider_returns_rtsp_when_present():
    from kukiihome_ha_agent.camera_config_publisher import StreamSourceAttrProvider

    client = _FakeHAClient(
        {
            "camera.reolink_front": _FakeHAState(
                {"stream_source": "rtsp://admin:pw@1.2.3.4:554/h264Preview_01_sub"}
            )
        }
    )
    provider = StreamSourceAttrProvider(client)
    provider.register(device_id="dev_reo", camera_entity="camera.reolink_front")

    url = await provider.get_rtsp_url(device_id="dev_reo", vendor="reolink")
    assert url == "rtsp://admin:pw@1.2.3.4:554/h264Preview_01_sub"


@pytest.mark.asyncio
async def test_stream_source_provider_rejects_hls_url():
    """User explicitly rejected HLS in the data plane — even when HA
    exposes one we don't use it; chain falls through."""
    from kukiihome_ha_agent.camera_config_publisher import StreamSourceAttrProvider

    client = _FakeHAClient(
        {"camera.x": _FakeHAState({"stream_source": "http://192.168.1.20/stream/index.m3u8"})}
    )
    provider = StreamSourceAttrProvider(client)
    provider.register(device_id="dev_x", camera_entity="camera.x")
    assert await provider.get_rtsp_url(device_id="dev_x", vendor=None) is None


@pytest.mark.asyncio
async def test_stream_source_provider_returns_none_when_attr_missing():
    from kukiihome_ha_agent.camera_config_publisher import StreamSourceAttrProvider

    client = _FakeHAClient({"camera.x": _FakeHAState({"other_attr": "value"})})
    provider = StreamSourceAttrProvider(client)
    provider.register(device_id="dev_x", camera_entity="camera.x")
    assert await provider.get_rtsp_url(device_id="dev_x", vendor=None) is None


@pytest.mark.asyncio
async def test_stream_source_provider_unknown_device_returns_none():
    """Device wasn't registered -> no entity to look up. Return
    None silently so the chain falls through."""
    from kukiihome_ha_agent.camera_config_publisher import StreamSourceAttrProvider

    provider = StreamSourceAttrProvider(_FakeHAClient({}))
    assert await provider.get_rtsp_url(device_id="ghost", vendor=None) is None


@pytest.mark.asyncio
async def test_stream_source_provider_handles_ha_state_read_exception():
    """HAClient raising (network blip) shouldn't kill the publisher —
    just return None and let the chain try the next provider."""
    from kukiihome_ha_agent.camera_config_publisher import StreamSourceAttrProvider

    class _FailingClient:
        async def get_state(self, entity_id: str):
            raise RuntimeError("HA unreachable")

    provider = StreamSourceAttrProvider(_FailingClient())
    provider.register(device_id="dev_x", camera_entity="camera.x")
    assert await provider.get_rtsp_url(device_id="dev_x", vendor=None) is None


@pytest.mark.asyncio
async def test_vendor_inferred_from_entity_name():
    """Reolink in the camera_entity -> vendor=reolink. Affects which
    template is picked + what's stamped on the event."""
    pub = CameraConfigPublisher(nats_url="nats://unused", creds=_FixedProvider("rtsp://x"))
    pub._nc = _FakeNATS()

    for cam_entity, friendly, expected_vendor in [
        ("camera.reolink_front", "Front", "reolink"),
        ("camera.driveway", "Dahua Driveway", "dahua"),
        ("camera.unknown", "Unknown", None),
    ]:
        pub._nc.publish_log.clear()  # type: ignore[attr-defined]
        spec = _SpecStub(
            device_id=f"dev_{cam_entity}",
            camera_entity=cam_entity,
            friendly_name=friendly,
        )
        await pub.publish_configured(spec)
        log = pub._nc.publish_log  # type: ignore[attr-defined]
        decoded = json.loads(log[0][1])
        assert decoded["vendor"] == expected_vendor
