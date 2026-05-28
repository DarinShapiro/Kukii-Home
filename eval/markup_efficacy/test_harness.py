"""Unit tests for the markup-efficacy harness.

These cover the fixture loader + prompt rendering. The actual VLM
call path (``run``) needs an API key and is exercised manually.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from fixtures import (
    load_all_fixtures,
    metadata_block_for,
    render_annotated_jpeg,
)
from questions import QUESTION_BATTERY, render_prompt


def _tiny_jpeg(w: int = 320, h: int = 240) -> bytes:
    img = np.full((h, w, 3), 80, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", img)
    assert ok
    return jpeg.tobytes()


def _write_fixture(tmpdir: Path, fixture_id: str, payload: dict) -> None:
    (tmpdir / f"{fixture_id}.yaml").write_text(
        yaml.dump(payload, sort_keys=False), encoding="utf-8"
    )
    (tmpdir / f"{fixture_id}.jpg").write_bytes(_tiny_jpeg())


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_load_fixture_with_identified_entities(fixture_dir: Path):
    _write_fixture(
        fixture_dir,
        "test_alice_at_door",
        {
            "fixture_id": "test_alice_at_door",
            "camera_id": "reolink_front",
            "captured_ts": 1779938468.989,
            "width": 320,
            "height": 240,
            "known_actors": [
                {"id": "actor_alice", "name": "Alice"},
                {"id": "actor_rex", "name": "Rex"},
            ],
            "identified_entities": [
                {
                    "actor_id": "actor_alice",
                    "actor_name": "Alice",
                    "kind": "person",
                    "bbox": [0.42, 0.18, 0.55, 0.78],
                    "identity_method": "face_arcface",
                    "identity_confidence": 0.93,
                    "detection_confidence": 0.95,
                    "track_id": "t-alice-1",
                }
            ],
            "ground_truth": {
                "identity_present": "YES, Alice",
                "anomaly_present": "NO",
                "vehicle_count": 0,
                "behavior_summary": "Alice arrives at the front door.",
                "alert_tier": "TIER_0",
            },
        },
    )

    fixtures = load_all_fixtures(fixture_dir)
    assert len(fixtures) == 1
    f = fixtures[0]
    assert f.fixture_id == "test_alice_at_door"
    assert f.camera_id == "reolink_front"
    assert f.known_actor_names() == ["Alice", "Rex"]
    assert len(f.identified_entities) == 1
    assert f.identified_entities[0].actor_name == "Alice"
    assert f.identified_entities[0].kind == "person"
    assert f.ground_truth["alert_tier"] == "TIER_0"


def test_load_fixture_with_no_entities(fixture_dir: Path):
    """A fixture with zero identified entities is valid -- represents
    the common case where YOLO detected nothing nameable."""
    _write_fixture(
        fixture_dir,
        "test_empty_scene",
        {
            "fixture_id": "test_empty_scene",
            "camera_id": "dahua_test",
            "captured_ts": 1234.5,
            "width": 320,
            "height": 240,
            "known_actors": [],
            "identified_entities": [],
            "ground_truth": {"alert_tier": "TIER_0"},
        },
    )
    fixtures = load_all_fixtures(fixture_dir)
    assert len(fixtures) == 1
    assert fixtures[0].identified_entities == ()


def test_load_skips_example_yaml(fixture_dir: Path):
    """EXAMPLE.yaml in the real fixtures dir is documentation, not
    runtime data."""
    (fixture_dir / "EXAMPLE.yaml").write_text("# example", encoding="utf-8")
    fixtures = load_all_fixtures(fixture_dir)
    assert fixtures == []


def test_load_skips_yaml_without_jpeg(fixture_dir: Path, capsys):
    """Missing JPEG -> skip with warning, don't crash."""
    (fixture_dir / "orphan.yaml").write_text(
        yaml.dump(
            {
                "fixture_id": "orphan",
                "camera_id": "x",
                "captured_ts": 0.0,
                "width": 1,
                "height": 1,
            }
        ),
        encoding="utf-8",
    )
    fixtures = load_all_fixtures(fixture_dir)
    assert fixtures == []
    captured = capsys.readouterr()
    assert "skipping" in captured.err


