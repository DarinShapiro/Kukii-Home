#!/usr/bin/env python
"""Markup-efficacy v0 harness.

Subcommands:
    capture --camera <id>     Pull a fresh frame from the running
                              preprocessor (localhost:8090) into
                              fixtures/<camera>_<ts>.jpg + a YAML stub.
                              You then hand-fill the YAML with ground
                              truth before runs.

    render                    Re-generate annotated-variant JPEGs for
                              every fixture from its identified_entities.
                              Output: fixtures/<id>.annotated.jpg.

    run [--model M]           Send every (fixture x variant x question)
                              to Claude. Caches results under
                              cache/<sha>.json so re-runs are free.

    report                    Read the cache + ground truth, produce
                              a comparison report at results/report.md
                              + results/raw.json.

Each subcommand is small and idempotent. Designed so the workflow
``capture -> label -> render -> run -> report`` is the day-to-day pattern.

Requires:
    ANTHROPIC_API_KEY in env for `run` (other subcommands work
    without it -- capture / render / report are key-free).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Local imports -- eval/ is not a package proper, so make the harness
# directory itself importable.
sys.path.insert(0, str(Path(__file__).parent))

from fixtures import (
    FIXTURES_DIR,
    Fixture,
    load_all_fixtures,
    metadata_block_for,
    render_annotated_jpeg,
)
from questions import QUESTION_BATTERY, Question, render_prompt

HARNESS_DIR = Path(__file__).parent
CACHE_DIR = HARNESS_DIR / "cache"
RESULTS_DIR = HARNESS_DIR / "results"

# Two variants we're comparing. Both get the same JSON metadata in the
# text prompt; only the pixels differ.
_VARIANTS = ("raw_with_metadata", "annotated_with_metadata")


# ─── capture ────────────────────────────────────────────────────────


def _cmd_capture(args: argparse.Namespace) -> int:
    """Pull the most-recent buffered frame for a camera and write a
    fixture stub. You then hand-fill the YAML's ground_truth +
    identified_entities sections before running."""
    import httpx

    base = args.preprocessor_url.rstrip("/")
    # Fetch current frame_window for the camera (1-second window
    # ending now), then download the most recent FrameRef.
    ts_end = time.time()
    ts_start = ts_end - 2.0
    url = (
        f"{base}/frame_window"
        f"?camera_id={args.camera}&ts_start={ts_start}&ts_end={ts_end}"
        f"&enrich=false"
    )
    with httpx.Client(timeout=10.0) as client:
        r = client.get(url)
        r.raise_for_status()
        window = r.json()
        if not window["frames"]:
            print(f"ERROR: no frames in window for {args.camera}.")  # noqa: T201
            print("  Is the preprocessor running with backend=rtsp")  # noqa: T201
            print("  and the camera publishing config events?")  # noqa: T201
            return 1
        # Take the most recent frame.
        frame = max(window["frames"], key=lambda f: f["ts"])
        ts = frame["ts"]
        width = frame["width"]
        height = frame["height"]
        # Fetch the actual JPEG bytes.
        jpeg = client.get(frame["uri"]).content

    fixture_id = f"{args.camera}_{int(ts)}_{args.label}" if args.label else f"{args.camera}_{int(ts)}"
    fixture_id = fixture_id.replace(" ", "_")
    jpeg_path = FIXTURES_DIR / f"{fixture_id}.jpg"
    yaml_path = FIXTURES_DIR / f"{fixture_id}.yaml"
    jpeg_path.write_bytes(jpeg)

    stub = {
        "fixture_id": fixture_id,
        "camera_id": args.camera,
        "captured_ts": ts,
        "width": width,
        "height": height,
        "known_actors": [
            {"id": "actor_TODO", "name": "TODO_name"},
        ],
        "identified_entities": [],
        "ground_truth": {
            "identity_present": "TODO (e.g. 'YES, Alice' or 'NO')",
            "anomaly_present": "TODO",
            "vehicle_count": 0,
            "behavior_summary": "TODO",
            "alert_tier": "TIER_0",
        },
    }
    yaml_path.write_text(yaml.dump(stub, sort_keys=False), encoding="utf-8")
    print(f"captured: {jpeg_path.relative_to(HARNESS_DIR)}")  # noqa: T201
    print(f"stub:     {yaml_path.relative_to(HARNESS_DIR)}")  # noqa: T201
    print("\nNext: edit the YAML to fill in known_actors, identified_entities, and ground_truth.")  # noqa: T201
    return 0


# ─── render ─────────────────────────────────────────────────────────


def _cmd_render(_args: argparse.Namespace) -> int:
    """Generate the annotated-variant JPEG for each fixture from its
    identified_entities. Idempotent -- overwrites existing output."""
    fixtures = load_all_fixtures()
    if not fixtures:
        print("No fixtures to render. Capture some first.")  # noqa: T201
        return 0
    n = 0
    for f in fixtures:
        if not f.identified_entities:
            # Annotated variant is identical to raw when no entities
            # are recognized. Still write it for variant-uniformity.
            annotated_path = f.jpeg_path.with_suffix(".annotated.jpg")
            annotated_path.write_bytes(f.raw_jpeg())
            print(f"  {f.fixture_id}: no entities -> annotated = raw")  # noqa: T201
            continue
        annotated_path = f.jpeg_path.with_suffix(".annotated.jpg")
        annotated_path.write_bytes(render_annotated_jpeg(f))
        n += 1
        print(f"  {f.fixture_id}: {len(f.identified_entities)} entities -> {annotated_path.name}")  # noqa: T201
    print(f"\nrendered {n} annotated variants ({len(fixtures)} total fixtures)")  # noqa: T201
    return 0


# ─── run ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _RunKey:
    fixture_id: str
    variant: str
    question_id: str
    model: str

    def cache_path(self) -> Path:
        h = hashlib.sha256(
            f"{self.fixture_id}|{self.variant}|{self.question_id}|{self.model}".encode()
        ).hexdigest()[:16]
        return CACHE_DIR / f"{h}.json"


def _cmd_run(args: argparse.Namespace) -> int:
    """Run every (fixture x variant x question) through the VLM,
    caching responses."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in environment.")  # noqa: T201
        return 2

    fixtures = load_all_fixtures()
    if not fixtures:
        print("No fixtures to run. Capture + label some first.")  # noqa: T201
        return 0

    CACHE_DIR.mkdir(exist_ok=True)
    asyncio.run(_run_all(fixtures, args.model, api_key, force=args.force))
    return 0


