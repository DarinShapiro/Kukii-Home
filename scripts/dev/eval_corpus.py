#!/usr/bin/env python
"""Corpus manifest for the rigorous identity-eval framework (Epic 10.11 / #103).

Every saved clip under ``face_debug/corpus/<name>/`` gets a
``manifest.json`` describing the controlled conditions it was captured
under. This is what turns ad-hoc walk corpora into a *labeled dataset*
we can reach defensible conclusions from — the gap the cross-outfit
finding exposed (results were "directional" because conditions weren't
recorded/controlled).

A manifest carries the axes an experiment controls or varies:
  * subject_id  — WHO (enables genuine-vs-imposter separability, the
    thing self-recall can't measure)
  * outfit_id   — isolates clothing from identity
  * camera / lighting / activity / stream / fps — the conditions a
    conclusion must be conditioned on

The eval harness (next piece) groups clips by these fields to compute
precision/recall/separability per (model, camera, lighting, ...), and
to build clean controlled comparisons (same subject+camera, differ only
in outfit; etc.).

Pure stdlib + dataclass — no model/torch deps, so it stays importable
anywhere and fast to test.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

MANIFEST_NAME = "manifest.json"

# Controlled-axis vocabularies. Kept permissive (free-form allowed) but
# these are the expected values so the harness can group reliably.
LIGHTING = ("day", "dusk", "night", "ir")
ACTIVITY = ("walk", "stand", "approach", "loiter", "pass")
STREAM = ("main", "sub")


@dataclass
class ClipManifest:
    """Describes one captured clip's conditions. ``subject_id`` is the
    ground-truth identity label (or ``"unknown"`` / ``"imposter"``)."""

    name: str
    camera: str
    subject_id: str
    outfit_id: str = "default"
    lighting: str = "day"
    activity: str = "walk"
    stream: str = "main"
    fps: float = 0.0
    captured_at: str = ""  # ISO-8601; caller stamps (clock is injectable)
    frame_count: int = 0
    notes: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict) -> ClipManifest:
        known = {f for f in cls.__dataclass_fields__}  # noqa: C416
        kwargs = {k: v for k, v in d.items() if k in known}
        extra = {k: str(v) for k, v in d.items() if k not in known}
        if extra:
            kwargs.setdefault("extra", {}).update(extra)
        return cls(**kwargs)


def manifest_path(corpus_root: Path, name: str) -> Path:
    return corpus_root / name / MANIFEST_NAME


def write_manifest(corpus_root: Path, m: ClipManifest) -> Path:
    """Write ``manifest.json`` into the clip's dir (created if needed)."""
    p = manifest_path(corpus_root, m.name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(m.to_json(), encoding="utf-8")
    return p


def read_manifest(corpus_root: Path, name: str) -> ClipManifest | None:
    """Load a clip's manifest, or ``None`` if it has none (legacy clip)."""
    p = manifest_path(corpus_root, name)
    if not p.is_file():
        return None
    return ClipManifest.from_dict(json.loads(p.read_text(encoding="utf-8")))


def discover_manifests(corpus_root: Path) -> list[ClipManifest]:
    """All manifested clips under ``corpus_root``, sorted by name.
    Clips without a manifest are skipped (the harness reports them)."""
    if not corpus_root.is_dir():
        return []
    out: list[ClipManifest] = []
    for child in sorted(corpus_root.iterdir()):
        if child.is_dir():
            m = read_manifest(corpus_root, child.name)
            if m is not None:
                out.append(m)
    return out
