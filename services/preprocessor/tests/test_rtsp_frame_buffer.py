"""Unit tests for the RTSPFrameBuffer.

The buffer reads from a RollingBuffer that's pre-populated by the
test (skipping the real RTSP capture path; that's covered separately
by integration tests against a media-server testcontainer).
"""

from __future__ import annotations

import pytest
from sentihome_preprocessor.pipelines.rolling_buffer import (
    BufferedFrame,
    RollingBuffer,
)
from sentihome_preprocessor.pipelines.rtsp_frame_buffer import RTSPFrameBuffer
from sentihome_preprocessor.state import ActorCache


@pytest.fixture
async def rolling() -> RollingBuffer:
    return RollingBuffer(horizon_seconds=3600.0)


@pytest.fixture
async def buf(rolling: RollingBuffer) -> RTSPFrameBuffer:
    return RTSPFrameBuffer(
        rolling_buffer=rolling,
        configured_cameras=["cam_a", "cam_b"],
        node_id="test",
        external_base_url="http://example:8090",
    )


def _f(ts: float, *, size: int = 100, w: int = 1280, h: int = 720) -> BufferedFrame:
    return BufferedFrame(ts=ts, jpeg_bytes=b"x" * size, width=w, height=h)


# ─── get_window ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_window_returns_frames_from_rolling_buffer(
    rolling: RollingBuffer, buf: RTSPFrameBuffer
):
    for ts in (100.0, 101.0, 102.0):
        await rolling.write("cam_a", _f(ts))
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=100.0,
        ts_end=102.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert [f.ts for f in fw.frames] == [100.0, 101.0, 102.0]
    assert fw.camera_id == "cam_a"
    assert fw.preprocessor_node_id == "test"


@pytest.mark.asyncio
async def test_get_window_emits_absolute_uris_using_external_base_url(
    rolling: RollingBuffer, buf: RTSPFrameBuffer
):
    await rolling.write("cam_a", _f(123.456))
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert len(fw.frames) == 1
    assert fw.frames[0].uri == "http://example:8090/frames/cam_a/123.456.jpg"


@pytest.mark.asyncio
async def test_get_window_unknown_camera_empty(buf: RTSPFrameBuffer):
    fw = await buf.get_window(
        camera_id="ghost_cam",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.frames == ()


@pytest.mark.asyncio
async def test_get_window_inverted_window_empty(buf: RTSPFrameBuffer):
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=100.0,
        ts_end=50.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.frames == ()


@pytest.mark.asyncio
async def test_get_window_no_enrichment_in_phase_10_1_5(
    rolling: RollingBuffer, buf: RTSPFrameBuffer
):
    """RTSPFrameBuffer doesn't compute detections / actor matches
    against real frames yet — those wire in Phase 10.3+. Until then,
    the contract is: frames present, enrichment fields empty."""
    await rolling.write("cam_a", _f(100.0))
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=99.0,
        ts_end=101.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.detections == ()
    assert fw.actor_matches == ()


@pytest.mark.asyncio
async def test_get_window_records_latency(rolling: RollingBuffer, buf: RTSPFrameBuffer):
    await rolling.write("cam_a", _f(100.0))
    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.enrichment_latency_ms >= 0


# ─── serve_frame ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serve_frame_returns_bytes_for_buffered_ts(
    rolling: RollingBuffer, buf: RTSPFrameBuffer
):
    await rolling.write("cam_a", _f(123.456, size=42))
    data = await buf.serve_frame("cam_a", 123.456)
    assert data is not None
    assert len(data) == 42


@pytest.mark.asyncio
async def test_serve_frame_unknown_camera_returns_none(buf: RTSPFrameBuffer):
    assert await buf.serve_frame("ghost_cam", 100.0) is None


@pytest.mark.asyncio
async def test_serve_frame_missing_ts_returns_none(rolling: RollingBuffer, buf: RTSPFrameBuffer):
    await rolling.write("cam_a", _f(100.0))
    assert await buf.serve_frame("cam_a", 999.0) is None


# ─── Phase 10.3: detector wiring ────────────────────────────────────