async def _run_all(
    fixtures: list[Fixture], model: str, api_key: str, *, force: bool
) -> None:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)

    total = len(fixtures) * len(_VARIANTS) * len(QUESTION_BATTERY)
    done = 0
    cache_hits = 0
    print(f"running {total} (fixture x variant x question) combinations ...")  # noqa: T201

    for fixture in fixtures:
        for variant in _VARIANTS:
            jpeg_bytes = _jpeg_for_variant(fixture, variant)
            jpeg_b64 = base64.standard_b64encode(jpeg_bytes).decode("ascii")
            for q in QUESTION_BATTERY:
                key = _RunKey(
                    fixture_id=fixture.fixture_id,
                    variant=variant,
                    question_id=q.id,
                    model=model,
                )
                cache_path = key.cache_path()
                done += 1
                if cache_path.exists() and not force:
                    cache_hits += 1
                    continue

                try:
                    response_text, usage = await _ask_vlm(
                        client=client,
                        model=model,
                        question=q,
                        fixture=fixture,
                        jpeg_b64=jpeg_b64,
                    )
                except Exception as e:
                    print(f"  [{done}/{total}] FAILED: {key.fixture_id} {variant} {q.id}: {e}")  # noqa: T201
                    continue

                cache_path.write_text(
                    json.dumps(
                        {
                            "fixture_id": fixture.fixture_id,
                            "variant": variant,
                            "question_id": q.id,
                            "model": model,
                            "response": response_text,
                            "usage": usage,
                            "ts": time.time(),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(f"  [{done}/{total}] {fixture.fixture_id} {variant} {q.id}")  # noqa: T201

    print(f"\ndone. cache hits: {cache_hits}/{total}")  # noqa: T201


def _jpeg_for_variant(fixture: Fixture, variant: str) -> bytes:
    if variant == "raw_with_metadata":
        return fixture.raw_jpeg()
    if variant == "annotated_with_metadata":
        annotated_path = fixture.jpeg_path.with_suffix(".annotated.jpg")
        if annotated_path.exists():
            return annotated_path.read_bytes()
        # Fallback: re-render on the fly.
        return render_annotated_jpeg(fixture)
    raise ValueError(f"unknown variant {variant!r}")


async def _ask_vlm(
    *,
    client,
    model: str,
    question: Question,
    fixture: Fixture,
    jpeg_b64: str,
) -> tuple[str, dict]:
    """One VLM call. Returns (response_text, usage_dict)."""
    rendered = render_prompt(
        question,
        camera_id=fixture.camera_id,
        known_actor_names=fixture.known_actor_names(),
    )
    full_text = (
        metadata_block_for(fixture)
        + "\n\n"
        + rendered
    )
    msg = await client.messages.create(
        model=model,
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": jpeg_b64,
                        },
                    },
                    {"type": "text", "text": full_text},
                ],
            }
        ],
    )
    response_text = "".join(
        block.text for block in msg.content if hasattr(block, "text")
    ).strip()
    usage = {
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
    }
    return response_text, usage


