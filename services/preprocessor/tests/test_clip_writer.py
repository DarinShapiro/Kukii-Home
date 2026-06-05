"""Clip writer — mux persisted JPEG frames into a playable MP4."""

from __future__ import annotations

import asyncio
import json

import av
import numpy as np
import pytest
from kukiihome_preprocessor.clip_writer import (
    _resolve_frame_paths,
    clip_dimensions,
    frame_count_in_event,
    get_or_build_clip,
    mux_jpegs_to_mp4,
)

# ─── Fixtures ────────────────────────────────────────────────────────


def _write_jpeg(path, width=320, height=240, color=(200, 200, 200)):
    """Write a solid-color JPEG via opencv (already a preprocessor dep)."""
    import cv2

    arr = np.zeros((height, width, 3), dtype=np.uint8)
    # opencv uses BGR; the color tuple is treated as-is — fine for the test.
    arr[:, :] = color
    ok, encoded = cv2.imencode(".jpg", arr)
    assert ok, "cv2.imencode failed"
    path.write_bytes(encoded.tobytes())


def _make_event(tmp_path, *, n_frames=6, fps=4.0, manifest=True):
    """Build a synthetic event directory: ``frame_*.jpg`` + manifest."""
    event_dir = tmp_path / "cam1" / "event_abc"
    event_dir.mkdir(parents=True)
    ts_base = 1_700_000_000.0
    frame_index = []
    for i in range(n_frames):
        name = f"frame_{i:05d}.jpg"
        _write_jpeg(event_dir / name, color=(20 * i, 50, 200 - 20 * i))
        frame_index.append(
            {
                "name": name,
                "ts": ts_base + i / fps,
                "has_motion": True,
            }
        )
    if manifest:
        (event_dir / "event.json").write_text(
            json.dumps(
                {
                    "camera_id": "cam1",
                    "frame_index": frame_index,
                }
            )
        )
    return event_dir


# ─── frame_count_in_event ────────────────────────────────────────────


def test_frame_count_in_event_zero_when_empty(tmp_path):
    empty = tmp_path / "cam" / "evt"
    empty.mkdir(parents=True)
    assert frame_count_in_event(empty) == 0


def test_frame_count_in_event_counts_jpgs(tmp_path):
    ed = _make_event(tmp_path, n_frames=5)
    assert frame_count_in_event(ed) == 5


# ─── _resolve_frame_paths ────────────────────────────────────────────


def test_resolve_frame_paths_uses_manifest_ts_order(tmp_path):
    ed = _make_event(tmp_path, n_frames=3, fps=4.0)
    # Tamper the manifest with reversed ts to verify sort happens
    manifest_path = ed / "event.json"
    manifest = json.loads(manifest_path.read_text())
    # Put frames in reversed order in manifest
    manifest["frame_index"].reverse()
    manifest_path.write_text(json.dumps(manifest))
    frames = _resolve_frame_paths(ed, manifest_path)
    # Result should still be ts-ascending (sort by ts)
    ts_seq = [t for _, t in frames]
    assert ts_seq == sorted(ts_seq)


def test_resolve_frame_paths_fallback_when_manifest_missing(tmp_path):
    ed = _make_event(tmp_path, n_frames=3, manifest=False)
    frames = _resolve_frame_paths(ed, ed / "nonexistent.json")
    assert len(frames) == 3
    # Synthetic ts at 4 fps: 0.0, 0.25, 0.5
    assert frames[0][1] == pytest.approx(0.0)
    assert frames[1][1] == pytest.approx(0.25)
    assert frames[2][1] == pytest.approx(0.5)


def test_resolve_frame_paths_ignores_missing_files(tmp_path):
    ed = _make_event(tmp_path, n_frames=3)
    # Delete one JPEG; the index still references it
    (ed / "frame_00001.jpg").unlink()
    frames = _resolve_frame_paths(ed, ed / "event.json")
    # Only the two existing files reach the resolver's output
    assert len(frames) == 2


# ─── mux + cache ─────────────────────────────────────────────────────


def test_mux_produces_playable_mp4(tmp_path):
    ed = _make_event(tmp_path, n_frames=4)
    out = mux_jpegs_to_mp4(event_dir=ed)
    assert out.exists()
    assert out.stat().st_size > 0
    # Real check: the output is a decodable container with one video stream
    with av.open(str(out), mode="r") as c:
        assert len(c.streams.video) == 1
        v = c.streams.video[0]
        assert v.codec.name in ("h264", "libx264")
        # Decode all frames — the encoder may concatenate, but at least one
        # decodable frame must be present.
        decoded = list(c.decode(video=0))
        assert len(decoded) >= 1


def test_mux_caches_subsequent_calls(tmp_path):
    ed = _make_event(tmp_path, n_frames=3)
    out1 = mux_jpegs_to_mp4(event_dir=ed)
    mtime_1 = out1.stat().st_mtime_ns
    # Second call: cached, mtime unchanged
    out2 = mux_jpegs_to_mp4(event_dir=ed)
    assert out2 == out1
    assert out2.stat().st_mtime_ns == mtime_1


def test_mux_raises_when_no_frames(tmp_path):
    empty = tmp_path / "cam" / "evt"
    empty.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        mux_jpegs_to_mp4(event_dir=empty)


def test_mux_explicit_output_path(tmp_path):
    ed = _make_event(tmp_path, n_frames=3)
    target = tmp_path / "elsewhere" / "named.mp4"
    out = mux_jpegs_to_mp4(event_dir=ed, output=target)
    assert out == target
    assert target.exists()


def test_clip_dimensions_returns_size_after_mux(tmp_path):
    ed = _make_event(tmp_path, n_frames=2)
    mux_jpegs_to_mp4(event_dir=ed)
    dims = clip_dimensions(ed)
    assert dims == (320, 240)


def test_clip_dimensions_returns_none_when_missing(tmp_path):
    ed = _make_event(tmp_path, n_frames=2)  # no mux call
    assert clip_dimensions(ed) is None


# ─── async wrapper coalesces concurrent builds ───────────────────────


@pytest.mark.asyncio
async def test_get_or_build_clip_coalesces_concurrent_callers(tmp_path):
    """Two simultaneous get_or_build_clip calls for the same event should
    produce ONE mux pass — the second one waits, then reads cached file."""
    ed = _make_event(tmp_path, n_frames=3)
    out1, out2 = await asyncio.gather(
        get_or_build_clip(event_dir=ed),
        get_or_build_clip(event_dir=ed),
    )
    assert out1 == out2
    assert out1.exists()
