#!/usr/bin/env python
"""Run the identity-DAG simulator over a synthetic workload + sweep
resource configs (Epic 10.11.3b).

Costs are seeded from real measurements this project captured:
  * yolo11x detection on the Iris Plus iGPU — ~600ms/frame steady,
    ~35.8s one-time OpenVINO compile (warmup)
  * OSNet body re-ID — ~60ms, batchable
  * ArcFace face — ~200ms per head, not batchable
  * DINOv2 pet — ~80ms, batchable

Edit the SimPipeline costs / workload / configs to explore "what if".
Accuracy isn't simulated — pair this latency/utilization view with the
offline corpus probes (body_id_probe etc.) per (model, camera).

Usage:
    python scripts/dev/dag_sim.py
"""

from __future__ import annotations

from sentihome_preprocessor.pipelines.identity.sim import SimFrame, SimPipeline, sweep

PIPELINES = [
    SimPipeline("detect_yolo", frozenset({"frame"}), resource_class="gpu", base_ms=600, warmup_ms=35800),
    SimPipeline("face_arcface", frozenset({"person"}), resource_class="gpu", base_ms=200),
    SimPipeline(
        "body_id_osnet",
        frozenset({"person"}),
        resource_class="gpu",
        depends_on=("face_arcface",),
        batchable=True,
        base_ms=20,
        per_item_ms=10,
    ),
    SimPipeline(
        "pet_dinov2", frozenset({"dog", "cat"}), resource_class="gpu", batchable=True, base_ms=80
    ),
]


def _workload(n_frames: int = 30, people_per_frame: int = 2) -> list[SimFrame]:
    frames: list[SimFrame] = []
    for i in range(n_frames):
        # A "frame" token triggers detection; persons trigger the
        # identity pipelines downstream.
        dets = (
            ("frame", f"f{i}"),
            *(("person", f"p{j}") for j in range(people_per_frame)),
        )
        frames.append(SimFrame(ts=float(i), detections=dets))
    return frames


def main() -> None:
    frames = _workload()
    configs = [{"gpu": 1}, {"gpu": 2}, {"gpu": 4}]
    n_person = sum(1 for k, _ in frames[0].detections if k == "person")
    print(f"workload: {len(frames)} frames, {n_person} person(s)/frame\n")
    print(f"{'gpu_slots':>9} {'makespan_ms':>12} {'gpu_util':>9} {'calls':>6} {'dropped':>8}")
    for cfg, res in sweep(PIPELINES, frames, configs, budget_ms=None):
        print(
            f"{cfg['gpu']:>9} {res.makespan_ms:>12.0f} {res.utilization.get('gpu', 0):>9.2f} "
            f"{res.calls:>6} {len(res.dropped):>8}"
        )
    print("\nper-pipeline busy (gpu=2):")
    _, r2 = sweep(PIPELINES, frames, [{"gpu": 2}])[0]
    for name, ms in sorted(r2.per_pipeline_ms.items(), key=lambda kv: -kv[1]):
        print(f"  {name:>16}: {ms:>10.0f} ms")


if __name__ == "__main__":
    main()
