"""Tests for the eval-corpus manifest (Epic #103, piece 1).

Run locally: ``uv run pytest scripts/dev/test_eval_corpus.py -q``
(CI's pytest covers services/ + shared/lib-python/ only.)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from eval_corpus import (  # noqa: E402
    ClipManifest,
    discover_manifests,
    read_manifest,
    write_manifest,
)


def test_roundtrip(tmp_path: Path):
    m = ClipManifest(
        name="stand1",
        camera="dahuapoolcam",
        subject_id="darin",
        outfit_id="o1",
        lighting="day",
        activity="stand",
        stream="main",
        fps=7.5,
        frame_count=50,
        captured_at="2026-05-30T09:00:00Z",
    )
    write_manifest(tmp_path, m)
    assert read_manifest(tmp_path, "stand1") == m


def test_unknown_fields_captured_in_extra(tmp_path: Path):
    m = ClipManifest.from_dict(
        {"name": "x", "camera": "c", "subject_id": "s", "weird_key": "v"}
    )
    assert m.extra == {"weird_key": "v"}


def test_discover_skips_clips_without_manifest(tmp_path: Path):
    write_manifest(tmp_path, ClipManifest(name="has", camera="c", subject_id="s"))
    (tmp_path / "legacy").mkdir()
    assert [m.name for m in discover_manifests(tmp_path)] == ["has"]


def test_missing_manifest_returns_none(tmp_path: Path):
    (tmp_path / "legacy").mkdir()
    assert read_manifest(tmp_path, "legacy") is None


def test_discover_empty_root(tmp_path: Path):
    assert discover_manifests(tmp_path / "nope") == []
