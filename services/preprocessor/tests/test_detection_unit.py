"""YOLODetector unit tests with mocked Ultralytics output.

The real model invocation lives behind an asyncio.run_in_executor +
ultralytics import, both of which we patch. Verifies the
COCO-name → DetectionTag.kind mapping, the pixel→normalized bbox
conversion, the confidence-threshold filtering, and the JPEG-decode
failure path.

A slow integration test that actually loads YOLO11n and runs on a
real fixture image lives in ``test_detection_integration.py``
(skipped unless ultralytics is importable).
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from kukiihome_preprocessor.pipelines.detection import (
    _INTERESTING_COCO_CLASSES,
    DetectionConfig,
    YOLODetector,
    _jpeg_to_bgr,
    _results_to_tags,
)

# ─── Fixtures ────────────────────────────────────────────────────────


def _solid_jpeg(width: int = 320, height: int = 240) -> bytes:
    """Produce a valid JPEG of the given dimensions filled with a
    flat color. Real bytes from the cv2 encoder so the decoder
    test path exercises actual JPEG decoding."""
    import cv2

    img = np.full((height, width, 3), 128, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", img)
    assert ok
    return jpeg.tobytes()


class _FakeBoxes:
    """Stand-in for ultralytics.Results.boxes — provides the three
    tensors YOLO returns (xyxy, conf, cls). Numpy stand-ins for
    torch tensors; the _to_numpy helper handles both."""

    def __init__(
        self,
        *,
        xyxy: list[list[float]],
        conf: list[float],
        cls: list[int],
        track_ids: list[int] | None = None,
    ) -> None:
        self.xyxy = np.array(xyxy, dtype=np.float32)
        self.conf = np.array(conf, dtype=np.float32)
        self.cls = np.array(cls, dtype=np.float32)
        # ultralytics exposes track ids as boxes.id in track mode;
        # None in predict mode / for unconfirmed tracks.
        self.id = np.array(track_ids, dtype=np.float32) if track_ids is not None else None


class _FakeResult:
    """Stand-in for ultralytics.Results."""

    def __init__(self, *, names: dict[int, str], boxes: _FakeBoxes) -> None:
        self.names = names
        self.boxes = boxes


# COCO class name dict — YOLO's full 80-class names, but for tests we
# only populate the few we care about plus one "uninteresting" class
# to verify filtering.
_FAKE_NAMES = {
    0: "person",
    16: "dog",
    17: "cat",
    2: "car",
    63: "laptop",  # Not in _INTERESTING_COCO_CLASSES — should be filtered.
}


# ─── _results_to_tags: the COCO → DetectionTag mapping ──────────────


def test_results_to_tags_maps_known_coco_classes():
    result = _FakeResult(
        names=_FAKE_NAMES,
        boxes=_FakeBoxes(
            xyxy=[[100, 50, 300, 200], [150, 100, 250, 220]],
            conf=[0.92, 0.78],
            cls=[0, 16],  # person, dog
            track_ids=[7, 12],  # YOLO track-mode ids
        ),
    )
    tags = _results_to_tags([result], frame_shape=(480, 640, 3), frame_ts=1234.5)
    assert len(tags) == 2

    person = next(t for t in tags if t.kind == "person")
    assert person.confidence == 0.92
    # Pixel→normalized: (100, 50, 300, 200) at (640, 480) →
    # (0.1562, 0.1042, 0.4688, 0.4167).
    assert person.bbox == (0.1562, 0.1042, 0.4688, 0.4167)
    assert person.frame_ts == 1234.5
    # Track mode → boxes.id carried through as a string track_id.
    assert person.track_id == "7"

    dog = next(t for t in tags if t.kind == "dog")
    assert dog.confidence == 0.78
    assert dog.track_id == "12"


def test_results_to_tags_synthesizes_track_id_without_tracker():
    """Predict mode (or an unconfirmed track) leaves ``boxes.id`` None.
    We synthesize a per-frame track_id so identity pipelines never
    receive a None track_id — which silently disabled face/body
    recognition in the RTSP path (the bug this guards against)."""
    result = _FakeResult(
        names=_FAKE_NAMES,
        boxes=_FakeBoxes(xyxy=[[0, 0, 100, 100]], conf=[0.8], cls=[0]),  # no track_ids
    )
    tags = _results_to_tags([result], frame_shape=(480, 640, 3), frame_ts=12.345)
    assert len(tags) == 1
    assert tags[0].track_id is not None
    assert tags[0].track_id == "12345-0"  # int(round(12.345*1000))-<index>


def test_results_to_tags_filters_uninteresting_classes():
    """A 'laptop' detection at 0.99 confidence should be dropped —
    not in _INTERESTING_COCO_CLASSES."""
    result = _FakeResult(
        names=_FAKE_NAMES,
        boxes=_FakeBoxes(
            xyxy=[[0, 0, 100, 100], [10, 10, 50, 50]],
            conf=[0.99, 0.85],
            cls=[63, 0],  # laptop (filtered), person (kept)
        ),
    )
    tags = _results_to_tags([result], frame_shape=(480, 640, 3), frame_ts=0)
    assert len(tags) == 1
    assert tags[0].kind == "person"


def test_results_to_tags_maps_car_to_vehicle():
    """Multiple COCO classes collapse onto our smaller tag vocabulary.
    Ensure that mapping holds."""
    result = _FakeResult(
        names=_FAKE_NAMES,
        boxes=_FakeBoxes(
            xyxy=[[0, 0, 100, 100]],
            conf=[0.8],
            cls=[2],  # car → vehicle
        ),
    )
    tags = _results_to_tags([result], frame_shape=(480, 640, 3), frame_ts=0)
    assert len(tags) == 1
    assert tags[0].kind == "vehicle"


def test_results_to_tags_handles_empty_input():
    assert _results_to_tags([], frame_shape=(480, 640, 3), frame_ts=0) == ()


def test_results_to_tags_handles_no_boxes():
    """Results object with no detections still works."""
    result = _FakeResult(
        names=_FAKE_NAMES,
        boxes=_FakeBoxes(xyxy=[], conf=[], cls=[]),
    )
    tags = _results_to_tags([result], frame_shape=(480, 640, 3), frame_ts=0)
    assert tags == ()


# ─── Per-class confidence floors ────────────────────────────────────


def test_per_class_floor_keeps_low_conf_dog_drops_low_conf_person():
    """A 0.34 dog (the real top-down score) must survive the 0.25 dog
    floor, while a 0.34 person is dropped by the 0.5 default floor. This
    is the fix for the dog being invisible to the pet pipeline (S16)."""
    result = _FakeResult(
        names=_FAKE_NAMES,
        boxes=_FakeBoxes(
            xyxy=[[0, 0, 100, 100], [200, 200, 300, 300]],
            conf=[0.34, 0.34],
            cls=[16, 0],  # dog @ 0.34 (kept), person @ 0.34 (dropped)
        ),
    )
    tags = _results_to_tags(
        [result], frame_shape=(480, 640, 3), frame_ts=0, config=DetectionConfig()
    )
    kinds = {t.kind for t in tags}
    assert kinds == {"dog"}


def test_per_class_floor_still_drops_very_low_dog():
    """A dog below even the 0.25 floor is dropped — the floor is real,
    not 'keep all animals'."""
    result = _FakeResult(
        names=_FAKE_NAMES,
        boxes=_FakeBoxes(xyxy=[[0, 0, 100, 100]], conf=[0.18], cls=[16]),
    )
    tags = _results_to_tags(
        [result], frame_shape=(480, 640, 3), frame_ts=0, config=DetectionConfig()
    )
    assert tags == ()


def test_inference_floor_is_min_across_all_classes():
    """The conf handed to the model must be the lowest floor, else
    low-floor classes never receive candidate boxes to filter."""
    det = YOLODetector(DetectionConfig(confidence_min=0.5, per_class_confidence={"dog": 0.25}))
    assert det._inference_floor() == 0.25


def test_default_per_class_includes_animals():
    c = DetectionConfig()
    assert c.per_class_confidence.get("dog") == 0.25
    assert c.per_class_confidence.get("cat") == 0.25


# ─── _jpeg_to_bgr: the decode path ──────────────────────────────────


def test_jpeg_decode_returns_bgr_array():
    img = _jpeg_to_bgr(_solid_jpeg(width=320, height=240))
    assert img is not None
    assert img.shape == (240, 320, 3)
    assert img.dtype == np.uint8


def test_jpeg_decode_returns_none_for_garbage():
    """Bad input should return None instead of raising."""
    assert _jpeg_to_bgr(b"not a JPEG") is None
    assert _jpeg_to_bgr(b"") is None


# ─── Class-mapping sanity check ──────────────────────────────────────


def test_interesting_class_map_covers_alert_relevant_objects():
    """The COCO classes we care about have to be in the map. Pinned
    so a careless map edit doesn't accidentally drop e.g. person."""
    must_be_mapped = {"person", "dog", "cat", "car"}
    assert must_be_mapped <= set(_INTERESTING_COCO_CLASSES.keys())
    assert _INTERESTING_COCO_CLASSES["person"] == "person"
    assert _INTERESTING_COCO_CLASSES["dog"] == "dog"


