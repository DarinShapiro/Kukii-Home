"""Home Assistant WebSocket + REST client wrapper (Epic 9 #135).

Architecture: docs/architecture/07-tool-layer-mcp.md.

The client is the single low-level seam between SentiHome and HA Core:
- WebSocket subscription for state-changed events (the read path)
- REST for service calls + one-shot state fetches (the write path)
- Long-lived access token auth (or Supervisor proxy + SUPERVISOR_TOKEN)
- Auto-reconnect with exponential backoff on the WebSocket
- Snapshot + delta cache so callers don't re-fetch state on every read

The higher-level :mod:`mcp_tools` module wraps these primitives in the
tool surface documented in §07.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
import websockets

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Client types
# ─────────────────────────────────────────────────────────────────────


@dataclass
class HAClientSettings:
    """Resolved connection settings for the HA Core API."""

    ha_url: str
    """HTTP base, e.g. ``http://homeassistant.local:8123`` or
    ``http://supervisor/core`` for add-on deployments."""
    ha_token: str
    websocket: bool = True
    reconnect_max_seconds: float = 60.0


@dataclass
class HAState:
    """One HA entity's state snapshot."""

    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: str | None = None
    last_updated: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> HAState:
        return cls(
            entity_id=payload["entity_id"],
            state=payload.get("state", "unknown"),
            attributes=payload.get("attributes", {}) or {},
            last_changed=payload.get("last_changed"),
            last_updated=payload.get("last_updated"),
        )


class HAClientError(Exception):
    """Raised on auth / transport failures the caller should surface."""


# Type for the event callback wired by mcp_tools / coordinators.
StateChangeHandler = Callable[[HAState, HAState | None], Awaitable[None]]


# ─────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────


