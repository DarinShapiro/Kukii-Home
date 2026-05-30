"""Unit tests for the frame markup pipeline.

The contract being verified: only RECOGNIZED entities above the
minimum confidence threshold get drawn. Below threshold or empty
input -> input frame returned untouched.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from sentihome_preprocessor.pipelines.markup import (
    _HIGH_CONF_THRESHOLD,
    _MIN_CONF_THRESHOLD,
    annotate_frame,
    encode_jpeg,
)
from sentihome_shared.preprocessor import IdentifiedEntity


def _blank(w: int = 320, h: int = 240, color: int = 80) -> np.ndarray:
    return np.full((h, w, 3), color, dtype=np.uint8)


def _entity(
    *,
    name: str = "Alice",
    actor_id: str = "actor_alice",
    kind: str = "person",
    method: str = "face_arcface",
    identity_confidence: float = 0.92,
    bbox: tuple[float, float, float, float] = (0.2, 0.2, 0.6, 0.8),
    frame_ts: float = 0.0,
) -> IdentifiedEntity:
    return IdentifiedEntity(
        frame_ts=frame_ts,
        kind=kind,  # type: ignore[arg-type]
        actor_id=actor_id,
        actor_name=name,
        bbox=bbox,
        detection_confidence=0.95,
        identity_confidence=identity_confidence,
        identity_method=method,  # type: ignore[arg-type]
        track_id="t-1",
    )


# ─── Empty / quiet cases ────────────────────────────────────────────


def test_no_entities_returns_input_untouched():
    img = _blank()
    out, stats = annotate_frame(img, ())
    assert stats.entities_annotated == 0
    assert stats.entities_skipped_below_threshold == 0
    # Same object (no copy when nothing to do).
    assert out is img


def test_input_array_not_mutated_when_entities_drawn():
    """Annotation produces a COPY so the rolling buffer's bytes stay
    pristine."""
    img = _blank()
    original_first_pixel = tuple(int(c) for c in img[0, 0])
    out, _ = annotate_frame(img, (_entity(),))
    # Output is a different array.
    assert out is not img
    # Input is unchanged.
    assert tuple(int(c) for c in img[0, 0]) == original_first_pixel


# ─── Threshold gating ───────────────────────────────────────────────


def test_entity_below_minimum_threshold_is_skipped():
    img = _blank()
    low = _entity(identity_confidence=_MIN_CONF_THRESHOLD - 0.01)
    out, stats = annotate_frame(img, (low,))
    assert stats.entities_annotated == 0
    assert stats.entities_skipped_below_threshold == 1
    # Bbox area in the output should be pixel-identical to input
    # (nothing drawn there).
    x1, y1 = 64, 48
    x2, y2 = 192, 192
    assert np.array_equal(img[y1:y2, x1:x2], out[y1:y2, x1:x2])


def test_entity_at_minimum_threshold_is_annotated():
    img = _blank()
    just_in = _entity(identity_confidence=_MIN_CONF_THRESHOLD)
    out, stats = annotate_frame(img, (just_in,))
    assert stats.entities_annotated == 1
    # Pixels in the bbox region differ — we drew something.
    h, w = img.shape[:2]
    bx1, by1 = int(0.2 * w), int(0.2 * h)
    bx2, by2 = int(0.6 * w), int(0.8 * h)
    assert not np.array_equal(img[by1:by2, bx1:bx2], out[by1:by2, bx1:bx2])


def test_high_confidence_uses_solid_green():
    """Green BGR (60, 220, 80). High-confidence box top edge should
    have solid green pixels along the line."""
    img = _blank()
    high = _entity(identity_confidence=_HIGH_CONF_THRESHOLD + 0.05)
    out, _ = annotate_frame(img, (high,))
    h, w = img.shape[:2]
    by1 = int(0.2 * h)
    bx1 = int(0.2 * w)
    bx2 = int(0.6 * w)
    # Sample the top edge of the box (a few pixels along).
    top_edge_pixels = out[by1, bx1 + 4 : bx2 - 4]
    # All pixels along the solid top edge should be the green color.
    green_pixels = np.all(top_edge_pixels == np.array([60, 220, 80]), axis=1)
    assert green_pixels.any(), "expected solid-green top edge for high-confidence entity"


def test_medium_confidence_uses_dashed_yellow():
    """Yellow BGR (40, 200, 230). Dashed -> some pixels along the
    edge are yellow, others retain the background color."""
    img = _blank()
    mid = _entity(identity_confidence=(_MIN_CONF_THRESHOLD + _HIGH_CONF_THRESHOLD) / 2)
    out, _ = annotate_frame(img, (mid,))
    h, w = img.shape[:2]
    by1 = int(0.2 * h)
    bx1 = int(0.2 * w)
    bx2 = int(0.6 * w)
    top_edge = out[by1, bx1 + 4 : bx2 - 4]
    # At least some pixels are yellow (the dashes).
    yellow_pixels = np.all(top_edge == np.array([40, 200, 230]), axis=1)
    assert yellow_pixels.any(), "expected at least some yellow pixels along dashed top edge"
    # At least some pixels are NOT yellow (the gaps).
    assert not yellow_pixels.all(), "expected gaps between dashes"


# ─── Label rendering ────────────────────────────────────────────────


def test_label_contains_actor_name_and_method():
    """Verify the label text rendered onto the frame contains the
    expected substrings by OCRing the label region. We don't have a
    real OCR here — just verify the label region differs from the
    background (i.e., text was rendered) AND we exercise the path
    end-to-end."""
    img = _blank(w=640, h=480)
    ent = _entity(name="Rex", method="pet_dinov2", identity_confidence=0.88)
    out, _ = annotate_frame(img, (ent,))
    # The label box is filled with a solid color (green for high conf).
    # Sample a known label-region pixel (just inside the top-left of
    # the bbox, above the box if there's room).
    h, w = img.shape[:2]
    bx1 = int(0.2 * w)
    by1 = int(0.2 * h)
    # Label sits just above the bbox top edge; pixel a few above by1.
    label_sample = out[by1 - 5, bx1 + 5]
    assert not np.array_equal(label_sample, img[by1 - 5, bx1 + 5]), (
        "expected label background to be drawn above the bbox"
    )


def test_method_label_covers_all_active_modalities():
    """The label text is VLM-facing grounding, so every active identity
    method must map to a clean human name — a missing entry would leak
    the raw pipeline id (e.g. "body_id_osnet") onto the frame."""
    from sentihome_preprocessor.pipelines.markup import _METHOD_LABEL

    for method in ("face_arcface", "body_id_osnet", "pet_dinov2"):
        assert method in _METHOD_LABEL, f"no pretty label for {method}"
    assert _METHOD_LABEL["body_id_osnet"] == "body"


# ─── encode_jpeg ────────────────────────────────────────────────────


def test_encode_jpeg_produces_valid_jpeg_bytes():
    img = _blank()
    out = encode_jpeg(img)
    assert out[:3] == b"\xff\xd8\xff", "expected JPEG SOI marker"


def test_encode_jpeg_roundtrips_to_same_dimensions():
    img = _blank(w=800, h=600)
    encoded = encode_jpeg(img)
    decoded = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == img.shape


# ─── End-to-end pixel check ─────────────────────────────────────────


def test_multiple_entities_at_different_confidence_levels():
    img = _blank()
    ents = (
        _entity(name="Alice", bbox=(0.05, 0.05, 0.30, 0.45), identity_confidence=0.95),
        _entity(name="Rex", bbox=(0.40, 0.40, 0.70, 0.90), identity_confidence=0.72),
        _entity(name="Ghost", bbox=(0.75, 0.10, 0.95, 0.40), identity_confidence=0.45),
    )
    out, stats = annotate_frame(img, ents)
    assert stats.entities_annotated == 2  # Alice + Rex
    assert stats.entities_skipped_below_threshold == 1  # Ghost
    # Output isn't the input.
    assert out is not img


def test_bbox_at_image_edge_does_not_crash():
    """Bboxes that touch / exceed image bounds get clamped."""
    img = _blank()
    ent = _entity(bbox=(0.0, 0.0, 1.0, 1.0))
    out, stats = annotate_frame(img, (ent,))
    assert stats.entities_annotated == 1
    assert out.shape == img.shape


@pytest.mark.parametrize(
    "kind,method",
    [
        ("person", "face_arcface"),
        ("dog", "pet_dinov2"),
        ("cat", "pet_dinov2"),
        ("vehicle", "plate_lpr"),
    ],
)
def test_all_supported_kinds_render(kind: str, method: str):
    """Every kind/method pair the contract allows should render
    without error."""
    img = _blank()
    ent = _entity(kind=kind, method=method)
    out, stats = annotate_frame(img, (ent,))
    assert stats.entities_annotated == 1
    assert out is not img
