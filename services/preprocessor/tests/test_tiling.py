"""Unit tests for tiled detection geometry, NMS merge, and the IoU tracker.

These are the pure pieces of the tiled-detection path — the part that must
be correct *before* the empirical recovery test (scripts/dev/tiling_eval.py)
on real footage means anything. The cross-tile/cross-frame track-id merge
(IoUTracker) is the barrier the architecture notes flagged; it gets the
most coverage here.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest
from kukiihome_preprocessor.pipelines.tiling import (
    Box,
    IoUTracker,
    compute_tiles,
    detect_tiled,
    iou,
    merge_boxes,
)

# ─── compute_tiles ───────────────────────────────────────────────────────


def test_tiles_cover_full_frame_with_overlap():
    tiles = compute_tiles(3840, 2160, tile=1280, overlap=0.2)
    assert tiles, "expected tiles for a 4K frame"
    # Every tile is within bounds and at most `tile` in each dim.
    for t in tiles:
        assert 0 <= t.x0 < t.x1 <= 3840
        assert 0 <= t.y0 < t.y1 <= 2160
        assert t.width <= 1280 and t.height <= 1280
    # The far edges are covered (last tile flush to the edge).
    assert max(t.x1 for t in tiles) == 3840
    assert max(t.y1 for t in tiles) == 2160
    # First tile starts at the origin.
    assert any(t.x0 == 0 and t.y0 == 0 for t in tiles)


def test_tiles_adjacent_columns_actually_overlap():
    tiles = compute_tiles(3840, 2160, tile=1280, overlap=0.2)
    xs = sorted({t.x0 for t in tiles})
    # Step is tile*(1-overlap)=1024, so consecutive origins differ by <=1024
    # < 1280 → the [x0, x0+1280) spans overlap.
    for a, b in pairwise(xs):
        assert b - a < 1280, "adjacent columns must overlap"


def test_small_frame_is_single_tile():
    tiles = compute_tiles(800, 600, tile=1280, overlap=0.2)
    assert tiles == [tiles[0]]
    assert (tiles[0].x0, tiles[0].y0, tiles[0].x1, tiles[0].y1) == (0, 0, 800, 600)


def test_compute_tiles_rejects_bad_overlap():
    with pytest.raises(ValueError):
        compute_tiles(100, 100, tile=50, overlap=1.0)


# ─── iou + merge_boxes ───────────────────────────────────────────────────


def test_iou_identical_is_one_disjoint_is_zero():
    a = Box(0, 0, 10, 10, 0.9, "dog")
    assert iou(a, a) == pytest.approx(1.0)
    b = Box(100, 100, 110, 110, 0.9, "dog")
    assert iou(a, b) == 0.0


def test_merge_dedups_same_object_across_seam():
    # Same dog seen by two overlapping tiles → two near-identical boxes.
    high = Box(100, 100, 200, 200, 0.8, "dog")
    dup = Box(102, 98, 198, 203, 0.6, "dog")
    merged = merge_boxes([dup, high], iou_thresh=0.45)
    assert len(merged) == 1
    assert merged[0].conf == 0.8  # higher-confidence survivor kept


def test_merge_keeps_different_classes_overlapping():
    dog = Box(100, 100, 200, 200, 0.7, "dog")
    person = Box(100, 100, 200, 200, 0.9, "person")
    merged = merge_boxes([dog, person], iou_thresh=0.45)
    assert len(merged) == 2  # different classes never suppress each other


def test_merge_keeps_distinct_same_class_objects():
    a = Box(0, 0, 50, 50, 0.9, "person")
    b = Box(500, 500, 560, 560, 0.8, "person")
    assert len(merge_boxes([a, b])) == 2


# ─── IoUTracker (the flagged barrier) ────────────────────────────────────


def test_tracker_keeps_stable_id_for_moving_object():
    tr = IoUTracker(iou_thresh=0.3)
    # A dog drifting right across frames; consecutive boxes overlap.
    f1 = tr.update([Box(100, 100, 200, 200, 0.8, "dog")])
    f2 = tr.update([Box(120, 100, 220, 200, 0.8, "dog")])
    f3 = tr.update([Box(140, 100, 240, 200, 0.8, "dog")])
    assert f1[0].track_id == f2[0].track_id == f3[0].track_id


def test_tracker_assigns_new_id_to_new_object():
    tr = IoUTracker(iou_thresh=0.3)
    f1 = tr.update([Box(100, 100, 200, 200, 0.8, "dog")])
    # A second, disjoint object appears.
    f2 = tr.update(
        [Box(120, 100, 220, 200, 0.8, "dog"), Box(900, 900, 1000, 1000, 0.7, "dog")]
    )
    ids = {b.track_id for b in f2}
    assert f1[0].track_id in ids  # original track continued
    assert len(ids) == 2  # plus a brand-new one


def test_tracker_does_not_cross_class_match():
    tr = IoUTracker(iou_thresh=0.3)
    tr.update([Box(100, 100, 200, 200, 0.8, "dog")])
    # Same location next frame but it's a person → must be a new track.
    f2 = tr.update([Box(100, 100, 200, 200, 0.8, "person")])
    assert f2[0].cls == "person"
    # the dog track did not get hijacked by the person box
    assert f2[0].track_id == "2"


def test_tracker_ages_out_stale_tracks():
    tr = IoUTracker(iou_thresh=0.3, max_age=2)
    first = tr.update([Box(100, 100, 200, 200, 0.8, "dog")])[0].track_id
    for _ in range(5):  # object gone for > max_age frames
        tr.update([])
    # Object reappears in the same spot → should be a NEW id, old one aged out.
    reappear = tr.update([Box(100, 100, 200, 200, 0.8, "dog")])[0].track_id
    assert reappear != first


def test_tracker_one_to_one_under_contention():
    # Two close objects must not both bind to one track.
    tr = IoUTracker(iou_thresh=0.1)
    tr.update([Box(0, 0, 100, 100, 0.9, "person"), Box(60, 0, 160, 100, 0.8, "person")])
    f2 = tr.update(
        [Box(5, 0, 105, 100, 0.9, "person"), Box(65, 0, 165, 100, 0.8, "person")]
    )
    assert len({b.track_id for b in f2}) == 2  # distinct ids preserved


# ─── detect_tiled orchestration (fake detector, no torch) ────────────────


def test_detect_tiled_offsets_and_merges():
    frame = np.zeros((2160, 3840, 3), dtype=np.uint8)

    # Fake detector: returns one box in the centre of every tile, in
    # tile-local coords. After offset+merge these become distinct full-frame
    # boxes (one per tile, since tile centres are far apart).
    def fake_detect(crops: list) -> list[list[Box]]:
        out = []
        for c in crops:
            h, w = c.shape[:2]
            cx, cy = w / 2, h / 2
            out.append([Box(cx - 5, cy - 5, cx + 5, cy + 5, 0.9, "person")])
        return out

    boxes = detect_tiled(frame, fake_detect, tile=1280, overlap=0.2)
    n_tiles = len(compute_tiles(3840, 2160, tile=1280, overlap=0.2))
    assert len(boxes) == n_tiles
    # All boxes mapped into full-frame coords (none stuck at tile-local).
    for b in boxes:
        assert 0 <= b.x1 < 3840 and 0 <= b.y1 < 2160