# ─── report ─────────────────────────────────────────────────────────


def _cmd_report(args: argparse.Namespace) -> int:
    """Aggregate cached responses + ground truth into a delta report."""
    fixtures = load_all_fixtures()
    if not fixtures:
        print("No fixtures.")  # noqa: T201
        return 0

    RESULTS_DIR.mkdir(exist_ok=True)
    rows: list[dict[str, Any]] = []
    for fixture in fixtures:
        for variant in _VARIANTS:
            for q in QUESTION_BATTERY:
                key = _RunKey(
                    fixture_id=fixture.fixture_id,
                    variant=variant,
                    question_id=q.id,
                    model=args.model,
                )
                cache_path = key.cache_path()
                if not cache_path.exists():
                    continue
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                ground_truth = fixture.ground_truth.get(q.ground_truth_key)
                response = cached["response"]
                # Crude string-match scoring. Manual override expected
                # for v0; this just produces a starting signal.
                matches = _crude_match(response, ground_truth, q.category)
                rows.append(
                    {
                        "fixture_id": fixture.fixture_id,
                        "variant": variant,
                        "question_id": q.id,
                        "category": q.category,
                        "ground_truth": ground_truth,
                        "response": response,
                        "matches": matches,
                        "input_tokens": cached["usage"]["input_tokens"],
                        "output_tokens": cached["usage"]["output_tokens"],
                    }
                )

    raw_path = RESULTS_DIR / "raw.json"
    raw_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    report = _build_report(rows)
    report_path = RESULTS_DIR / "report.md"
    report_path.write_text(report, encoding="utf-8")
    print(report)  # noqa: T201
    print(f"\nfull data: {raw_path}")  # noqa: T201
    print(f"report:    {report_path}")  # noqa: T201
    return 0


def _crude_match(response: str, ground_truth: Any, category: str) -> bool:
    """First-pass crude scoring. Manual review of raw.json is expected
    for v0; this just gets some signal flowing."""
    if ground_truth is None or response is None:
        return False
    if isinstance(ground_truth, int):
        # Counting questions -- extract the first integer.
        import re

        m = re.search(r"\d+", response)
        if m is None:
            return False
        return int(m.group()) == ground_truth
    gt = str(ground_truth).upper().strip()
    rt = response.upper().strip()
    if category in {"identity", "anomaly", "alert_tier"}:
        # Single-line categorical answer -- first word/segment match.
        return gt.split(",")[0].strip() in rt[:50]
    # behavior -- too freeform for crude string match; just flag the
    # row for manual review.
    return False


