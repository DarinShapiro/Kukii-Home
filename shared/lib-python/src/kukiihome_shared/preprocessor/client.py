"""HTTP client for the preprocessor's REST surface.

HA-side services use this to issue RPCs at the preprocessor. The
primary call is :meth:`PreprocessorClient.get_frame_window` — the
dispatcher / triage worker invokes this after a TriggerEvent fires
to obtain frames + enrichment for the relevant time interval.

This is the *only* code path the HA-side has into the preprocessor.
It carries no logic — it's pure transport.
"""

from __future__ import annotations

from typing import Self

import httpx

from kukiihome_shared.preprocessor.contracts import (
    ActorEnrollmentEvent,
    FrameWindow,
    KnobAdjustment,
    PreprocessorStatus,
)

DEFAULT_TIMEOUT_SECONDS = 10.0


class PreprocessorClient:
    """Thin async httpx wrapper around the preprocessor REST API.

    Use as an async context manager::

        async with PreprocessorClient("http://inference-box:8090") as cli:
            window = await cli.get_frame_window(
                camera_id="front_porch",
                ts_start=evt.ts - 5.0,
                ts_end=evt.ts + 2.0,
                enrich=True,
            )

    Or as a long-lived object whose lifecycle the caller manages::

        client = PreprocessorClient("http://...")
        try:
            ...
        finally:
            await client.close()
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # rstrip so endpoints aren't double-slashed when a trailing
        # slash slips into base_url.
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ─── primary RPC ────────────────────────────────────────────────

    async def get_frame_window(
        self,
        *,
        camera_id: str,
        ts_start: float,
        ts_end: float,
        enrich: bool = True,
    ) -> FrameWindow:
        """Pull frames + (optional) enrichment for a time window.

        The preprocessor returns whatever it has buffered in
        ``[ts_start, ts_end]`` for ``camera_id``. Empty frames tuple
        if the camera was silent / disconnected during that interval.
        """
        r = await self._client.get(
            f"{self._base_url}/frame_window",
            params={
                "camera_id": camera_id,
                "ts_start": ts_start,
                "ts_end": ts_end,
                "enrich": "true" if enrich else "false",
            },
        )
        r.raise_for_status()
        return FrameWindow.model_validate(r.json())

    # ─── operational RPCs ──────────────────────────────────────────

    async def healthz(self) -> bool:
        """Liveness check. True iff the preprocessor returns 200."""
        r = await self._client.get(f"{self._base_url}/healthz")
        return r.status_code == 200

    async def status(self) -> PreprocessorStatus:
        r = await self._client.get(f"{self._base_url}/status")
        r.raise_for_status()
        return PreprocessorStatus.model_validate(r.json())

    async def tune(self, adjustment: KnobAdjustment) -> None:
        r = await self._client.post(
            f"{self._base_url}/tune",
            json=adjustment.model_dump(mode="json"),
        )
        r.raise_for_status()

    async def enroll_actor(self, event: ActorEnrollmentEvent) -> None:
        """Fall-back direct-enrollment path. Prefer the NATS subject
        :data:`kukiihome_shared.preprocessor.SUBJECT_ACTOR_ENROLLED`
        for production traffic."""
        r = await self._client.post(
            f"{self._base_url}/actors/enroll",
            json=event.model_dump(mode="json"),
        )
        r.raise_for_status()
