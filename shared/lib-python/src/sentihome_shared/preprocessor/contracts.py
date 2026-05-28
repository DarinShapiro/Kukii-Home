"""Wire contracts crossing the preprocessor process boundary.

The preprocessor is a pull-based service. Triage / dispatcher
on the HA side calls ``GET /frame_window`` upon a camera event and
gets back a :class:`FrameWindow`. The preprocessor never broadcasts
detection events — clients only ever ask.

Two flows still use NATS (broadcast / fire-and-forget), and they're
both INBOUND to the preprocessor:

* :class:`ActorEnrollmentEvent` — memory broadcasts when a KnownActor
  changes; preprocessor subscribes to refresh its identity cache.

Plus REST RPCs the HA side calls on the preprocessor:

* ``GET /frame_window``  → :class:`FrameWindow` (the primary RPC)
* ``GET /status``        → :class:`PreprocessorStatus`
* ``GET /healthz``       → liveness
* ``POST /tune``         → :class:`KnobAdjustment` (feedback loop)
* ``POST /actors/enroll`` → :class:`ActorEnrollmentEvent`
                            (fall-back; canonical path is NATS)

Versioning: every top-level model carries a ``schema_version`` field
(currently always ``"v1"``). Bump on breaking changes; run both for
the migration window.

Strict ``extra="forbid"``: a producer adding an unknown field surfaces
during testing rather than silently being dropped on the consumer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ─── Substructures: detections, actor matches, frame refs ───────────


class DetectionTag(_Strict):
    """One object the detector found in a frame.

    Production: YOLO11x. The preprocessor returns multiple
    DetectionTags per frame_window — one per (object, frame) pair —
    unless tracking collapsed them by track_id.
    """

    kind: str
    """Object class (``person``, ``vehicle``, ``dog``, ``cat``,
    ``package``, etc.)."""

    confidence: float = Field(ge=0.0, le=1.0)

    bbox: tuple[float, float, float, float]
    """``(x1, y1, x2, y2)`` in normalized image coordinates (0..1)."""

    frame_ts: float
    """Unix-seconds timestamp of the frame this detection came from.
    Lets the caller correlate a detection back to a specific frame
    in the window."""

    track_id: str | None = None
    """Cross-frame tracking handle. None for unstable tracks."""


class ActorMatch(_Strict):
    """One identity attribution against a KnownActor.

    Produced by the face pipeline (ArcFace), the pet pipeline (DINOv2
    centroid match), or the vehicle pipeline (plate match via
    fastALPR).
    """

    actor_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    match_method: Literal["face_arcface", "pet_dinov2", "plate_lpr"]

    frame_ts: float
    """Frame the match was observed on."""

    track_id: str | None = None
    """If from a tracked detection, the same ``track_id`` as the
    corresponding DetectionTag."""


class FrameRef(_Strict):
    """A handle to one buffered frame.

    ``uri`` points to the actual pixels (object-store path on the
    inference box). For dev/skeleton the URI may be a synthetic
    placeholder like ``synthetic://cam_id/ts``; the test consumer
    doesn't need to dereference it.
    """

    ts: float
    uri: str
    annotated_uri: str | None = None
    """Optional URI to a marked-up version of this frame with bounding
    boxes drawn around RECOGNIZED entities only (no boxes for unknown
    persons, unknown vehicles, etc.). Set when there's at least one
    :class:`IdentifiedEntity` for this frame; ``None`` when the frame
    is wholly anonymous. Consumer fetches this URI to get the
    VLM-ready annotated JPEG."""

    width: int | None = None
    height: int | None = None
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    """Optional sharpness / lighting / occlusion composite from the
    preprocessor's quality assessment. None when the preprocessor
    didn't run the assessment (enrich=False)."""