class _StubDetector:
    """Stand-in for YOLODetector — records the batch it's handed +
    returns canned DetectionTags so the test can assert wiring
    without paying for ultralytics import."""

    def __init__(self, tags_per_frame: int = 1) -> None:
        self.batches_received: list[list[tuple[bytes, float]]] = []
        self._tags_per_frame = tags_per_frame

    async def detect_batch(self, frames: list[tuple[bytes, float]]) -> tuple:
        from sentihome_shared.preprocessor import DetectionTag

        self.batches_received.append(list(frames))
        out: list[DetectionTag] = []
        for _, ts in frames:
            for _ in range(self._tags_per_frame):
                out.append(
                    DetectionTag(
                        kind="person",
                        confidence=0.9,
                        bbox=(0.0, 0.0, 1.0, 1.0),
                        frame_ts=ts,
                    )
                )
        return tuple(out)


def _motion_frame(ts: float, *, size: int = 100) -> BufferedFrame:
    """Buffered frame flagged as containing motion. Used by detector
    tests since the default ``enrich_motion_only=True`` skips quiet
    frames."""
    return BufferedFrame(
        ts=ts,
        jpeg_bytes=b"x" * size,
        width=1280,
        height=720,
        has_motion=True,
    )


@pytest.mark.asyncio
async def test_get_window_populates_detections_when_detector_provided(
    rolling: RollingBuffer,
):
    """With a detector wired in, get_window's detections tuple is
    populated from the buffered frames. All test frames have
    has_motion=True so they pass the motion-gate filter."""
    detector = _StubDetector(tags_per_frame=2)
    buf = RTSPFrameBuffer(
        rolling_buffer=rolling,
        configured_cameras=["cam_a"],
        node_id="t",
        external_base_url="http://example:8090",
        detector=detector,
    )
    for ts in (100.0, 101.0, 102.0):
        await rolling.write("cam_a", _motion_frame(ts))

    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )

    # 3 frames, 2 tags each → 6 detections.
    assert len(fw.detections) == 6
    # Every detection ts must match one of the buffered frames.
    assert {d.frame_ts for d in fw.detections} == {100.0, 101.0, 102.0}
    # Detector was called exactly once with the full batch.
    assert len(detector.batches_received) == 1
    assert len(detector.batches_received[0]) == 3


# ─── Phase 10.3.2: motion gating filter ─────────────────────────────


@pytest.mark.asyncio
async def test_get_window_motion_gating_skips_quiet_frames(
    rolling: RollingBuffer,
):
    """Default enrich_motion_only=True: frames without has_motion
    are skipped by the detector — saves YOLO work on steady scenes."""
    detector = _StubDetector()
    buf = RTSPFrameBuffer(
        rolling_buffer=rolling,
        configured_cameras=["cam_a"],
        node_id="t",
        external_base_url="http://example:8090",
        detector=detector,
    )
    # 3 quiet frames + 2 motion frames.
    await rolling.write("cam_a", _f(100.0))  # quiet
    await rolling.write("cam_a", _motion_frame(101.0))  # motion
    await rolling.write("cam_a", _f(102.0))  # quiet
    await rolling.write("cam_a", _motion_frame(103.0))  # motion
    await rolling.write("cam_a", _f(104.0))  # quiet

    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )

    # All 5 frames are in the FrameRef list.
    assert len(fw.frames) == 5
    # But only the 2 motion frames went to YOLO.
    assert len(detector.batches_received) == 1
    sent_ts = {ts for (_jpeg, ts) in detector.batches_received[0]}
    assert sent_ts == {101.0, 103.0}
    assert len(fw.detections) == 2


@pytest.mark.asyncio
async def test_get_window_motion_gating_disabled_sends_all_frames(
    rolling: RollingBuffer,
):
    """enrich_motion_only=False overrides the gate — every frame in
    the window goes to YOLO. For forensic / replay use."""
    detector = _StubDetector()
    buf = RTSPFrameBuffer(
        rolling_buffer=rolling,
        configured_cameras=["cam_a"],
        node_id="t",
        external_base_url="http://example:8090",
        detector=detector,
        enrich_motion_only=False,
    )
    # All quiet frames.
    for ts in (100.0, 101.0, 102.0):
        await rolling.write("cam_a", _f(ts))

    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert len(fw.frames) == 3
    # Motion gate disabled → all frames sent to detector.
    assert len(detector.batches_received) == 1
    assert len(detector.batches_received[0]) == 3
    assert len(fw.detections) == 3


