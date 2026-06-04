"""HTTP client the HA-agent uses to pull recognition from the preprocessor.

Epic 10.9. The preprocessor runs on a separate inference box and
serves a pull-based RPC: ``GET /frame_window?camera_id=&ts_start=
&ts_end=&enrich=true`` returns a :class:`FrameWindow` with detections,
identified_entities, and per-frame ``annotated_uri`` links. When an
HA sensor fires an alert, :class:`AlertEnricher` calls this client to
fetch the recognition for that camera + time window and fold it into
the stored event.

The ``FrameWindow`` / ``IdentifiedEntity`` contracts live in
``kukiihome_shared.preprocessor`` (the seam was put there precisely
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
from kukiihome_shared.preprocessor import FrameWindow

logger = structlog.get_logger(__name__)


class PreprocessorClient:
    """Thin async client over the preprocessor's HTTP surface.

    One long-lived httpx client. ``base_url`` is where the
    preprocessor is reachable from the HA-agent's network (e.g.
    ``http://192.168.68.50:8090`` or ``http://inference.local:8090``).
    """

    def __init__(self, base_url: str, *, timeout: float = 20.0) -> None:
        self._base = base_url.rstrip("/")
        # keepalive_expiry kept *below* uvicorn's ~5s idle-close so we never
        # reuse a connection the server already dropped. Without this, a POST
        # on a stale pooled connection fails (httpx won't auto-retry a
        # non-idempotent request) while GETs silently recover — which surfaced
        # as "label failed but the page loads fine." Paired with a one-shot
        # retry in _post_json. timeout is generous: identity writes are fast,
        # but a weak HA host over LAN shouldn't false-fail on a blip.
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(keepalive_expiry=2.0),
        )

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

    # ─── Identity Review UI (Epic 10 / Build #292) ──────────────────
    #
    # Back the ha-agent's ingress "Review" page. The preprocessor owns the
    # detections.db + frames + recognizer, so these proxy its /identity/*
    # surface. Same fail-soft posture: a sleeping inference box yields an
    # empty list / None, and the page renders an "offline" state instead of
    # erroring.

    async def list_identity_tracks(
        self, *, status: str | None = None, kind: str | None = None, limit: int = 200
    ) -> list[dict]:
        params: dict[str, object] = {"limit": limit}
        if status:
            params["status"] = status
        if kind:
            params["kind"] = kind
        body = await self._get_json("/identity/tracks", params=params)
        return body.get("tracks", []) if body else []

    async def list_identity_subjects(self) -> list[dict]:
        body = await self._get_json("/identity/subjects")
        return body.get("subjects", []) if body else []

    async def label_track(self, payload: dict) -> dict | None:
        """POST /identity/label — label a track → enroll + retroactive resolve.
        Returns the response dict, or None on failure."""
        return await self._post_json("/identity/label", payload)

    async def resolve_identity(self, *, event_id: str | None = None) -> dict | None:
        return await self._post_json("/identity/resolve", {"event_id": event_id})

    async def reject_track(self, event_id: str, track_id: str) -> dict | None:
        """Split-to-unknown: clear a track's (wrong) resolution."""
        return await self._post_json(
            "/identity/reject", {"event_id": event_id, "track_id": track_id}
        )

    async def merge_subjects(self, from_id: str, into_id: str) -> dict | None:
        """Merge two labels that are the same subject (``from`` → ``into``)."""
        return await self._post_json(
            "/identity/subjects/merge", {"from_id": from_id, "into_id": into_id}
        )

    async def fetch_track_thumb(self, event_id: str, track_id: str) -> bytes | None:
        """GET the cropped track thumbnail bytes for the Review page to re-serve."""
        return await self._fetch_bytes(f"/identity/tracks/{event_id}/{track_id}/thumb.jpg")

    async def fetch_track_clip(self, event_id: str, track_id: str) -> bytes | None:
        """GET the animated track-clip GIF bytes for the track-detail page."""
        return await self._fetch_bytes(f"/identity/tracks/{event_id}/{track_id}/clip.gif")

    async def get_track_detail(self, event_id: str, track_id: str) -> dict | None:
        """GET the track-detail JSON (summary + ranked candidates)."""
        return await self._get_json(f"/identity/tracks/{event_id}/{track_id}/detail")

    async def _fetch_bytes(self, path: str) -> bytes | None:
        try:
            resp = await self._http.get(f"{self._base}{path}")
        except httpx.HTTPError as e:
            logger.info("preprocessor_client.fetch_failed", path=path, error=str(e))
            return None
        if resp.status_code >= 400 or not resp.content:
            return None
        return resp.content

    async def _get_json(self, path: str, *, params: dict | None = None) -> dict | None:
        try:
            resp = await self._http.get(f"{self._base}{path}", params=params)
        except httpx.HTTPError as e:
            logger.info("preprocessor_client.unreachable", path=path, error=str(e))
            return None
        if resp.status_code >= 400:
            return None
        try:
            return resp.json()
        except Exception:
            return None

    async def _post_json(self, path: str, payload: dict) -> dict | None:
        # One retry on a transport error: closes the stale-keep-alive race for
        # POSTs (a pooled connection the server idle-closed). Safe to retry —
        # the identity writes (label/reject/resolve) are idempotent. A genuine
        # 4xx/5xx is NOT retried (it's a real rejection, not a blip).
        last_err: Exception | None = None
        for _attempt in range(2):
            try:
                resp = await self._http.post(f"{self._base}{path}", json=payload)
            except httpx.TransportError as e:
                last_err = e
                continue
            except httpx.HTTPError as e:
                logger.info("preprocessor_client.post_failed", path=path, error=str(e))
                return None
            if resp.status_code >= 400:
                logger.warning(
                    "preprocessor_client.post_http_error",
                    path=path, status=resp.status_code, body=resp.text[:200],
                )
                return None
            try:
                return resp.json()
            except Exception:
                return None
        logger.info("preprocessor_client.post_failed", path=path, error=str(last_err))
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
