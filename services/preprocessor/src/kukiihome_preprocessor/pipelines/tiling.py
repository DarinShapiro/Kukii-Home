"""Tiled full-resolution detection — the proper "never downsample the 4K" answer.

Running YOLO at ``imgsz=1280`` on a 3840x2160 frame still letterboxes the
whole frame down to 1280 — a ~3x shrink, so a distant 150px dog becomes
~50px before the detector sees it. Cranking ``imgsz`` is the crude fix;
**tiling is the correct one**: slice the 4K frame into overlapping
native-resolution tiles, detect each tile at full pixel density (the dog is
~150px *in its tile*), then merge boxes back into one full-frame result.

This module is deliberately split into pure, individually-testable pieces:

* :func:`compute_tiles` — frame geometry → tile rectangles (pure).
* :class:`Box` — a detection in full-frame pixel coords.
* :func:`merge_boxes` — class-aware global NMS that dedups objects seen in
  the overlap between adjacent tiles (pure).
* :class:`IoUTracker` — greedy IoU association across frames, assigning
  stable ``track_id``s to merged boxes.

That last piece is the part the architecture notes flagged as the hard
barrier: tiled detection runs ``model.predict`` per tile (no tracker), so
the persistent ``track_id`` the identity/correlation pipeline depends on
can no longer come from ultralytics' built-in ``model.track``. We recover
it by tracking the *merged full-frame* boxes ourselves — which is also
strictly more correct than per-tile tracking (a track that crosses a tile
seam stays one track).

The detector orchestration (:func:`detect_tiled`) takes the per-batch
inference call as a plain callable so the whole pipeline is unit-testable
with a fake detector — no torch/ultralytics needed in tests.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tile:
    """A tile rectangle in full-frame pixel coords, ``[x0, y0)`` origin.

    ``x1``/``y1`` are exclusive (slice bounds), so ``frame[y0:y1, x0:x1]``
    extracts the tile and ``(x1 - x0, y1 - y0)`` is its size.
    """

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def compute_tiles(
    width: int,
    height: int,
    *,
    tile: int = 1280,
    overlap: float = 0.2,
) -> list[Tile]:
    """Tile an ``width x height`` frame into overlapping ``tile``-sized squares.

    Origins step by ``tile * (1 - overlap)``; the final row/column is
    clamped flush to the frame edge so the whole frame is covered without a
    runt tile hanging off the side. Overlap (default 20%) ensures an object
    straddling a seam is wholly inside at least one tile, so :func:`merge_boxes`
    can dedup it rather than two half-boxes surviving.

    If the frame is already <= ``tile`` in a dimension, that dimension gets a
    single full-width/height tile (no point slicing a small frame).
    """
    if width <= 0 or height <= 0:
        return []
    if not 0.0 <= overlap < 1.0:
        raise ValueError(f"overlap must be in [0, 1); got {overlap}")
    if tile <= 0:
        raise ValueError(f"tile must be positive; got {tile}")

    def _origins(extent: int) -> list[int]:
        if extent <= tile:
            return [0]
        step = max(1, round(tile * (1.0 - overlap)))
        origins: list[int] = []
        pos = 0
        last = extent - tile
        while pos < last:
            origins.append(pos)
            pos += step
        origins.append(last)  # final tile flush to the far edge
        # de-dup if step landed exactly on `last`
        out: list[int] = []
        for o in origins:
            if not out or o != out[-1]:
                out.append(o)
        return out

    xs = _origins(width)
    ys = _origins(height)
    tiles: list[Tile] = []
    for y0 in ys:
        for x0 in xs:
            x1 = min(x0 + tile, width)
            y1 = min(y0 + tile, height)
            tiles.append(Tile(x0=x0, y0=y0, x1=x1, y1=y1))
    return tiles


@dataclass
class Box:
    """A detection in full-frame pixel coords (``x1<=x2``, ``y1<=y2``)."""

    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    cls: str
    track_id: str | None = None

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


def iou(a: Box, b: Box) -> float:
    """Intersection-over-union of two boxes. 0.0 if disjoint or degenerate."""
    ix1, iy1 = max(a.x1, b.x1), max(a.y1, b.y1)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    union = a.area + b.area - inter
    return inter / union if union > 0.0 else 0.0


def merge_boxes(boxes: Sequence[Box], *, iou_thresh: float = 0.45) -> list[Box]:
    """Class-aware global NMS across all tiles' detections.

    Adjacent tiles overlap, so the same object is often detected twice (once
    per tile). Greedy NMS keeps the highest-confidence box and suppresses
    lower-confidence boxes of the *same class* that overlap it beyond
    ``iou_thresh``. Different classes never suppress each other (a dog
    standing on a person's box is two real detections).

    Pure and order-independent w.r.t. ties (sorts by confidence desc).
    """
    survivors: list[Box] = []
    for box in sorted(boxes, key=lambda b: b.conf, reverse=True):
        if any(box.cls == s.cls and iou(box, s) > iou_thresh for s in survivors):
            continue
        survivors.append(box)
    return survivors


@dataclass
class _Track:
    track_id: str
    box: Box
    last_seen: int  # frame index of last update

    @property
    def cls(self) -> str:
        return self.box.cls


@dataclass
class IoUTracker:
    """Greedy IoU tracker over merged full-frame boxes across frames.

    Replaces ultralytics' ``model.track`` (unavailable once detection runs
    per-tile via ``predict``). For each frame, matches incoming boxes to
    existing tracks by highest IoU within the same class above
    ``iou_thresh``; unmatched boxes start new tracks. Tracks not seen for
    ``max_age`` frames are dropped.

    Stateful by design — feed frames in chronological order. ``update``
    stamps each box's ``track_id`` in place and returns the same list.
    """

    iou_thresh: float = 0.3
    max_age: int = 30
    _tracks: list[_Track] = field(default_factory=list)
    _next_id: int = 1
    _frame: int = 0

    def update(self, boxes: list[Box]) -> list[Box]:
        frame = self._frame
        self._frame += 1

        # Build all candidate (iou, box_idx, track) matches, then assign
        # greedily by descending IoU so each box and track is used once.
        candidates: list[tuple[float, int, _Track]] = []
        for bi, box in enumerate(boxes):
            for tr in self._tracks:
                if tr.cls != box.cls:
                    continue
                score = iou(box, tr.box)
                if score >= self.iou_thresh:
                    candidates.append((score, bi, tr))
        candidates.sort(key=lambda c: c[0], reverse=True)

        used_boxes: set[int] = set()
        used_tracks: set[str] = set()
        for _score, bi, tr in candidates:
            if bi in used_boxes or tr.track_id in used_tracks:
                continue
            used_boxes.add(bi)
            used_tracks.add(tr.track_id)
            tr.box = boxes[bi]
            tr.last_seen = frame
            boxes[bi].track_id = tr.track_id

        for bi, box in enumerate(boxes):
            if bi in used_boxes:
                continue
            tid = str(self._next_id)
            self._next_id += 1
            box.track_id = tid
            self._tracks.append(_Track(track_id=tid, box=box, last_seen=frame))

        # Age out stale tracks.
        self._tracks = [t for t in self._tracks if frame - t.last_seen <= self.max_age]
        return boxes


# ─── orchestration ──────────────────────────────────────────────────────

# A detector callable: takes a list of HxWx3 tile images, returns one list of
# Boxes per tile (in *tile-local* pixel coords). Kept abstract so tests pass a
# fake and production passes a YOLO-backed closure — no torch in the module.
TileDetectFn = Callable[[list], list[list[Box]]]


def detect_tiled(
    frame,
    detect_fn: TileDetectFn,
    *,
    tile: int = 1280,
    overlap: float = 0.2,
    iou_thresh: float = 0.45,
) -> list[Box]:
    """Run tiled detection on a single full-res ``frame`` (HxWx3 array-like).

    Slices the frame into overlapping tiles, runs ``detect_fn`` on the batch
    of tile crops (one call → batched inference on GPU), offsets each tile's
    boxes back into full-frame coords, and merges across seams. Returns
    full-frame :class:`Box` list *without* track_ids — feed the per-frame
    results through :class:`IoUTracker` to stamp those.
    """
    h, w = frame.shape[:2]
    tiles = compute_tiles(w, h, tile=tile, overlap=overlap)
    crops = [frame[t.y0 : t.y1, t.x0 : t.x1] for t in tiles]
    per_tile = detect_fn(crops)
    all_boxes: list[Box] = []
    for t, boxes in zip(tiles, per_tile, strict=True):
        for b in boxes:
            all_boxes.append(
                Box(
                    x1=b.x1 + t.x0,
                    y1=b.y1 + t.y0,
                    x2=b.x2 + t.x0,
                    y2=b.y2 + t.y0,
                    conf=b.conf,
                    cls=b.cls,
                )
            )
    return merge_boxes(all_boxes, iou_thresh=iou_thresh)
