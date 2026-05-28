"""Runtime configuration for the recognition preprocessor.

Loaded once at startup. All values come from environment variables
with sensible dev defaults, so the service runs out of the box in
the dev compose stack without an explicit config file.

When production deployment lands, this module will gain a
``load_from_file()`` constructor that reads YAML on the inference
box. For Phase 10.1 skeleton the env-var path is sufficient.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return list(default)
    return [s.strip() for s in raw.split(",") if s.strip()]


@dataclass(frozen=True)
class PreprocessorConfig:
    """Frozen runtime config snapshot.

    Held by the FastAPI app + the NATS subscriber via dependency
    injection. Re-tunable knobs go through
    :class:`~sentihome_shared.preprocessor.KnobAdjustment`;
    everything here is set once at process start.
    """

    # ─── identity ──────────────────────────────────────────────────
    node_id: str = "default"
    """Identifies this preprocessor instance — surfaced on every
    FrameWindow so the consumer knows which box answered."""

    # ─── network ───────────────────────────────────────────────────
    http_host: str = "0.0.0.0"  # noqa: S104 (bind-all is correct inside a container)
    http_port: int = 8090
    """The FastAPI /healthz /status /frame_window /tune /actors/enroll surface."""

    external_base_url: str = "http://localhost:8090"
    """Where OUTSIDE callers can reach this preprocessor. Used to
    construct absolute FrameRef.uri values that the caller can fetch.
    In dev compose, callers go through the service network name; set
    via SENTIHOME_PREPROCESSOR_EXTERNAL_URL env in production."""

    nats_url: str = "nats://localhost:4222"

    # ─── cameras ────────────────────────────────────────────────────
    cameras: list[str] = field(default_factory=list)
    """Camera ids this preprocessor watches. Phase 10.2 will source
    from the canonical topology; for skeleton, read from env."""

    camera_rtsp_urls: dict[str, str] = field(default_factory=dict)
    """Per-camera RTSP URL. Used only when ``backend == "rtsp"``.
    The recommended URL is the H.264 sub-stream (cheap to decode);
    main-stream main-stream pulls for face/plate detail are
    on-demand in Phase 10.4+."""

    # ─── backend selector ──────────────────────────────────────────
    backend: str = "synthetic"
    """One of ``"synthetic"`` or ``"rtsp"``. ``synthetic`` is the
    default and is what CI / unit tests use. ``rtsp`` wires
    :class:`RTSPCaptureSupervisor` + :class:`RTSPFrameBuffer` for
    real cameras."""

    # ─── synthetic frame buffer (CI / unit tests) ──────────────────
    synthetic_frames_per_second: float = 2.0
    """How dense the synthetic-buffer frame timeline is."""

    synthetic_buffer_horizon_seconds: float = 300.0
    """Requests for windows older than this return empty — mirrors
    production rolling-buffer aging."""

    # ─── RTSP rolling buffer (production-ish) ──────────────────────
    rtsp_buffer_horizon_seconds: float = 300.0
    rtsp_buffer_max_entries_per_camera: int = 1024
    rtsp_capture_interval_seconds: float = 1.0
    """Target keyframe cadence. 1.0s matches typical sub-stream GOP
    intervals; lower numbers buy higher temporal resolution at the
    cost of buffer footprint."""

    # ─── Detection (Phase 10.3) ────────────────────────────────────
    detection_enabled: bool = False
    """When True (and backend=rtsp), the FrameBufferBackend gets a
    YOLODetector wired in, populating FrameWindow.detections from
    real frames. Defaults to False so adding ultralytics as a hard
    dep at runtime is opt-in — the service can run without it."""

    detection_weights: str = "yolo11x.pt"
    """Ultralytics model name or path to .pt file. ``yolo11x`` is
    the production target: ~109 MB, much higher mAP than the nano
    model, sub-30ms on a 4090 (~500-1000ms on CPU). Unit tests pin
    yolo11n explicitly for speed; everywhere else gets the real
    model. We learned the hard way that yolo11n hallucinates "car"
    on a pool surface — false positives erase the value of the
    whole detection pipeline."""

    detection_confidence_min: float = 0.5
    detection_image_size: int = 640
    detection_device: str | None = None
    """``"cuda:0"`` / ``"cpu"`` / None (auto-pick)."""


def load_from_env() -> PreprocessorConfig:
    """Build a :class:`PreprocessorConfig` from environment variables.

    Defaults are tuned for the dev compose stack — no env vars
    required for a happy ``synthetic``-backend local run. For
    ``rtsp`` backend, set ``SENTIHOME_PREPROCESSOR_BACKEND=rtsp``
    plus one ``SENTIHOME_PREPROCESSOR_RTSP_<CAMERA_ID>=<url>`` per
    camera in ``SENTIHOME_PREPROCESSOR_CAMERAS``.
    """
    cameras = _env_list(
        "SENTIHOME_PREPROCESSOR_CAMERAS",
        ["front_porch", "driveway_cam"],
    )
    return PreprocessorConfig(
        node_id=os.environ.get("SENTIHOME_PREPROCESSOR_NODE_ID", "default"),
        http_host=os.environ.get("SENTIHOME_PREPROCESSOR_HTTP_HOST", "0.0.0.0"),  # noqa: S104
        http_port=_env_int("SENTIHOME_PREPROCESSOR_HTTP_PORT", 8090),
        external_base_url=os.environ.get(
            "SENTIHOME_PREPROCESSOR_EXTERNAL_URL", "http://localhost:8090"
        ),
        nats_url=os.environ.get("NATS_URL", "nats://localhost:4222"),
        cameras=cameras,
        camera_rtsp_urls=_collect_camera_urls(cameras),
        backend=os.environ.get("SENTIHOME_PREPROCESSOR_BACKEND", "synthetic"),
        synthetic_frames_per_second=_env_float(
            "SENTIHOME_PREPROCESSOR_SYNTHETIC_FPS", 2.0
        ),
        synthetic_buffer_horizon_seconds=_env_float(
            "SENTIHOME_PREPROCESSOR_BUFFER_HORIZON_S", 300.0
        ),
        rtsp_buffer_horizon_seconds=_env_float(
            "SENTIHOME_PREPROCESSOR_BUFFER_HORIZON_S", 300.0
        ),
        rtsp_buffer_max_entries_per_camera=_env_int(
            "SENTIHOME_PREPROCESSOR_BUFFER_MAX_ENTRIES", 1024
        ),
        rtsp_capture_interval_seconds=_env_float(
            "SENTIHOME_PREPROCESSOR_CAPTURE_INTERVAL_S", 1.0
        ),
        detection_enabled=os.environ.get(
            "SENTIHOME_PREPROCESSOR_DETECTION", "false"
        ).lower()
        in ("1", "true", "yes", "on"),
        detection_weights=os.environ.get(
            "SENTIHOME_PREPROCESSOR_DETECTION_WEIGHTS", "yolo11n.pt"
        ),
        detection_confidence_min=_env_float(
            "SENTIHOME_PREPROCESSOR_DETECTION_CONF_MIN", 0.35
        ),
        detection_image_size=_env_int(
            "SENTIHOME_PREPROCESSOR_DETECTION_IMG_SIZE", 640
        ),
        detection_device=os.environ.get(
            "SENTIHOME_PREPROCESSOR_DETECTION_DEVICE"
        )
        or None,
    )


def _collect_camera_urls(cameras: list[str]) -> dict[str, str]:
    """Resolve per-camera RTSP URLs from env vars.

    Convention: ``SENTIHOME_PREPROCESSOR_RTSP_<CAMERA_ID_UPPER>=<url>``.
    Camera ids with non-uppercase / non-alnum characters are upper-
    cased and have non-alnum chars replaced with ``_``. Missing URLs
    map the camera to the empty string; the RTSP supervisor refuses
    to start a task with an empty URL, surfacing the misconfig.
    """
    out: dict[str, str] = {}
    for cam in cameras:
        key = "SENTIHOME_PREPROCESSOR_RTSP_" + _camera_id_to_env_suffix(cam)
        out[cam] = os.environ.get(key, "")
    return out


def _camera_id_to_env_suffix(camera_id: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in camera_id).upper()