class IdentifiedEntity(_Strict):
    """Pre-correlated identity claim for one entity in one frame.

    The structure the VLM consumes for grounding: a bounding box, a
    confirmed identity (with friendly name), and the confidence of
    both the detection and the identification. Only emitted for
    entities the preprocessor can name — unknown person / unknown
    dog / unknown vehicle do NOT produce IdentifiedEntities, because
    a labeled "unknown" box adds noise without adding signal (the
    VLM already sees the raw pixels).

    Produced by :class:`RTSPFrameBuffer.get_window` by joining
    :class:`DetectionTag` and :class:`ActorMatch` on shared
    ``track_id``, then resolving ``actor_id`` to a friendly name
    via the preprocessor's :class:`ActorCache`.
    """

    frame_ts: float

    kind: Literal["person", "dog", "cat", "vehicle"]
    """The detection class. Constrained because the markup pipeline
    only cares about classes that have a corresponding identity
    pipeline."""

    actor_id: str
    actor_name: str
    """Friendly display name, e.g. ``"Alice"`` / ``"Rex"`` /
    ``"Bob's truck"``. Resolved from the ActorCache at correlation
    time."""

    bbox: tuple[float, float, float, float]
    """``(x1, y1, x2, y2)`` in normalized [0, 1] image coords —
    matches :attr:`DetectionTag.bbox`."""

    detection_confidence: float = Field(ge=0.0, le=1.0)
    """How confident YOLO was that something is here at all."""

    identity_confidence: float = Field(ge=0.0, le=1.0)
    """How confident the identity pipeline was that this is the
    specific actor. The markup pipeline's visual style branches
    on this: solid green box at >= 0.85, dashed yellow 0.6-0.85,
    not annotated < 0.6."""

    identity_method: Literal["face_arcface", "pet_dinov2", "plate_lpr"]

    track_id: str | None = None


# ─── Primary RPC payload: FrameWindow ────────────────────────────────


class FrameWindow(_Strict):
    """Returned by ``GET /frame_window``.

    Carries the buffered frames in ``[ts_start, ts_end]`` for one
    camera, plus optional enrichment (detections + actor matches)
    when the caller asked for it.

    Intentionally event-agnostic: the preprocessor doesn't know what
    TriggerEvent or alert this corresponds to. The caller maps this
    to an EnrichedEvent on the HA side by attaching event_id /
    trace_id / privacy_tier.
    """

    schema_version: Literal["v1"] = "v1"

    camera_id: str
    ts_start: float
    ts_end: float
    """The window the caller asked for. Returned verbatim so the
    caller can sanity-check what they got vs. what they requested."""

    preprocessor_node_id: str = "default"
    """Identifies which preprocessor instance produced this — useful
    in multi-inference-box deployments."""

    frames: tuple[FrameRef, ...] = ()
    """Frames inside the window. May be empty if the camera was
    silent / disconnected during that interval."""

    detections: tuple[DetectionTag, ...] = ()
    """Aggregated detections across all returned frames. Empty when
    the caller passed ``enrich=False``."""

    actor_matches: tuple[ActorMatch, ...] = ()
    """Aggregated identity matches. Empty when ``enrich=False`` or
    when no KnownActor was confidently matched."""

    identified_entities: tuple[IdentifiedEntity, ...] = ()
    """Pre-correlated identity claims suitable for VLM grounding +
    visual markup. The preprocessor builds these by joining
    ``detections`` and ``actor_matches`` on ``track_id`` and
    resolving the actor's friendly name. Consumers (VLM-router,
    UI) read this in preference to walking ``detections`` +
    ``actor_matches`` separately.

    Empty when no entity in the window was recognized — including
    the common case where YOLO sees a person/vehicle/pet but the
    identity pipeline didn't match it to any known actor. That's
    the correct quiet behavior; the VLM still sees the raw frame
    and can reason about the unknown entity from pixels alone."""

    enrichment_mode: Literal["frames_only", "enriched"] = "enriched"

    enrichment_latency_ms: int = 0
    """How long the preprocessor spent on the request, end-to-end.
    Useful for the latency-capture / observability layer."""


# ─── Status + tuning RPCs ────────────────────────────────────────────


class PreprocessorStatus(_Strict):
    """Health + queue-depth snapshot returned by ``GET /status``."""

    schema_version: Literal["v1"] = "v1"

    healthy: bool
    uptime_seconds: float
    model_versions: dict[str, str] = Field(default_factory=dict)
    """e.g. ``{"yolo": "11x-2.1", "face": "arcface-r100-v3", ...}``"""

    cameras_active: int
    cameras_total: int
    frame_windows_served_total: int
    """Cumulative count of /frame_window calls answered since
    process start. Useful for spotting traffic patterns + sizing
    the buffer."""

    actors_cached: int