def _build_report(rows: list[dict[str, Any]]) -> str:
    by_variant: dict[str, dict[str, list[bool]]] = {}
    tokens_by_variant: dict[str, list[int]] = {}
    for r in rows:
        v = r["variant"]
        c = r["category"]
        by_variant.setdefault(v, {}).setdefault(c, []).append(r["matches"])
        tokens_by_variant.setdefault(v, []).append(r["input_tokens"])

    lines = [
        "# Markup Efficacy v0 -- Comparison Report",
        "",
        "## Crude accuracy by variant x category",
        "",
        "*(Note: 'behavior' rows never match by crude string compare; review raw.json manually.)*",
        "",
        "| Category | raw_with_metadata | annotated_with_metadata | Δ |",
        "| --- | --- | --- | --- |",
    ]
    categories = sorted({r["category"] for r in rows})
    for c in categories:
        raw_scores = by_variant.get("raw_with_metadata", {}).get(c, [])
        ann_scores = by_variant.get("annotated_with_metadata", {}).get(c, [])
        raw_acc = sum(raw_scores) / len(raw_scores) if raw_scores else 0.0
        ann_acc = sum(ann_scores) / len(ann_scores) if ann_scores else 0.0
        delta = ann_acc - raw_acc
        lines.append(
            f"| {c} | {raw_acc:.0%} ({len(raw_scores)}) | "
            f"{ann_acc:.0%} ({len(ann_scores)}) | {delta:+.0%} |"
        )

    lines.extend(
        [
            "",
            "## Input-token cost by variant",
            "",
            "Pixel-burned annotations slightly inflate the JPEG; this table",
            "shows whether the cost is meaningful in tokens.",
            "",
            "| Variant | mean input tokens | n |",
            "| --- | --- | --- |",
        ]
    )
    for v in _VARIANTS:
        toks = tokens_by_variant.get(v, [])
        mean = sum(toks) / len(toks) if toks else 0
        lines.append(f"| {v} | {mean:.0f} | {len(toks)} |")

    lines.extend(
        [
            "",
            "## Interpretation guide",
            "",
            "- Δ positive across categories: annotated channel helps. Keep markup.",
            "- Δ ~zero across categories: JSON metadata is doing the work alone.",
            "  Consider dropping the markup pipeline.",
            "- Δ negative on counting / behavior: markup is confusing the VLM.",
            "  Investigate (could be the box pixels themselves, or the labels).",
            "",
            "## Manual review",
            "",
            "Crude string match underestimates accuracy on freeform responses.",
            "Open `raw.json` and re-score by hand for the behavior questions",
            "before drawing conclusions.",
        ]
    )
    return "\n".join(lines)


# ─── main ───────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_capture = sub.add_parser("capture", help="capture a fresh fixture")
    p_capture.add_argument("--camera", required=True)
    p_capture.add_argument(
        "--preprocessor-url", default="http://localhost:8090"
    )
    p_capture.add_argument(
        "--label",
        default=None,
        help="optional short label appended to the fixture_id",
    )
    p_capture.set_defaults(func=_cmd_capture)

    p_render = sub.add_parser("render", help="render annotated variants")
    p_render.set_defaults(func=_cmd_render)

    p_run = sub.add_parser("run", help="run VLM over all fixtures x variants x questions")
    p_run.add_argument(
        "--model",
        default="claude-opus-4-5",
        help="anthropic model name",
    )
    p_run.add_argument(
        "--force",
        action="store_true",
        help="ignore the cache; re-run every combination",
    )
    p_run.set_defaults(func=_cmd_run)

    p_report = sub.add_parser("report", help="aggregate cached responses into a report")
    p_report.add_argument(
        "--model",
        default="claude-opus-4-5",
        help="match the model used in run (cache keys include the model)",
    )
    p_report.set_defaults(func=_cmd_report)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