def test_metadata_block_with_entities(fixture_dir: Path):
    _write_fixture(
        fixture_dir,
        "test_metadata",
        {
            "fixture_id": "test_metadata",
            "camera_id": "x",
            "captured_ts": 0.0,
            "width": 320,
            "height": 240,
            "known_actors": [{"id": "a", "name": "Alice"}],
            "identified_entities": [
                {
                    "actor_id": "a",
                    "actor_name": "Alice",
                    "kind": "person",
                    "bbox": [0.1, 0.2, 0.3, 0.8],
                    "identity_method": "face_arcface",
                    "identity_confidence": 0.92,
                    "detection_confidence": 0.95,
                }
            ],
            "ground_truth": {},
        },
    )
    f = load_all_fixtures(fixture_dir)[0]
    block = metadata_block_for(f)
    assert "Alice" in block
    assert "person" in block
    assert "face_arcface" in block


def test_metadata_block_empty():
    """No entities -> a short 'no entities' line, not an empty string
    (the VLM should still get *something*)."""
    from fixtures import Fixture

    f = Fixture(
        fixture_id="x",
        camera_id="x",
        captured_ts=0.0,
        width=1,
        height=1,
        jpeg_path=Path("/nonexistent"),
        known_actors=(),
        identified_entities=(),
        ground_truth={},
    )
    block = metadata_block_for(f)
    assert "No identified entities" in block


def test_render_annotated_jpeg_no_entities_returns_valid_jpeg(fixture_dir: Path):
    _write_fixture(
        fixture_dir,
        "test_render_empty",
        {
            "fixture_id": "test_render_empty",
            "camera_id": "x",
            "captured_ts": 0.0,
            "width": 320,
            "height": 240,
            "known_actors": [],
            "identified_entities": [],
            "ground_truth": {},
        },
    )
    f = load_all_fixtures(fixture_dir)[0]
    out = render_annotated_jpeg(f)
    # Annotation pipeline with no entities returns the raw bytes
    # (annotate_frame short-circuits). Just verify they decode.
    arr = np.frombuffer(out, dtype=np.uint8)
    decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert decoded is not None


def test_render_annotated_jpeg_with_entity_changes_pixels(fixture_dir: Path):
    _write_fixture(
        fixture_dir,
        "test_render_alice",
        {
            "fixture_id": "test_render_alice",
            "camera_id": "x",
            "captured_ts": 0.0,
            "width": 320,
            "height": 240,
            "known_actors": [{"id": "a", "name": "Alice"}],
            "identified_entities": [
                {
                    "actor_id": "a",
                    "actor_name": "Alice",
                    "kind": "person",
                    "bbox": [0.2, 0.2, 0.6, 0.8],
                    "identity_method": "face_arcface",
                    "identity_confidence": 0.92,
                    "detection_confidence": 0.95,
                }
            ],
            "ground_truth": {},
        },
    )
    f = load_all_fixtures(fixture_dir)[0]
    raw = f.raw_jpeg()
    annotated = render_annotated_jpeg(f)
    # JPEG bytes should differ (annotation modifies pixels).
    assert raw != annotated


# ─── questions ──────────────────────────────────────────────────────


def test_question_battery_has_all_required_categories():
    cats = {q.category for q in QUESTION_BATTERY}
    assert {"identity", "anomaly", "counting", "behavior", "alert_tier"} <= cats


def test_render_prompt_substitutes_actors():
    q = next(q for q in QUESTION_BATTERY if q.category == "identity")
    rendered = render_prompt(q, camera_id="cam_a", known_actor_names=["Alice", "Rex"])
    assert "Alice" in rendered
    assert "Rex" in rendered


def test_render_prompt_handles_empty_actors():
    q = next(q for q in QUESTION_BATTERY if q.category == "identity")
    rendered = render_prompt(q, camera_id="cam_a", known_actor_names=[])
    assert "(none)" in rendered