class HAClient:
    """HA Core WebSocket + REST client.

    Construct once at service startup, share across handlers. The websocket
    listener is started by :meth:`start` and runs as a background task that
    self-heals on disconnect.

        async with HAClient(settings) as client:
            await client.subscribe_state_changes(handler)
            ...
    """

    def __init__(
        self,
        settings: HAClientSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
        ws_connector: Callable[..., Any] | None = None,
    ) -> None:
        self._settings = settings
        self._http = http_client or httpx.AsyncClient(
            base_url=settings.ha_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.ha_token}"},
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        self._ws_connector = ws_connector or websockets.connect
        self._ws_task: asyncio.Task[None] | None = None
        self._ws_active: Any | None = None  # live websocket inside the loop
        self._ws_pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._stop_event = asyncio.Event()
        self._ws_msg_id = 0
        self._handlers: list[StateChangeHandler] = []
        self._state_cache: dict[str, HAState] = {}
        self._ready = asyncio.Event()

    # ─── lifecycle ─────────────────────────────────────────────────

    async def __aenter__(self) -> HAClient:
        await self.start()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.stop()

    async def start(self) -> None:
        """Start the background WebSocket listener (no-op if disabled)."""
        if not self._settings.websocket:
            return
        if self._ws_task is not None:
            return
        self._stop_event.clear()
        self._ws_task = asyncio.create_task(self._ws_loop(), name="ha_client_ws")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._ws_task is not None:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None
        await self._http.aclose()

    async def wait_ready(self, *, timeout: float = 10.0) -> None:  # noqa: ASYNC109
        """Wait until the WebSocket has authenticated + subscribed."""
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    # ─── REST: state ───────────────────────────────────────────────

    async def get_states(self) -> list[HAState]:
        """One-shot fetch of all entity states (uses cache when fresh)."""
        if self._state_cache:
            return list(self._state_cache.values())
        resp = await self._http.get("/api/states")
        resp.raise_for_status()
        states = [HAState.from_payload(p) for p in resp.json()]
        self._state_cache = {s.entity_id: s for s in states}
        return states

    async def get_state(self, entity_id: str) -> HAState | None:
        cached = self._state_cache.get(entity_id)
        if cached is not None:
            return cached
        resp = await self._http.get(f"/api/states/{entity_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        state = HAState.from_payload(resp.json())
        self._state_cache[entity_id] = state
        return state

    def snapshot(self) -> dict[str, HAState]:
        """Read-only view of the in-memory state cache."""
        return dict(self._state_cache)

    async def fetch_camera_snapshot(self, entity_id: str) -> bytes:
        """Return the current frame for ``entity_id`` as image bytes.

        Uses HA's REST endpoint ``/api/camera_proxy/{entity_id}`` which
        internally calls the camera integration's ``async_camera_image()``
        method. Content-Type on the response is validated — if it's not
        ``image/*``, we raise rather than silently writing garbage to
        disk (e.g. an ONVIF-configured camera returning its login HTML).

        Live testing against HA 2026.5.3 confirmed there is NO WebSocket
        command for camera image fetch — `camera/get_image` returns
        "Unknown command". The REST path is canonical.

        When this raises HAClientError because of a non-image response,
        the diagnosis is almost always HA-side:
          * The camera entity's integration doesn't implement
            ``async_camera_image()`` correctly
          * Or it's misconfigured (e.g. ONVIF still-image URL requiring
            auth we don't have)
        Fix: in HA, switch the camera to a different integration (e.g.
        the official Reolink integration instead of generic ONVIF) OR
        use SentiHome's ``rtsp-direct`` adapter with the camera's RTSP
        URL + credentials so SentiHome bypasses HA's image-fetch entirely.
        """
        resp = await self._http.get(f"/api/camera_proxy/{entity_id}")
        if resp.status_code >= 400:
            raise HAClientError(
                f"camera_proxy {entity_id} failed: HTTP {resp.status_code} {resp.text[:200]}"
            )
        ctype = resp.headers.get("content-type", "")
        if not ctype.startswith("image/"):
            preview = resp.text[:200].replace("\n", " ")
            raise HAClientError(
                f"camera_proxy {entity_id} returned content-type={ctype!r} "
                f"(expected image/*). HA's integration for this camera entity "
                f"is returning non-image data. First 200 chars: {preview!r}"
            )
        return resp.content

    # ─── REST: service calls ───────────────────────────────────────

    async def sign_url(self, path: str) -> str | None:
        """Ask the SentiHome integration to sign a /api/sentihome/...
        path for use in notification tap actions. Epic 10.8.5.

        Returns the signed URL (with ``?authSig=<jwt>`` appended)
        that the HA Companion app can fetch without needing a
        session cookie — the JWT in the query string substitutes
        for the cookie HA's middleware would otherwise require.

        Returns ``None`` when the integration isn't installed /
        responding. Caller falls back to the unsigned URL (which
        won't auth-bypass but is still informative for debugging).
        """
        try:
            resp = await self._http.get(
                "/api/sentihome/sign", params={"path": path}
            )
        except httpx.HTTPError as e:
            logger.warning("ha_client.sign_url_failed", path=path, error=str(e))
            return None
        if resp.status_code >= 400:
            logger.warning(
                "ha_client.sign_url_http_error",
                path=path,
                status=resp.status_code,
                body=resp.text[:200],
            )
            return None
        try:
            return resp.json().get("signed_url")
        except json.JSONDecodeError:
            return None

    async def call_service(
        self,
        domain: str,
        service: str,
        *,
        entity_id: str | list[str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke an HA service. Returns the (possibly empty) response body."""
        payload: dict[str, Any] = dict(data or {})
        if entity_id is not None:
            payload["entity_id"] = entity_id
        resp = await self._http.post(f"/api/services/{domain}/{service}", json=payload)
        if resp.status_code >= 400:
            raise HAClientError(
                f"service {domain}.{service} failed: HTTP {resp.status_code} {resp.text}"
            )
        try:
            return resp.json() if resp.content else {}
        except json.JSONDecodeError:
            return {"_raw": resp.text}

    async def list_services(self) -> list[dict[str, Any]]:
        """Fetch HA's full service registry via ``GET /api/services``.

        Returns a list of ``{"domain": str, "services": {svc_name: {...}}}``
        entries — exactly the HA REST API's shape. Callers typically
        filter to a single domain (e.g. ``notify``).

        Used by :meth:`HATools.list_notify_services` to populate the
        Notifications card without the user needing to hand-type service
        names.
        """
        resp = await self._http.get("/api/services")
        if resp.status_code >= 400:
            raise HAClientError(
                f"GET /api/services failed: HTTP {resp.status_code} {resp.text[:200]}"
            )
        body = resp.json()
        return body if isinstance(body, list) else []

    # ─── subscriptions ─────────────────────────────────────────────

    def on_state_change(self, handler: StateChangeHandler) -> None:
        """Register a coroutine ``handler(new, old)`` for state-changed events."""
        self._handlers.append(handler)

    async def iter_state_changes(self) -> AsyncIterator[tuple[HAState, HAState | None]]:
        """Async iterator alternative to :meth:`on_state_change`."""
        queue: asyncio.Queue[tuple[HAState, HAState | None]] = asyncio.Queue()

        async def _push(new: HAState, old: HAState | None) -> None:
            await queue.put((new, old))

        self.on_state_change(_push)
        while not self._stop_event.is_set():
            yield await queue.get()

    # ─── internals ─────────────────────────────────────────────────

    async def _ws_loop(self) -> None:
        """Connect → auth → subscribe; reconnect with exponential backoff."""
        backoff = 1.0
        ws_url = self._settings.ha_url.rstrip("/").replace("http", "ws", 1) + "/api/websocket"
        while not self._stop_event.is_set():
            try:
                async with self._ws_connector(ws_url) as ws:
                    await self._ws_authenticate(ws)
                    await self._ws_subscribe(ws)
                    self._ws_active = ws
                    self._ready.set()
                    backoff = 1.0
                    try:
                        await self._ws_consume(ws)
                    finally:
                        self._ws_active = None
                        # Fail any in-flight requests so callers don't hang.
                        for fut in list(self._ws_pending.values()):
                            if not fut.done():
                                fut.set_exception(HAClientError("ws closed mid-request"))
                        self._ws_pending.clear()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._ready.clear()
                logger.warning("ha_client.ws_disconnected", error=str(e), backoff=backoff)
                # Jittered exponential backoff capped at settings.reconnect_max_seconds.
                sleep_for = min(
                    self._settings.reconnect_max_seconds,
                    backoff * (1.0 + random.random() * 0.25),  # noqa: S311
                )
                await asyncio.sleep(sleep_for)
                backoff = min(self._settings.reconnect_max_seconds, backoff * 2)

    async def _ws_authenticate(self, ws: Any) -> None:
        """HA WS auth handshake."""
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            raise HAClientError(f"unexpected WS hello: {hello}")
        await ws.send(json.dumps({"type": "auth", "access_token": self._settings.ha_token}))
        result = json.loads(await ws.recv())
        if result.get("type") != "auth_ok":
            raise HAClientError(f"auth failed: {result}")
        logger.info("ha_client.ws_authenticated", ha_version=result.get("ha_version"))

    async def _ws_subscribe(self, ws: Any) -> None:
        """Subscribe to state_changed events."""
        self._ws_msg_id += 1
        await ws.send(
            json.dumps(
                {
                    "id": self._ws_msg_id,
                    "type": "subscribe_events",
                    "event_type": "state_changed",
                }
            )
        )
        result = json.loads(await ws.recv())
        if not result.get("success"):
            raise HAClientError(f"subscribe failed: {result}")

    async def _ws_consume(self, ws: Any) -> None:
        async for raw in ws:
            if self._stop_event.is_set():
                return
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Route command responses (result with our id) to the
            # pending Future for that request.
            msg_type = msg.get("type")
            msg_id = msg.get("id")
            if msg_type == "result" and msg_id in self._ws_pending:
                fut = self._ws_pending.pop(msg_id)
                if not fut.done():
                    fut.set_result(msg)
                continue

            if msg_type != "event":
                continue
            event = msg.get("event") or {}
            if event.get("event_type") != "state_changed":
                continue
            data = event.get("data") or {}
            new_payload = data.get("new_state")
            old_payload = data.get("old_state")
            if new_payload is None:
                continue
            new = HAState.from_payload(new_payload)
            old = HAState.from_payload(old_payload) if old_payload else None
            self._state_cache[new.entity_id] = new
            for handler in list(self._handlers):
                try:
                    await handler(new, old)
                except Exception:
                    logger.exception("ha_client.handler_failed", entity_id=new.entity_id)