# ─── Config defaults ─────────────────────────────────────────────────


def test_detection_config_defaults_are_sane():
    c = DetectionConfig()
    assert c.weights.endswith(".pt")
    assert 0.0 < c.confidence_min < 1.0
    assert 0.0 < c.iou_min < 1.0
    assert c.image_size in (320, 416, 512, 640, 768, 1024, 1280)


# ─── device resolution (openvino ↔ ultralytics 'intel:*') ───────────


@pytest.mark.parametrize(
    ("backend", "device", "expected"),
    [
        # pytorch: pass through verbatim (torch device strings).
        ("pytorch", None, None),
        ("pytorch", "cpu", "cpu"),
        ("pytorch", "cuda:0", "cuda:0"),
        # openvino: bare OpenVINO names → ultralytics 'intel:<dev>'.
        # A bare "GPU"/"AUTO" would otherwise hit ultralytics' CUDA
        # parser and raise ValueError, silently denying the iGPU.
        ("openvino", "GPU", "intel:gpu"),
        ("openvino", "GPU.0", "intel:gpu"),
        ("openvino", "CPU", "intel:cpu"),
        ("openvino", "NPU", "intel:npu"),
        ("openvino", "AUTO", "intel:gpu"),
        ("openvino", None, "intel:gpu"),
        # Already-correct ultralytics form is accepted verbatim.
        ("openvino", "intel:gpu", "intel:gpu"),
        ("openvino", "intel:cpu", "intel:cpu"),
    ],
)
def test_resolve_device_maps_openvino_names(backend, device, expected):
    det = YOLODetector(DetectionConfig(backend=backend, device=device))
    assert det._resolve_device() == expected


