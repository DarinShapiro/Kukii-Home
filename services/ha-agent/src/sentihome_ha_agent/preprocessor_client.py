"""HTTP client the HA-agent uses to pull recognition from the preprocessor.

Epic 10.9. The preprocessor runs on a separate inference box and
serves a pull-based RPC: ``GET /frame_window?camera_id=&ts_start=
&ts_end=&enrich=true`` returns a :class:`FrameWindow` with detections,
identified_entities, and per-frame ``annotated_uri`` links. When an
HA sensor fires an alert, :class:`AlertEnricher` calls this client to
fetch the recognition for that camera + time window and fold it into
the stored event.

The ``FrameWindow`` / ``IdentifiedEntity`` contracts live in
``sentihome_shared.preprocessor`` (the seam was put there precisely
so both sides can speak it without the HA-agent depending on the
preprocessor package).

Everything here degrades to ``None`` on failure — a sleeping
inference box or a network blip must never break alert recording or
notification. The alert just keeps its HA snapshot + rule-that-fired.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx
import structlog
from sentihome_shared.preprocessor import FrameWindow

logger = structlog.get_logger(__name__)


class PreprocessorClient:
    """Thin async client over the preprocessor's HTTP surface.

    One long-lived httpx client. ``base_url`` is where the
    preprocessor is reachable from the HA-agent's network (e.g.
    ``http://192.168.68.50:8090`` or ``http://inference.local:8090``).
    """

    def __init__(self, base_url: str, *, timeout: float = 8.0) -> None:
        self._base = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=3.0))

    async def close(self) -> None:
        await self._http.aclose()

    async def get_frame_window(
        self,
        *,
        camera_id: str,
        ts_start: float,
        ts_end: float,
        enrich: bool = True,
    ) -> FrameWindow | None:
        """Pull the enriched frame window for ``camera_id`` over
        ``[ts_start, ts_end]`` (unix seconds). Returns the parsed
        :class:`FrameWindow`, or ``None`` on any transport/parse
        failure (preprocessor down, timeout, bad payload)."""
        params = {
            "camera_id": camera_id,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "enrich": str(enrich).lower(),
        }
        try:
            resp = await self._http.get(f"{self._base}/frame_window", params=params)
        except httpx.HTTPError as e:
            logger.info(
                "preprocessor_client.unreachable",
                camera_id=camera_id,
                error=str(e),
                hint="inference box offline or unreachable; alert keeps HA snapshot",
            )
            return None
        if resp.status_code >= 400:
            logger.warning(
                "preprocessor_client.frame_window_http_error",
                camera_id=camera_id,
                status=resp.status_code,
                body=resp.text[:200],
            )
            return None
        try:
            return FrameWindow.model_validate(resp.json())
        except Exception as e:
            logger.warning(
                "preprocessor_client.frame_window_parse_failed",
                camera_id=camera_id,
                error=str(e),
            )
            return None

    async def fetch_frame_image(self, uri: str) -> bytes | None:
        """Fetch annotated/raw frame bytes for a FrameRef URI.

        ``uri`` is a ``FrameRef.uri`` / ``annotated_uri`` — typically
        an absolute URL built from the preprocessor's
        ``external_base_url``. That base may be misconfigured (e.g.
        ``localhost``) from the HA-agent's vantage point, so we use
        only the PATH from the URI and re-join it with our own
        ``base_url``. Returns ``None`` on failure."""
        if not uri:
            return None
        path = urlsplit(uri).path or uri
        try:
            resp = await self._http.get(f"{self._base}{path}")
        except httpx.HTTPError as e:
            logger.info("preprocessor_client.frame_fetch_failed", uri=uri, error=str(e))
            return None
        if resp.status_code >= 400 or not resp.content:
            return None
        return resp.content
