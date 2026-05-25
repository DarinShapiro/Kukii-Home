"""Adapter auto-detection and registry.

At bootstrap, SentiHome's core inspects environment + config to determine which
NVR adapters are configured (via env vars or YAML) and instantiates them. The
registry is the single source of truth for "which adapter handles which camera"
at runtime.

Per §03.5: a single deployment can have multiple adapters active simultaneously
(e.g., 2 cameras via Frigate built-in, 1 via direct RTSP). The registry holds
the camera-to-adapter mapping.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from sentihome_shared.adapter import NVRAdapter
    from sentihome_shared.adapter.base import CameraCapability

logger = structlog.get_logger(__name__)


@dataclass
class AdapterRegistry:
    """Maps camera_id → NVRAdapter instance.

    Populated at bootstrap; queried by the triage worker, action dispatcher,
    and observability layer.
    """

    _adapters: list[NVRAdapter] = field(default_factory=list)
    _by_camera: dict[str, NVRAdapter] = field(default_factory=dict)
    _capabilities: dict[str, CameraCapability] = field(default_factory=dict)

    @property
    def adapters(self) -> list[NVRAdapter]:
        return list(self._adapters)

    @property
    def cameras(self) -> list[str]:
        return list(self._by_camera)

    def register(self, adapter: NVRAdapter) -> None:
        """Add an adapter to the registry."""
        if adapter in self._adapters:
            return
        self._adapters.append(adapter)
        logger.info("adapter_registry.registered", name=adapter.name, mode=adapter.mode.value)

    async def discover_all(self) -> None:
        """For each registered adapter, list cameras and populate the mapping.

        Cameras are uniquely identified by ``camera_id``. If two adapters
        advertise the same camera_id, the first wins (with a warning logged).
        """
        for adapter in self._adapters:
            try:
                cams = await adapter.list_cameras()
            except Exception as e:
                logger.warning(
                    "adapter_registry.list_cameras_failed",
                    adapter=adapter.name,
                    error=str(e),
                )
                continue
            for cam in cams:
                if cam.camera_id in self._by_camera:
                    existing = self._by_camera[cam.camera_id]
                    logger.warning(
                        "adapter_registry.camera_conflict",
                        camera_id=cam.camera_id,
                        existing_adapter=existing.name,
                        new_adapter=adapter.name,
                    )
                    continue
                self._by_camera[cam.camera_id] = adapter
                self._capabilities[cam.camera_id] = cam
                logger.debug(
                    "adapter_registry.camera_added",
                    camera_id=cam.camera_id,
                    adapter=adapter.name,
                    mode=cam.preprocessing_mode.value,
                )

    def adapter_for(self, camera_id: str) -> NVRAdapter | None:
        """Return the adapter handling ``camera_id``, or None."""
        return self._by_camera.get(camera_id)

    def capability_for(self, camera_id: str) -> CameraCapability | None:
        """Return the cached capability for ``camera_id``, or None."""
        return self._capabilities.get(camera_id)

    def mode_summary(self) -> dict[str, int]:
        """Return {mode: camera_count} histogram for observability."""
        from collections import Counter

        counter: Counter[str] = Counter()
        for cap in self._capabilities.values():
            counter[cap.preprocessing_mode.value] += 1
        return dict(counter)


# ─────────────────────────────────────────────────────────────────────
# Bootstrap helpers
# ─────────────────────────────────────────────────────────────────────


def bootstrap_from_env() -> AdapterRegistry:
    """Instantiate adapters based on environment variables.

    Recognized env vars:
        SENTIHOME_ADAPTER_AGENT_DVR_URL — enable AgentDVRAdapter
        SENTIHOME_ADAPTER_FRIGATE_URL  — enable FrigateAdapter
        SENTIHOME_ADAPTER_BLUEIRIS_URL — enable BlueIrisAdapter
        SENTIHOME_ADAPTER_RTSP_DIRECT_CONFIG — JSON-encoded camera list

    Other adapters (Synology, QNAP, UniFi) are similar but their full clients
    are still skeletons; they're enabled in v1.x.
    """
    registry = AdapterRegistry()

    if url := os.environ.get("SENTIHOME_ADAPTER_AGENT_DVR_URL"):
        from sentihome_adapter_agent_dvr import AgentDVRAdapter, AgentDVRConfig

        registry.register(
            AgentDVRAdapter(
                AgentDVRConfig(
                    base_url=url,
                    username=os.environ.get("SENTIHOME_ADAPTER_AGENT_DVR_USERNAME"),
                    password=os.environ.get("SENTIHOME_ADAPTER_AGENT_DVR_PASSWORD"),
                )
            )
        )

    if url := os.environ.get("SENTIHOME_ADAPTER_FRIGATE_URL"):
        from sentihome_adapter_frigate import FrigateAdapter, FrigateConfig

        registry.register(
            FrigateAdapter(
                FrigateConfig(
                    rest_url=url,
                    mqtt_host=os.environ.get("SENTIHOME_ADAPTER_FRIGATE_MQTT_HOST", "localhost"),
                )
            )
        )

    if rtsp_config := os.environ.get("SENTIHOME_ADAPTER_RTSP_DIRECT_CONFIG"):
        import json

        from sentihome_adapter_rtsp_direct import CameraConfig, RTSPDirectAdapter

        try:
            entries = json.loads(rtsp_config)
            cams = [CameraConfig(**entry) for entry in entries]
            registry.register(RTSPDirectAdapter(cameras=cams))
        except Exception as e:
            logger.error("bootstrap.rtsp_direct_config_failed", error=str(e))

    return registry