class KnobAdjustment(_Strict):
    """A request to retune one preprocessor knob.

    Posted to ``POST /tune`` by the feedback-loop subsystem when
    VLM-reported quality issues indicate a pipeline knob is mis-set.
    """

    schema_version: Literal["v1"] = "v1"

    knob_id: str
    """Dotted path, e.g. ``face.match_threshold`` or
    ``yolo.confidence_min``."""

    new_value: float | str | bool
    rationale: str = ""

    scope_camera_id: str | None = None
    """If set, applies only to this camera; None = global."""


# ─── CameraConfig — INBOUND broadcast (ha-agent → preprocessor) ─────


class CameraConfigEvent(_Strict):
    """Published by ha-agent whenever a camera's stream configuration
    changes (newly discovered, URL refreshed, user-disabled, removed).

    The preprocessor's :class:`CameraConfigSubscriber` consumes these
    and dynamically (re)starts the per-camera RTSP capture task.
    Eliminates the env-var "type your RTSP URLs" step — the
    HA-side already knows about cameras (via the HA add-on's
    discovery layer), so the preprocessor learns from there.

    The ``stream_url`` may be either:

    * Raw RTSP (``rtsp://user:pass@host:port/path``) — fast, low
      latency, but only works for integrations that expose the URL.
    * HA HLS proxy (``http://homeassistant:8123/api/hls/.../playlist.m3u8``)
      — universal across integrations, ~5s latency. Obtained from HA's
      WebSocket ``camera/stream`` command.

    PyAV decodes both — the capture task doesn't care which.
    """

    schema_version: Literal["v1"] = "v1"

    action: Literal["configured", "removed"]

    camera_id: str
    """Stable identifier across the SentiHome topology. Matches the
    same camera_id the topology + memory + events use."""

    stream_url: str | None = None
    """Required when action="configured", omitted when action="removed".
    The URL the preprocessor will open for capture."""

    stream_protocol: Literal["rtsp", "hls"] | None = None
    """Hint about what PyAV will see. The capture task uses
    ``rtsp_transport=tcp`` for RTSP; HLS uses default HTTP options.
    Optional; capture task can fall back to URL-scheme inspection."""

    vendor: str | None = None
    """e.g. ``"reolink"``, ``"dahua"``, ``"unifi"``. Informational —
    surfaces in logs / status. Not used for routing."""

    sub_stream: bool = True
    """True if this is the low-res sub-stream (cheap to decode,
    sufficient for motion + general detection). False = main stream.
    Phase 10.4 will use this to decide whether to also open a
    parallel main-stream session for face/plate detail."""

    refresh_after_seconds: float | None = None
    """For HLS streams whose URL contains a short-lived token: the
    expected lifetime. ha-agent should republish before this elapses.
    None for RTSP (no token refresh needed)."""


# ─── ActorEnrollment — INBOUND broadcast (memory → preprocessor) ─────


class ActorEnrollmentEvent(_Strict):
    """Published by the memory service when a KnownActor is enrolled,
    updated, or deactivated.

    Preprocessor subscribes to the three ``sentihome.memory.actor.*``
    subjects to keep its in-process cache fresh. Carries the
    embedding the preprocessor needs to do recognition.
    """

    schema_version: Literal["v1"] = "v1"

    actor_id: str
    action: Literal["enrolled", "updated", "deactivated"]

    name: str | None = None
    role: str | None = None
    access_profile: str | None = None

    face_embedding: tuple[float, ...] | None = None
    """Length matches the face model output (512 for ArcFace R100;
    placeholder 128 for in-memory testing). None for non-face actors
    (pets, vehicles)."""

    pet_dinov2_centroid: tuple[float, ...] | None = None
    """DINOv2 patch-feature centroid for pet recognition."""

    plate_text: str | None = None
    """For KnownVehicle actors; the canonical plate fastALPR matches
    against."""
