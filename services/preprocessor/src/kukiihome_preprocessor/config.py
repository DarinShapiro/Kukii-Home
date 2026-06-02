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


def _env_float_map(name: str, default: dict[str, float]) -> dict[str, float]:
    """Parse ``key:val,key2:val2`` into a float map. Empty/unset → default."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return dict(default)
    out: dict[str, float] = {}
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        k, v = pair.split(":", 1)
        try:
            out[k.strip()] = float(v.strip())
        except ValueError:
            continue
    return out or dict(default)


@dataclass(frozen=True)
class PreprocessorConfig:
    """Frozen runtime config snapshot.

    Held by the FastAPI app + the NATS subscriber via dependency
    injection. Re-tunable knobs go through
    :class:`~kukiihome_shared.preprocessor.KnobAdjustment`;
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
    via KUKIIHOME_PREPROCESSOR_EXTERNAL_URL env in production."""

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

    detection_backend: str = "pytorch"
    """One of ``pytorch`` or ``openvino``. OpenVINO requires a
    pre-exported model directory (see
    ``scripts/dev/export_yolo_openvino.py``) and the ``openvino``
    package installed at runtime. Auto-uses the configured
    ``detection_device`` to pick CPU vs GPU (Intel iGPU)."""

    detection_weights: str = "yolo11x.pt"
    """Ultralytics model name or path to .pt file. ``yolo11x`` is
    the production target: ~109 MB, much higher mAP than the nano
    model, sub-30ms on a 4090 (~500-1000ms on CPU). Unit tests pin
    yolo11n explicitly for speed; everywhere else gets the real
    model. We learned the hard way that yolo11n hallucinates "car"
    on a pool surface — false positives erase the value of the
    whole detection pipeline."""

    detection_confidence_min: float = 0.5
    detection_image_size: int = 1280
    """YOLO letterboxes the frame to this square size BEFORE detecting,
    so it sets the detail floor. 640 (the old default) on a 3840x2160
    feed is a ~6x downsample — a distant/small object (a dog at the far
    deck) shrinks below the detector's small-object floor and is missed
    or scored ~0.3. We never want to throw away the 4K we paid to capture
    (decision 2026-06-01: never trade quality for compute). 1280 recovers
    most of it at a sane single-shot cost; the proper full-res answer is
    TILED detection (slice 4K into native-res tiles, detect per-tile,
    merge) — deferred until it can be validated WITH track-id merging on
    clean footage (see planning/validation-findings.md). Raise toward
    1920+ on the GPU box. The crop side already uses the full-res frame
    (body_id._crop_person multiplies the bbox by full w,h), so only this
    detection downsample was leaking 4K detail."""

    detection_device: str | None = None
    """``"cuda:0"`` / ``"cpu"`` / None (auto-pick)."""

    detection_per_class_confidence: dict[str, float] = field(
        default_factory=lambda: {"dog": 0.25, "cat": 0.25, "animal": 0.25}
    )
    """Per-(mapped-kind) confidence floor overrides. Animals read much
    lower than people on steep/distant cameras (a top-down dog ~0.34 vs a
    person 0.74-0.86), so a single people-tuned 0.5 floor makes pets
    invisible — the gate drops the dog before recognition sees a crop, and
    S16 (escaped pet / dog in yard) fails. Keyed by DetectionTag.kind, not
    COCO name. Env override: KUKIIHOME_PREPROCESSOR_DETECTION_PER_CLASS_CONF
    as ``dog:0.25,cat:0.3``."""

    # ─── Face recognition (Phase 10.4) ─────────────────────────────
    face_recognition_enabled: bool = False
    """When True (and backend=rtsp), the FrameBufferBackend gets a
    FaceRecognizer wired in, populating FrameWindow.actor_matches
    (and through correlation, identified_entities) from ArcFace
    embeddings matched against the ActorCache. Opt-in like detection
    so insightface + onnxruntime are only pulled at runtime when
    actually needed."""

    face_model_pack: str = "buffalo_s"
    """InsightFace model bundle name. ``buffalo_s`` (~10MB, fast,
    default) / ``buffalo_l`` (~280MB, slightly higher accuracy) /
    ``antelopev2``."""

    face_match_threshold: float = 0.5
    """Cosine-similarity threshold for a match. ArcFace embeddings
    are L2-normalized; ~0.5 is the InsightFace default working
    point."""

    face_det_confidence_min: float = 0.6
    face_det_size: int = 640
    face_providers: list[str] = field(default_factory=lambda: ["CPUExecutionProvider"])
    """ONNX execution providers in priority order. CPU-only by
    default — set via env to ``CUDAExecutionProvider`` on the 4090
    box or ``OpenVINOExecutionProvider`` on Intel iGPU."""

    # ─── Body re-ID (Phase 10.5.1) ─────────────────────────────────
    body_id_enabled: bool = False
    """When True (and backend=rtsp), the IdentityRouter gets a
    BodyIdPipeline. Off by default — needs an OSNet ONNX model
    available at ``body_id_model_path``."""

    body_id_model_path: str = "/data/kukiihome/models/osnet_x1_0.onnx"
    """Filesystem path to the pre-exported OSNet ONNX. Produced by
    ``scripts/dev/export_osnet_onnx.py``. The recognizer logs an
    error + treats every match as 'no match' if the file is missing,
    so the preprocessor stays up even with a misconfig."""

    body_id_match_threshold: float = 0.6
    """OSNet cosine threshold. Lower than ArcFace's 0.5 because
    OSNet embeddings live in a looser similarity space; tune via
    KnobAdjustment if FP/FN balance is off in production."""

    body_id_providers: list[str] = field(default_factory=lambda: ["CPUExecutionProvider"])

    # ─── CC-ReID — cloth-changing body re-ID (Phase 10.11.5) ───────
    ccreid_enabled: bool = False
    """When True (and backend=rtsp), the IdentityRouter gets a
    CCReIDPipeline. Off by default — needs a CAL/AIM CC-ReID ONNX
    model at ``ccreid_model_path``. Unlike OSNet body-ID, CC-ReID is
    clothes-invariant — a *durable* body anchor that survives outfit
    changes (Epic 10.11.5)."""

    ccreid_model_path: str = "/data/kukiihome/models/ccreid_cal_ltcc.onnx"
    """Filesystem path to the pre-exported CC-ReID ONNX. Produced by
    ``scripts/dev/export_ccreid_onnx.py``. Recognizer logs an error +
    treats every match as 'no match' if the file is missing."""

    ccreid_match_threshold: float = 0.5
    """CC-ReID cosine threshold. Clothes-invariant features sit in a
    tighter space than OSNet's clothing-dominated ones but cross-outfit
    matching is harder; 0.5 is a sane starting point — tune via
    KnobAdjustment once per-camera FP/FN data lands."""

    ccreid_input_height: int = 384
    ccreid_input_width: int = 192
    """CAL/AIM ships 384x192 (HxW) — taller ReID crop than OSNet's
    256x128. See ``scripts/dev/export_ccreid_onnx.py``."""

    ccreid_providers: list[str] = field(default_factory=lambda: ["CPUExecutionProvider"])

    # ─── Pet recognition (Phase 10.5.3) ────────────────────────────
    pet_enabled: bool = False
    """When True (and backend=rtsp), the IdentityRouter gets a
    PetPipeline. Off by default — needs a DINOv2 ONNX model at
    ``pet_model_path``."""

    pet_model_path: str = "/data/kukiihome/models/dinov2_vits14.onnx"
    """Filesystem path to the pre-exported DINOv2 ONNX. Produced by
    ``scripts/dev/export_dinov2_onnx.py``."""

    pet_match_threshold: float = 0.6
    """DINOv2 cosine threshold for 'same animal'. Raise toward 0.7
    if a neighbor's same-breed pet false-matches."""

    pet_providers: list[str] = field(default_factory=lambda: ["CPUExecutionProvider"])

    # ─── Gait recognition (Phase 10.11.6) ──────────────────────────
    gait_enabled: bool = False
    """When True (and backend=rtsp), the IdentityRouter gets a
    GaitPipeline (a TEMPORAL pipeline — runs once per window over a
    per-track frame sequence). Off by default — needs a GaitBase ONNX
    at ``gait_model_path`` + YOLO-seg weights. Durable, distance-robust,
    face-/clothing-independent (Epic 10.11.6)."""

    gait_model_path: str = "/data/kukiihome/models/gaitbase_grew.onnx"
    """Filesystem path to the pre-exported GaitBase ONNX. Produced by
    ``scripts/dev/export_gait_onnx.py``."""

    gait_seg_weights: str = "yolo11x-seg.pt"
    """Ultralytics segmentation weights for silhouette extraction."""

    gait_match_threshold: float = 0.35
    """GaitBase cosine threshold. Gait sims sit lower than face/body;
    0.35 is conservative — tune via KnobAdjustment per camera."""

    gait_min_frames: int = 15
    """Minimum usable silhouettes for a track to produce a gait match —
    a glance-by has no coherent stride."""

    gait_seg_device: str | None = None
    """``"cuda:0"`` / ``"cpu"`` / None for the segmentation model."""

    gait_providers: list[str] = field(default_factory=lambda: ["CPUExecutionProvider"])