@pytest.mark.asyncio
async def test_get_window_motion_gating_all_quiet_skips_detector_entirely(
    rolling: RollingBuffer,
):
    """When every buffered frame is quiet, detector isn't invoked at
    all (saves the batch-overhead cost too, not just inference)."""
    detector = _StubDetector()
    buf = RTSPFrameBuffer(
        rolling_buffer=rolling,
        configured_cameras=["cam_a"],
        node_id="t",
        external_base_url="http://example:8090",
        detector=detector,
    )
    for ts in (100.0, 101.0, 102.0):
        await rolling.write("cam_a", _f(ts))

    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert len(fw.frames) == 3
    assert fw.detections == ()
    assert detector.batches_received == []  # detector never called


@pytest.mark.asyncio
async def test_get_window_skips_detector_when_enrich_false(
    rolling: RollingBuffer,
):
    """enrich=False short-circuits the detector entirely."""
    detector = _StubDetector()
    buf = RTSPFrameBuffer(
        rolling_buffer=rolling,
        configured_cameras=["cam_a"],
        node_id="t",
        external_base_url="http://example:8090",
        detector=detector,
    )
    await rolling.write("cam_a", _f(100.0))

    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=False,
        cache=ActorCache(),
    )

    assert fw.detections == ()
    assert detector.batches_received == []


@pytest.mark.asyncio
async def test_get_window_without_detector_leaves_detections_empty(
    rolling: RollingBuffer,
):
    """No detector configured (skeleton / Phase 10.1.5 mode) →
    detections stay empty even with enrich=True."""
    buf = RTSPFrameBuffer(
        rolling_buffer=rolling,
        configured_cameras=["cam_a"],
        node_id="t",
        external_base_url="http://example:8090",
        # detector kwarg omitted on purpose
    )
    await rolling.write("cam_a", _f(100.0))

    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),
    )
    assert fw.detections == ()


# ─── Phase 10.4: face recognizer wiring ─────────────────────────────


class _StubFaceRecognizer:
    """Stand-in for FaceRecognizer — returns canned DetectedFace
    records so the wiring path can be exercised without insightface."""

    def __init__(self, faces_per_call: tuple = ()) -> None:
        self._faces = faces_per_call
        self.calls: list[tuple] = []  # (bgr_shape, enrolled_keys)

    async def detect_and_match(self, bgr, enrolled):
        self.calls.append((bgr.shape, tuple(sorted(enrolled.keys()))))
        return self._faces