# ─── End-to-end via patched model ───────────────────────────────────


@pytest.mark.asyncio
async def test_detect_invokes_model_with_decoded_frame(monkeypatch):
    """Patch _ensure_model + .predict to verify the wiring without
    actually loading ultralytics."""
    captured: dict = {}

    class _StubModel:
        def predict(self, img, **kwargs):
            captured["img_shape"] = img.shape if hasattr(img, "shape") else "<list>"
            captured["kwargs"] = kwargs
            return [
                _FakeResult(
                    names=_FAKE_NAMES,
                    boxes=_FakeBoxes(
                        xyxy=[[50, 60, 150, 180]],
                        conf=[0.91],
                        cls=[0],  # person
                    ),
                )
            ]

    det = YOLODetector(DetectionConfig(image_size=640))
    monkeypatch.setattr(det, "_ensure_model", lambda: _StubModel())

    tags = await det.detect(_solid_jpeg(width=320, height=240), frame_ts=99.0)
    assert len(tags) == 1
    assert tags[0].kind == "person"
    assert tags[0].confidence == 0.91
    assert tags[0].frame_ts == 99.0
    # Confirm the predict call saw a decoded BGR image (320x240).
    assert captured["img_shape"] == (240, 320, 3)
    assert captured["kwargs"]["imgsz"] == 640
    assert captured["kwargs"]["verbose"] is False


@pytest.mark.asyncio
async def test_detect_batch_returns_tags_for_each_frame_in_batch(monkeypatch):
    class _StubModel:
        def track(self, images, **kwargs):
            # detect_batch uses track mode (persistent track_ids).
            results = []
            for i, _ in enumerate(images):
                results.append(
                    _FakeResult(
                        names=_FAKE_NAMES,
                        boxes=_FakeBoxes(
                            xyxy=[[0, 0, 100, 100]],
                            conf=[0.8],
                            cls=[0],
                            track_ids=[i + 1],
                        ),
                    )
                )
            return results

    det = YOLODetector()
    monkeypatch.setattr(det, "_ensure_model", lambda: _StubModel())

    tags = await det.detect_batch(
        [(_solid_jpeg(), 10.0), (_solid_jpeg(), 11.0), (_solid_jpeg(), 12.0)]
    )
    assert {t.frame_ts for t in tags} == {10.0, 11.0, 12.0}
    assert all(t.kind == "person" for t in tags)


@pytest.mark.asyncio
async def test_detect_batch_empty_input_returns_empty_tuple():
    det = YOLODetector()
    assert await det.detect_batch([]) == ()


@pytest.mark.asyncio
async def test_detect_returns_empty_on_bad_jpeg(monkeypatch):
    """Corrupt frames should drop silently, not raise."""

    class _StubModel:
        def predict(self, *args, **kwargs):
            raise AssertionError("predict should not be called when decode fails")

    det = YOLODetector()
    monkeypatch.setattr(det, "_ensure_model", lambda: _StubModel())
    tags = await det.detect(b"\x00\x01\x02 not a jpeg", frame_ts=0)
    assert tags == ()


_ = io  # keep the io import referenced for fixtures
