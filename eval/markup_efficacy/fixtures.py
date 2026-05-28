"""Fixture loader + variant renderer for the markup efficacy harness.

A **fixture** is one captured camera frame paired with hand-labeled
ground-truth metadata. Lives under
``eval/markup_efficacy/fixtures/<fixture_id>.{jpg,yaml}``.

The YAML schema (see ``fixtures/EXAMPLE.yaml`` for a full annotated
example):

.. code-block:: yaml

    fixture_id: dahua_test_2026-05-27_pool_quiet
    camera_id: dahua_test
    captured_ts: 1779937813.859
    width: 704
    height: 480

    # The actors hypothetically known to the system at the time this
    # frame was captured. The harness substitutes these into the
    # question prompts.
    known_actors:
      - id: actor_alice
        name: Alice
      - id: actor_rex
        name: Rex

    # Identities the system would correctly identify in THIS frame if
    # face/pet/plate recognition were wired (Phase 10.4+). Drives the
    # annotated-variant renderer + the identity ground truth.
    identified_entities:
      - actor_id: actor_alice
        actor_name: Alice
        kind: person
        bbox: [0.18, 0.22, 0.42, 0.85]
        identity_method: face_arcface
        identity_confidence: 0.92
        detection_confidence: 0.95

    # Ground truth for the question battery.
    ground_truth:
      identity_present: "YES, Alice"
      anomaly_present: "NO"
      vehicle_count: 0
      behavior_summary: "A person is walking past the pool."
      alert_tier: TIER_0

The two variants the harness builds from this:

* **raw_with_metadata** -- the raw JPEG + a text block listing the
  identified_entities (as the JSON wire shape).
* **annotated_with_metadata** -- the JPEG with our markup pipeline
  applied (boxes around identified_entities) + the same text block.

Both variants get the same text grounding; the only difference is
whether the pixel channel carries the boxes too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sentihome_shared.preprocessor import IdentifiedEntity

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    camera_id: str
    captured_ts: float
    width: int
    height: int
    jpeg_path: Path
    known_actors: tuple[tuple[str, str], ...]
    """(actor_id, name) pairs known to the hypothetical system at capture time."""
    identified_entities: tuple[IdentifiedEntity, ...]
    ground_truth: dict[str, Any]

    def known_actor_names(self) -> list[str]:
        return [name for _, name in self.known_actors]

    def raw_jpeg(self) -> bytes:
        return self.jpeg_path.read_bytes()


def load_all_fixtures(directory: Path | None = None) -> list[Fixture]:
    """Load every fixture YAML in ``directory`` (default: the harness
    fixtures dir). Fixtures with a missing companion JPEG are skipped
    with a warning printed to stderr."""
    base = directory or FIXTURES_DIR
    out: list[Fixture] = []
    for yaml_path in sorted(base.glob("*.yaml")):
        if yaml_path.name == "EXAMPLE.yaml":
            continue
        try:
            f = _load_one(yaml_path)
        except FileNotFoundError as e:
            import sys

            print(f"WARN: skipping {yaml_path.name}: {e}", file=sys.stderr)  # noqa: T201
            continue
        out.append(f)
    return out


def _load_one(yaml_path: Path) -> Fixture:
    raw: dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

    fixture_id: str = raw["fixture_id"]
    jpeg_path = yaml_path.with_suffix(".jpg")
    if not jpeg_path.exists():
        raise FileNotFoundError(f"companion JPEG not found: {jpeg_path}")

    known_actors = tuple(
        (a["id"], a["name"]) for a in raw.get("known_actors", [])
    )

    entities_raw = raw.get("identified_entities", []) or []
    entities = tuple(
        IdentifiedEntity(
            frame_ts=raw["captured_ts"],
            kind=e["kind"],
            actor_id=e["actor_id"],
            actor_name=e["actor_name"],
            bbox=tuple(e["bbox"]),
            detection_confidence=e["detection_confidence"],
            identity_confidence=e["identity_confidence"],
            identity_method=e["identity_method"],
            track_id=e.get("track_id"),
        )
        for e in entities_raw
    )

    return Fixture(
        fixture_id=fixture_id,
        camera_id=raw["camera_id"],
        captured_ts=raw["captured_ts"],
        width=raw["width"],
        height=raw["height"],
        jpeg_path=jpeg_path,
        known_actors=known_actors,
        identified_entities=entities,
        ground_truth=raw.get("ground_truth", {}),
    )


def metadata_block_for(fixture: Fixture) -> str:
    """Render the text grounding block that goes into the VLM prompt
    alongside the image. Same content for both variants -- the only
    difference between variants is whether the pixel channel has
    boxes drawn or not."""
    if not fixture.identified_entities:
        return "No identified entities in this frame."
    lines: list[str] = ["Identified entities in this frame (per the recognition pipeline):"]
    for e in fixture.identified_entities:
        bbox_pct = f"({e.bbox[0]*100:.0f}-{e.bbox[2]*100:.0f}% x, {e.bbox[1]*100:.0f}-{e.bbox[3]*100:.0f}% y)"
        lines.append(
            f"  - {e.actor_name} ({e.kind}, "
            f"{e.identity_method}, identity_confidence={e.identity_confidence:.2f}) "
            f"at {bbox_pct}"
        )
    return "\n".join(lines)


def render_annotated_jpeg(fixture: Fixture) -> bytes:
    """Produce the annotated-variant JPEG bytes by running our actual
    markup pipeline against the fixture's identified_entities. Uses
    the same code path production frames would, so the harness
    measures what production actually does."""
    import cv2
    import numpy as np
    from sentihome_preprocessor.pipelines.markup import annotate_frame, encode_jpeg

    arr = np.frombuffer(fixture.raw_jpeg(), dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"failed to decode fixture {fixture.fixture_id}")
    annotated, _stats = annotate_frame(img, fixture.identified_entities)
    return encode_jpeg(annotated)