def _real_jpeg(ts: float, w: int = 200, h: int = 200) -> BufferedFrame:
    """Buffered frame with real JPEG bytes the face pipeline can decode."""
    import cv2
    import numpy as np

    img = np.full((h, w, 3), 128, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", img)
    assert ok
    return BufferedFrame(ts=ts, jpeg_bytes=jpeg.tobytes(), width=w, height=h, has_motion=True)


@pytest.mark.asyncio
async def test_get_window_runs_face_recognizer_for_person_frames(
    rolling: RollingBuffer,
):
    """When a person detection lands on a frame AND a face_recognizer
    is wired, get_window invokes the recognizer with the frame and
    the actor cache's face embeddings, then surfaces matched faces
    as ActorMatches with track_ids inherited from the person bbox."""
    import numpy as np
    from sentihome_preprocessor.pipelines.face import DetectedFace
    from sentihome_shared.preprocessor import (
        ActorEnrollmentEvent,
        DetectionTag,
    )

    class _PersonDetector:
        async def detect_batch(self, frames):
            return tuple(
                DetectionTag(
                    kind="person",
                    confidence=0.9,
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    track_id="t1",
                    frame_ts=ts,
                )
                for _, ts in frames
            )

    embedding = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    face_recognizer = _StubFaceRecognizer(
        faces_per_call=(
            DetectedFace(
                bbox=(0.4, 0.4, 0.6, 0.6),  # inside person bbox
                det_confidence=0.95,
                embedding=embedding,
                matched_actor_id="alice",
                match_confidence=0.82,
            ),
        )
    )

    cache = ActorCache()
    await cache.upsert(
        ActorEnrollmentEvent(
            actor_id="alice",
            action="enrolled",
            name="Alice",
            face_embedding=tuple(embedding.tolist()),
        )
    )

    buf = RTSPFrameBuffer(
        rolling_buffer=rolling,
        node_id="t",
        external_base_url="http://example:8090",
        detector=_PersonDetector(),
        face_recognizer=face_recognizer,
    )
    await rolling.write("cam_a", _real_jpeg(100.0))

    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=cache,
    )

    assert len(face_recognizer.calls) == 1
    _shape, enrolled_keys = face_recognizer.calls[0]
    assert enrolled_keys == ("alice",)

    assert len(fw.actor_matches) == 1
    am = fw.actor_matches[0]
    assert am.actor_id == "alice"
    assert am.match_method == "face_arcface"
    assert am.track_id == "t1"
    assert am.confidence == pytest.approx(0.82)

    # Correlates into an IdentifiedEntity (conf > 0.6).
    assert len(fw.identified_entities) == 1
    ent = fw.identified_entities[0]
    assert ent.kind == "person"
    assert ent.actor_id == "alice"
    assert ent.actor_name == "Alice"


@pytest.mark.asyncio
async def test_get_window_skips_face_recognition_without_person_detections(
    rolling: RollingBuffer,
):
    """Frames carrying only non-person detections (e.g. just a car)
    skip face recognition entirely — no ArcFace inference cost."""
    import numpy as np
    from sentihome_shared.preprocessor import (
        ActorEnrollmentEvent,
        DetectionTag,
    )

    class _CarDetector:
        async def detect_batch(self, frames):
            return tuple(
                DetectionTag(
                    kind="vehicle",
                    confidence=0.9,
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    track_id="t1",
                    frame_ts=ts,
                )
                for _, ts in frames
            )

    face_recognizer = _StubFaceRecognizer()
    cache = ActorCache()
    await cache.upsert(
        ActorEnrollmentEvent(
            actor_id="alice",
            action="enrolled",
            name="Alice",
            face_embedding=tuple(np.array([1.0, 0.0], dtype=np.float32).tolist()),
        )
    )

    buf = RTSPFrameBuffer(
        rolling_buffer=rolling,
        node_id="t",
        external_base_url="http://example:8090",
        detector=_CarDetector(),
        face_recognizer=face_recognizer,
    )
    await rolling.write("cam_a", _real_jpeg(100.0))

    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=cache,
    )

    assert face_recognizer.calls == []
    assert fw.actor_matches == ()


@pytest.mark.asyncio
async def test_get_window_no_face_recognition_when_actor_cache_empty(
    rolling: RollingBuffer,
):
    """No enrolled faces -> short-circuit before invoking the
    recognizer."""
    from sentihome_shared.preprocessor import DetectionTag

    class _PersonDetector:
        async def detect_batch(self, frames):
            return tuple(
                DetectionTag(
                    kind="person",
                    confidence=0.9,
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    track_id="t1",
                    frame_ts=ts,
                )
                for _, ts in frames
            )

    face_recognizer = _StubFaceRecognizer()
    buf = RTSPFrameBuffer(
        rolling_buffer=rolling,
        node_id="t",
        external_base_url="http://example:8090",
        detector=_PersonDetector(),
        face_recognizer=face_recognizer,
    )
    await rolling.write("cam_a", _real_jpeg(100.0))

    fw = await buf.get_window(
        camera_id="cam_a",
        ts_start=0.0,
        ts_end=1000.0,
        enrich=True,
        cache=ActorCache(),  # empty
    )

    assert face_recognizer.calls == []
    assert fw.actor_matches == ()
