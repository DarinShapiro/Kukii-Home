"""Camera-native motion as the gate (Dahua eventManager).

Our MOG2 over-triggers on benign movers (a pool cleaner's drifting hose,
water circulation) because it's a naive background-subtractor. The camera's
own motion/IVS detector has size/persistence filtering and cleanly ignores
those while still firing on a person — validated on the pool cam (0 events on
the idle hose over 35s; VideoMotion;action=Start the instant a person moved).

This is the pragmatic gate: subscribe to the camera's event stream, map
`Code=VideoMotion;action=Start|Stop` to a motion-active flag, and let the
capture loop use that for `has_motion` instead of MOG2. NOT the robust
long-term answer (it's vendor-specific Dahua CGI, single-signal) — the
two-heuristic corroboration + per-camera tuning lives in #291. This gets us
out of the MOG2-false-trigger quagmire now.

Threaded (holds a long-lived HTTP multipart stream); the capture decode
thread reads `.active` (an atomic bool) with no lock.
"""

from __future__ import annotations

import threading
import time
import urllib.request
from urllib.parse import unquote, urlsplit

import structlog

logger = structlog.get_logger(__name__)


def event_url_from_rtsp(rtsp_url: str) -> tuple[str, str, str]:
    """Derive (event_attach_url, user, password) from a camera's RTSP URL.

    rtsp://user:pass@host:port/path -> http://host/cgi-bin/eventManager.cgi?...
    Credentials are URL-decoded (the RTSP URL carries them percent-encoded).
    """
    parts = urlsplit(rtsp_url)
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")
    host = parts.hostname or ""
    url = (
        f"http://{host}/cgi-bin/eventManager.cgi?action=attach&codes=%5BVideoMotion%5D&heartbeat=5"
    )
    return url, user, password


def parse_motion_line(line: str) -> bool | None:
    """Map one event-stream line to a motion-active transition.

    Returns True on `action=Start`, False on `action=Stop`, None otherwise
    (heartbeats, boundaries, data lines). Only VideoMotion codes count.
    """
    if "Code=VideoMotion" not in line:
        return None
    if "action=Start" in line:
        return True
    if "action=Stop" in line:
        return False
    return None


class DahuaMotionWatcher:
    """Holds the Dahua eventManager attach stream and tracks motion-active.

    `.active` is the gate signal. Self-heals: if the stream drops, it
    reconnects with backoff. If no Start/Stop is seen for `stale_after`
    seconds while supposedly active (missed Stop), it self-clears.
    """

    def __init__(
        self,
        *,
        event_url: str,
        user: str,
        password: str,
        stale_after: float = 60.0,
        connect_timeout: float = 10.0,
    ) -> None:
        self._url = event_url
        self._user = user
        self._password = password
        self._stale_after = stale_after
        self._connect_timeout = connect_timeout
        self._active = False
        self._last_active_ts = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        # Self-clear a stuck "active" if we somehow missed the Stop event.
        if self._active and (time.time() - self._last_active_ts) > self._stale_after:
            self._active = False
        return self._active

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="dahua-motion", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _opener(self) -> urllib.request.OpenerDirector:
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, self._url, self._user, self._password)
        return urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                resp = self._opener().open(self._url, timeout=self._connect_timeout)
                logger.info("camera_motion.attached", url=self._url)
                backoff = 1.0
                for raw in resp:  # iterate the multipart stream line-by-line
                    if self._stop.is_set():
                        break
                    transition = parse_motion_line(raw.decode("latin-1", "replace"))
                    if transition is True:
                        self._active = True
                        self._last_active_ts = time.time()
                    elif transition is False:
                        self._active = False
                    elif self._active:
                        # heartbeat while active counts as liveness
                        self._last_active_ts = time.time()
            except Exception as e:  # connection dropped / timeout / auth blip
                if not self._stop.is_set():
                    logger.warning("camera_motion.stream_error", error=str(e), backoff_s=backoff)
                    self._active = False
                    self._stop.wait(backoff)
                    backoff = min(30.0, backoff * 2)