def load_from_env() -> PreprocessorConfig:
    """Build a :class:`PreprocessorConfig` from environment variables.

    Defaults are tuned for the dev compose stack — no env vars
    required for a happy ``synthetic``-backend local run. For
    ``rtsp`` backend, set ``KUKIIHOME_PREPROCESSOR_BACKEND=rtsp``
    plus one ``KUKIIHOME_PREPROCESSOR_RTSP_<CAMERA_ID>=<url>`` per
    camera in ``KUKIIHOME_PREPROCESSOR_CAMERAS``.
    """
    cameras = _env_list(
        "KUKIIHOME_PREPROCESSOR_CAMERAS",
        ["front_porch", "driveway_cam"],
    )
    return PreprocessorConfig(
        node_id=os.environ.get("KUKIIHOME_PREPROCESSOR_NODE_ID", "default"),
        http_host=os.environ.get("KUKIIHOME_PREPROCESSOR_HTTP_HOST", "0.0.0.0"),  # noqa: S104
        http_port=_env_int("KUKIIHOME_PREPROCESSOR_HTTP_PORT", 8090),
        external_base_url=os.environ.get(
            "KUKIIHOME_PREPROCESSOR_EXTERNAL_URL", "http://localhost:8090"
        ),
        nats_url=os.environ.get("NATS_URL", "nats://localhost:4222"),
        cameras=cameras,
        camera_rtsp_urls=_collect_camera_urls(cameras),
        backend=os.environ.get("KUKIIHOME_PREPROCESSOR_BACKEND", "synthetic"),
        synthetic_frames_per_second=_env_float("KUKIIHOME_PREPROCESSOR_SYNTHETIC_FPS", 2.0),
        synthetic_buffer_horizon_seconds=_env_float(
            "KUKIIHOME_PREPROCESSOR_BUFFER_HORIZON_S", 300.0
        ),
        rtsp_buffer_horizon_seconds=_env_float("KUKIIHOME_PREPROCESSOR_BUFFER_HORIZON_S", 300.0),
        rtsp_buffer_max_entries_per_camera=_env_int(
            "KUKIIHOME_PREPROCESSOR_BUFFER_MAX_ENTRIES", 1024
        ),
        rtsp_capture_interval_seconds=_env_float("KUKIIHOME_PREPROCESSOR_CAPTURE_INTERVAL_S", 1.0),
        detection_enabled=os.environ.get("KUKIIHOME_PREPROCESSOR_DETECTION", "false").lower()
        in ("1", "true", "yes", "on"),
        detection_backend=os.environ.get("KUKIIHOME_PREPROCESSOR_DETECTION_BACKEND", "pytorch"),
        detection_weights=os.environ.get("KUKIIHOME_PREPROCESSOR_DETECTION_WEIGHTS", "yolo11x.pt"),
        detection_confidence_min=_env_float("KUKIIHOME_PREPROCESSOR_DETECTION_CONF_MIN", 0.5),
        detection_image_size=_env_int("KUKIIHOME_PREPROCESSOR_DETECTION_IMG_SIZE", 1280),
        detection_per_class_confidence=_env_float_map(
            "KUKIIHOME_PREPROCESSOR_DETECTION_PER_CLASS_CONF",
            {"dog": 0.25, "cat": 0.25, "animal": 0.25},
        ),
        detection_device=os.environ.get("KUKIIHOME_PREPROCESSOR_DETECTION_DEVICE") or None,
        face_recognition_enabled=os.environ.get("KUKIIHOME_PREPROCESSOR_FACE", "false").lower()
        in ("1", "true", "yes", "on"),
        face_model_pack=os.environ.get("KUKIIHOME_PREPROCESSOR_FACE_MODEL_PACK", "buffalo_s"),
        face_match_threshold=_env_float("KUKIIHOME_PREPROCESSOR_FACE_MATCH_THRESHOLD", 0.5),
        face_det_confidence_min=_env_float("KUKIIHOME_PREPROCESSOR_FACE_DET_CONF_MIN", 0.6),
        face_det_size=_env_int("KUKIIHOME_PREPROCESSOR_FACE_DET_SIZE", 640),
        face_providers=_env_list(
            "KUKIIHOME_PREPROCESSOR_FACE_PROVIDERS",
            ["CPUExecutionProvider"],
        ),
        body_id_enabled=os.environ.get("KUKIIHOME_PREPROCESSOR_BODY_ID", "false").lower()
        in ("1", "true", "yes", "on"),
        body_id_model_path=os.environ.get(
            "KUKIIHOME_PREPROCESSOR_BODY_ID_MODEL_PATH",
            "/data/kukiihome/models/osnet_x1_0.onnx",
        ),
        body_id_match_threshold=_env_float("KUKIIHOME_PREPROCESSOR_BODY_ID_MATCH_THRESHOLD", 0.6),
        body_id_providers=_env_list(
            "KUKIIHOME_PREPROCESSOR_BODY_ID_PROVIDERS",
            ["CPUExecutionProvider"],
        ),
        ccreid_enabled=os.environ.get("KUKIIHOME_PREPROCESSOR_CCREID", "false").lower()
        in ("1", "true", "yes", "on"),
        ccreid_model_path=os.environ.get(
            "KUKIIHOME_PREPROCESSOR_CCREID_MODEL_PATH",
            "/data/kukiihome/models/ccreid_cal_ltcc.onnx",
        ),
        ccreid_match_threshold=_env_float("KUKIIHOME_PREPROCESSOR_CCREID_MATCH_THRESHOLD", 0.5),
        ccreid_input_height=_env_int("KUKIIHOME_PREPROCESSOR_CCREID_INPUT_HEIGHT", 384),
        ccreid_input_width=_env_int("KUKIIHOME_PREPROCESSOR_CCREID_INPUT_WIDTH", 192),
        ccreid_providers=_env_list(
            "KUKIIHOME_PREPROCESSOR_CCREID_PROVIDERS",
            ["CPUExecutionProvider"],
        ),
        pet_enabled=os.environ.get("KUKIIHOME_PREPROCESSOR_PET", "false").lower()
        in ("1", "true", "yes", "on"),
        pet_model_path=os.environ.get(
            "KUKIIHOME_PREPROCESSOR_PET_MODEL_PATH",
            "/data/kukiihome/models/dinov2_vits14.onnx",
        ),
        pet_match_threshold=_env_float("KUKIIHOME_PREPROCESSOR_PET_MATCH_THRESHOLD", 0.6),
        pet_providers=_env_list(
            "KUKIIHOME_PREPROCESSOR_PET_PROVIDERS",
            ["CPUExecutionProvider"],
        ),
        gait_enabled=os.environ.get("KUKIIHOME_PREPROCESSOR_GAIT", "false").lower()
        in ("1", "true", "yes", "on"),
        gait_model_path=os.environ.get(
            "KUKIIHOME_PREPROCESSOR_GAIT_MODEL_PATH",
            "/data/kukiihome/models/gaitbase_grew.onnx",
        ),
        gait_seg_weights=os.environ.get(
            "KUKIIHOME_PREPROCESSOR_GAIT_SEG_WEIGHTS", "yolo11x-seg.pt"
        ),
        gait_match_threshold=_env_float("KUKIIHOME_PREPROCESSOR_GAIT_MATCH_THRESHOLD", 0.35),
        gait_min_frames=_env_int("KUKIIHOME_PREPROCESSOR_GAIT_MIN_FRAMES", 15),
        gait_seg_device=os.environ.get("KUKIIHOME_PREPROCESSOR_GAIT_SEG_DEVICE") or None,
        gait_providers=_env_list(
            "KUKIIHOME_PREPROCESSOR_GAIT_PROVIDERS",
            ["CPUExecutionProvider"],
        ),
    )


def _collect_camera_urls(cameras: list[str]) -> dict[str, str]:
    """Resolve per-camera RTSP URLs from env vars.

    Convention: ``KUKIIHOME_PREPROCESSOR_RTSP_<CAMERA_ID_UPPER>=<url>``.
    Camera ids with non-uppercase / non-alnum characters are upper-
    cased and have non-alnum chars replaced with ``_``. Missing URLs
    map the camera to the empty string; the RTSP supervisor refuses
    to start a task with an empty URL, surfacing the misconfig.
    """
    out: dict[str, str] = {}
    for cam in cameras:
        key = "KUKIIHOME_PREPROCESSOR_RTSP_" + _camera_id_to_env_suffix(cam)
        out[cam] = os.environ.get(key, "")
    return out


def _camera_id_to_env_suffix(camera_id: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in camera_id).upper()
