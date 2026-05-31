"""NVRAdapter abstract base class — the contract for every NVR adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class PreprocessingMode(StrEnum):
    """Where preprocessing for this adapter runs. See §03.5."""

    NATIVE = "native"
    BUILT_IN = "built-in"
    SERVICE = "service"
    DIRECT = "direct"


class AdapterError(Exception):
    """Base class for adapter errors."""


class UnsupportedCapability(AdapterError):
    """Raised when calling a method the adapter doesn't support (e.g. PTZ).

    Callers should advertise capabilities via ``list_cameras`` and avoid
    calling unsupported features; this is a defensive backstop.
    """


@dataclass(frozen=True)
class CameraCapability:
    """Minimal capability descriptor returned by ``list_cameras``.

    Maps 1:1 to NVRCapability JSON schema (shared/schemas/common/nvr-capability.schema.json).
    Defined as a Python dataclass for ergonomic access; serialize via dataclasses.asdict()
    when crossing process boundaries.
    """

    camera_id: str
    name: str | None = None
    preprocessing_mode: PreprocessingMode = PreprocessingMode.SERVICE
    has_on_camera_ai: bool = False
    supported_events: tuple[str, ...] = ()
    max_resolution: tuple[int, int] | None = None
    fps: int | None = None
    ptz: bool = False
    audio: bool = False
    stream_profiles: tuple[str, ...] = ()
    rtsp_url: str | None = None


@dataclass(frozen=True)
class FramePointer:
    """A single frame entry in a FrameWindow result."""

    uri: str
    timestamp: datetime
    width: int | None = None
    height: int | None = None


@dataclass
class FrameWindow:
    """Result of ``nvr.get_frame_window`` — frames + preprocessing metadata."""

    camera_id: str
    ts_start: datetime
    ts_end: datetime
    frames: list[FramePointer]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MotionEvent:
    """Single push notification from ``subscribe_motion_events``."""

    camera_id: str
    timestamp: datetime
    event_type: str  # "motion" | "person" | "vehicle" | ...
    confidence: float | None = None
    bbox: tuple[float, float, float, float] | None = None
    raw: dict[str, Any] | None = None  # adapter-specific extras


# ─────────────────────────────────────────────────────────────────────
# The contract
# ─────────────────────────────────────────────────────────────────────


class NVRAdapter(ABC):
    """Abstract base class every NVR adapter inherits from.

    Adapters live under ``adapters/nvr-*`` and translate between a specific
    NVR platform (Agent DVR, Frigate, Blue Iris, Synology, QNAP, UniFi,
    or direct RTSP from cameras) and this unified contract.

    Kukii-Home core consumes adapters through this interface and is agnostic
    to which platform is underneath (see §03.5).
    """

    # ─────────────────────────────────────────────────────────────────
    # Identity
    # ─────────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """A stable adapter identifier, e.g. ``"adapter-frigate"``."""

    @property
    @abstractmethod
    def mode(self) -> PreprocessingMode:
        """The preprocessing mode this adapter operates in."""

    # ─────────────────────────────────────────────────────────────────
    # Discovery
    # ─────────────────────────────────────────────────────────────────

    @abstractmethod
    async def list_cameras(self) -> list[CameraCapability]:
        """Enumerate cameras + capabilities exposed by the underlying NVR."""

    # ─────────────────────────────────────────────────────────────────
    # Frame access (hot path)
    # ─────────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_frame_window(
        self,
        camera_id: str,
        ts_start: datetime,
        ts_end: datetime,
        *,
        with_metadata: bool = True,
    ) -> FrameWindow:
        """Return frames + preprocessing metadata for a time window."""

    # ─────────────────────────────────────────────────────────────────
    # Event subscription (push-driven ingress)
    # ─────────────────────────────────────────────────────────────────

    @abstractmethod
    async def subscribe_motion_events(
        self,
        camera_id: str | None = None,
    ) -> AsyncIterator[MotionEvent]:
        """Async iterator of motion / on-camera AI events.

        Pass ``camera_id=None`` to subscribe to all cameras.
        """
        # Subclasses must `yield` MotionEvents. Tooling requires the
        # function body for AsyncIterator to be a generator; type-only.
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]

    # ─────────────────────────────────────────────────────────────────
    # On-demand enrichment (rarely used outside the standard flow)
    # ─────────────────────────────────────────────────────────────────

    async def enrich_frame(
        self,
        camera_id: str,
        frame_uri: str,
        *,
        models: tuple[str, ...] = ("yolo", "face", "reid"),
    ) -> dict[str, Any]:
        """Optional: run on-demand enrichment on a single frame.

        Default implementation raises ``UnsupportedCapability``. Built-in
        and service-mode adapters typically override.
        """
        raise UnsupportedCapability(f"{self.name} does not support on-demand enrich_frame()")

    # ─────────────────────────────────────────────────────────────────
    # Direct stream access (attention modes)
    # ─────────────────────────────────────────────────────────────────

    async def get_stream_url(self, camera_id: str, profile: str = "main") -> str:
        """Return an RTSP URL for live frame sampling.

        Default implementation raises ``UnsupportedCapability``.
        """
        raise UnsupportedCapability(f"{self.name} does not expose direct RTSP URLs")

    # ─────────────────────────────────────────────────────────────────
    # Observation actions (PTZ, profile switch)
    # ─────────────────────────────────────────────────────────────────

    async def slew_ptz(self, camera_id: str, preset_id: str) -> bool:
        """Move a PTZ camera to a preset. Default: not supported."""
        raise UnsupportedCapability(f"{self.name} does not support PTZ")

    async def switch_profile(self, camera_id: str, profile: str) -> bool:
        """Switch a camera's active stream profile. Default: not supported."""
        raise UnsupportedCapability(f"{self.name} does not support profile switching")

    # ─────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize external connections. Default: no-op."""
        return None

    async def stop(self) -> None:
        """Clean shutdown. Default: no-op."""
        return None
