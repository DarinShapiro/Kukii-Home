"""Frame markup — draw bounding boxes around RECOGNIZED entities only.

The principle: a labeled "unknown person" box adds noise without
adding signal. The VLM already sees the raw pixels and can reason
about the unknown person from context. Only when we can give the
VLM a *name* (Alice, Rex, Bob's truck) does drawing a box improve
grounding instead of polluting it.

So this module annotates only :class:`IdentifiedEntity` instances —
the pre-correlated detection+identity claims with a friendly name
resolved. Unknown detections never get drawn.

Visual style (locked):
* **Green solid** box,    identity_confidence >= 0.85   (high)
* **Yellow dashed** box,  0.6 <= identity_confidence < 0.85 (probable)
* **No box**,             identity_confidence < 0.6     (unknown)

Label format:  ``"{name} ({method} {conf:.2f})"`` — e.g.
``Alice (face 0.92)``, ``Rex (pet 0.78)``, ``Bob's truck (plate)``.

The output JPEG is the input frame with annotation overlay. Original
pixels are preserved everywhere outside the bounding boxes — the VLM
sees the actual scene, just with our identity claims highlighted.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from sentihome_shared.preprocessor import IdentifiedEntity

# ─── Visual style ───────────────────────────────────────────────────


_HIGH_CONF_THRESHOLD = 0.85
_MIN_CONF_THRESHOLD = 0.6

# BGR colors (OpenCV convention).
_GREEN = (60, 220, 80)
_YELLOW = (40, 200, 230)

_BOX_THICKNESS_PX = 2
_DASH_LEN_PX = 8
_DASH_GAP_PX = 4
_LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX
_LABEL_FONT_SCALE = 0.5
_LABEL_FONT_THICKNESS = 1
_LABEL_PAD_PX = 3

# Pretty names for the methods that appear in labels.
_METHOD_LABEL = {
    "face_arcface": "face",
    "pet_dinov2": "pet",
    "plate_lpr": "plate",
}


@dataclass(frozen=True)
class MarkupStats:
    """Returned alongside the annotated frame for observability /
    logging. Used by RTSPFrameBuffer to track how many frames were
    materially annotated vs. left untouched."""

    entities_annotated: int
    entities_skipped_below_threshold: int


def annotate_frame(
    bgr: np.ndarray, entities: tuple[IdentifiedEntity, ...]
) -> tuple[np.ndarray, MarkupStats]:
    """Return a copy of ``bgr`` with bounding boxes drawn around
    every identity-confirmed entity above the minimum threshold.

    The input array is NOT mutated — we work on a copy. That's a
    small CPU cost (~1ms for 720p) but it keeps the rolling-buffer
    bytes pristine; the same raw frame can be re-annotated with
    a different entity set later if we ever recompute identities.

    With zero entities (the common case until face/pet/plate
    pipelines come online): returns the input untouched and zero
    stats. The caller can use that signal to skip caching the
    annotated version entirely.
    """
    if not entities:
        return bgr, MarkupStats(0, 0)

    out = bgr.copy()
    h, w = out.shape[:2]

    annotated = 0
    skipped = 0
    for ent in entities:
        if ent.identity_confidence < _MIN_CONF_THRESHOLD:
            skipped += 1
            continue
        annotated += 1

        is_high = ent.identity_confidence >= _HIGH_CONF_THRESHOLD
        color = _GREEN if is_high else _YELLOW

        x1, y1, x2, y2 = (
            int(ent.bbox[0] * w),
            int(ent.bbox[1] * h),
            int(ent.bbox[2] * w),
            int(ent.bbox[3] * h),
        )
        # Clamp to image bounds — defensive against bbox values
        # that round to outside [0, w/h].
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(0, min(x2, w - 1))
        y2 = max(0, min(y2, h - 1))

        if is_high:
            _draw_solid_rect(out, x1, y1, x2, y2, color)
        else:
            _draw_dashed_rect(out, x1, y1, x2, y2, color)

        method_label = _METHOD_LABEL.get(ent.identity_method, ent.identity_method)
        label = f"{ent.actor_name} ({method_label} {ent.identity_confidence:.2f})"
        _draw_label(out, label, x1, y1, color, frame_w=w)

    return out, MarkupStats(
        entities_annotated=annotated,
        entities_skipped_below_threshold=skipped,
    )


def encode_jpeg(bgr: np.ndarray, quality: int = 80) -> bytes:
    """JPEG-encode an annotated frame for the wire. Quality 80 keeps
    annotation visibility crisp without bloating bytes — annotations
    have hard edges that JPEG handles poorly at lower qualities."""
    ok, jpeg = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("cv2.imencode failed on annotated frame")
    return jpeg.tobytes()


# ─── primitives ─────────────────────────────────────────────────────


def _draw_solid_rect(
    img: np.ndarray, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int]
) -> None:
    cv2.rectangle(img, (x1, y1), (x2, y2), color, _BOX_THICKNESS_PX)


def _draw_dashed_rect(
    img: np.ndarray, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int]
) -> None:
    """Dashed rectangle — OpenCV doesn't have one built in. Stride
    DASH_LEN visible / DASH_GAP invisible along each edge."""
    pitch = _DASH_LEN_PX + _DASH_GAP_PX
    for x in range(x1, x2, pitch):
        cv2.line(img, (x, y1), (min(x + _DASH_LEN_PX, x2), y1), color, _BOX_THICKNESS_PX)
        cv2.line(img, (x, y2), (min(x + _DASH_LEN_PX, x2), y2), color, _BOX_THICKNESS_PX)
    for y in range(y1, y2, pitch):
        cv2.line(img, (x1, y), (x1, min(y + _DASH_LEN_PX, y2)), color, _BOX_THICKNESS_PX)
        cv2.line(img, (x2, y), (x2, min(y + _DASH_LEN_PX, y2)), color, _BOX_THICKNESS_PX)


def _draw_label(
    img: np.ndarray,
    text: str,
    x: int,
    y: int,
    color: tuple[int, int, int],
    *,
    frame_w: int,
) -> None:
    """Draw a label with a filled background rectangle above the
    bounding box. If the bbox is at the top edge, drop the label
    *inside* the box top instead so it stays visible."""
    (tw, th), baseline = cv2.getTextSize(
        text, _LABEL_FONT, _LABEL_FONT_SCALE, _LABEL_FONT_THICKNESS
    )
    pad = _LABEL_PAD_PX
    label_w = tw + 2 * pad
    label_h = th + 2 * pad + baseline

    # Default: above the box. If that goes off-frame, drop it inside.
    label_y2 = y - 1
    label_y1 = label_y2 - label_h
    if label_y1 < 0:
        label_y1 = y + 1
        label_y2 = label_y1 + label_h

    label_x1 = x
    label_x2 = min(x + label_w, frame_w - 1)

    # Filled background for readability over arbitrary backgrounds.
    cv2.rectangle(img, (label_x1, label_y1), (label_x2, label_y2), color, -1)
    # Text — black on the colored background.
    cv2.putText(
        img,
        text,
        (label_x1 + pad, label_y2 - pad - baseline),
        _LABEL_FONT,
        _LABEL_FONT_SCALE,
        (0, 0, 0),
        _LABEL_FONT_THICKNESS,
        cv2.LINE_AA,
    )
