"""Agent DVR OpenAPI 2.0 async client.

Thin httpx-based wrapper. Covers the endpoints needed by the adapter:
- list cameras / capabilities
- snapshot retrieval
- clip retrieval for a time window
- PTZ commands

Full Agent DVR API: https://ispysoftware.github.io/Agent_API/
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


class AgentDVRClientError(Exception):
    """Raised when Agent DVR API returns an error."""


@dataclass
class AgentDVRConfig:
    """Agent DVR connection settings."""

    base_url: str = "http://localhost:8090"
    """Agent DVR HTTP base URL (default port 8090)."""
    username: str | None = None
    password: str | None = None
    timeout_seconds: float = 10.0


class AgentDVRClient:
    """Async HTTP client for Agent DVR.

    Use as an async context manager::

        async with AgentDVRClient(config) as client:
            cams = await client.list_cameras()
    """

    def __init__(self, config: AgentDVRConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> AgentDVRClient:
        auth = None
        if self._config.username and self._config.password:
            auth = httpx.BasicAuth(self._config.username, self._config.password)
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            auth=auth,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise AgentDVRClientError("Client not started (use as async context manager)")
        return self._client

    async def list_cameras(self) -> list[dict[str, Any]]:
        """Return the camera list. Maps to GET /command.cgi?cmd=getcameras."""
        try:
            r = await self.http.get("/command.cgi", params={"cmd": "getCameras"})
            r.raise_for_status()
            data = r.json()
            return data.get("cameras", []) if isinstance(data, dict) else data
        except httpx.HTTPError as e:
            raise AgentDVRClientError(f"list_cameras failed: {e}") from e

    async def get_snapshot(self, camera_id: int | str) -> bytes:
        """Return a JPEG snapshot. Maps to GET /grab.jpg?oid=<camera_id>."""
        try:
            r = await self.http.get("/grab.jpg", params={"oid": camera_id})
            r.raise_for_status()
            return r.content
        except httpx.HTTPError as e:
            raise AgentDVRClientError(f"get_snapshot({camera_id}) failed: {e}") from e

    async def get_clip(
        self,
        camera_id: int | str,
        start_ts: int,
        end_ts: int,
    ) -> bytes:
        """Return a clip for a time window (epoch seconds).

        Maps to GET /video.mp4?oid=<id>&start=<ts>&end=<ts>.
        """
        try:
            r = await self.http.get(
                "/video.mp4",
                params={"oid": camera_id, "start": start_ts, "end": end_ts},
            )
            r.raise_for_status()
            return r.content
        except httpx.HTTPError as e:
            raise AgentDVRClientError(f"get_clip({camera_id}) failed: {e}") from e

    async def slew_ptz(self, camera_id: int | str, preset: str) -> bool:
        """Move PTZ to preset. Maps to GET /command.cgi?cmd=ptzGoto."""
        try:
            r = await self.http.get(
                "/command.cgi",
                params={"cmd": "ptzGoto", "oid": camera_id, "name": preset},
            )
            r.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error("agent_dvr.ptz_failed", camera=camera_id, preset=preset, error=str(e))
            return False
