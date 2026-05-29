"""Discrete-event simulator for the identity DAG (Epic 10.11.3b).

Iterate on configuration — pipeline placement, per-resource pool sizes,
batching, budget — *without* real models or hardware. Answers
questions like "gait on gpu:0 next to face, or its own pool?",
"does batching 30 crops beat 1-at-a-time on the iGPU?", "what budget
keeps p95 under 2s without dropping the high-value branch?" in
milliseconds instead of re-cabling hardware.

Faithfulness: the DAG structure comes from the *real* router topo-sort
(:func:`router._build_branches`), and the resource model mirrors
:class:`scheduling.ResourcePool` (per-class capacity, queue, slot
hold-for-duration). Only the clock is virtual and inference is replaced
by a cost model — so the sim and production share the structure, and a
branch/scheduling change shows up in both.

Cost model per pipeline call: ``warmup_once + base_ms + per_item_ms*n``,
where a non-batchable pipeline pays ``base+per_item`` per item (N
calls) and a batchable one pays it once for the whole batch. Seed it
from real measurements (OSNet steady-state, the OpenVINO 35.8s compile,
``step_timings_ms``).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from sentihome_preprocessor.pipelines.identity.router import _build_branches


@dataclass(frozen=True)
class SimPipeline:
    """A pipeline's cost + placement profile for the simulator.

    ``name`` / ``depends_on`` feed the real ``_build_branches`` so the
    simulated DAG matches production exactly."""

    name: str
    triggers_on: frozenset[str]
    resource_class: str = "gpu"
    depends_on: tuple[str, ...] = ()
    batchable: bool = False
    base_ms: float = 0.0
    per_item_ms: float = 0.0
    warmup_ms: float = 0.0

    def cost(self, n_items: int, *, first_call: bool) -> float:
        warm = self.warmup_ms if first_call else 0.0
        if self.batchable:
            return warm + self.base_ms + self.per_item_ms * n_items
        return warm + n_items * (self.base_ms + self.per_item_ms)


@dataclass(frozen=True)
class SimFrame:
    """One frame's detections: ``(kind, track_id)`` pairs."""

    ts: float
    detections: tuple[tuple[str, str], ...]


@dataclass
class SimResult:
    makespan_ms: float = 0.0
    """Wall-clock to finish all identity work for the window."""
    per_pipeline_ms: dict[str, float] = field(default_factory=dict)
    """Total busy time attributed to each pipeline."""
    per_resource_busy_ms: dict[str, float] = field(default_factory=dict)
    utilization: dict[str, float] = field(default_factory=dict)
    """busy / (makespan * pool_size) per resource class — 0..1."""
    dropped: list[str] = field(default_factory=list)
    """``pipeline@frame_ts`` entries shed by the budget deadline."""
    calls: int = 0


@dataclass
class _Job:
    """One branch instance on one frame: an ordered list of
    ``(pipeline, n_items)`` steps to run sequentially."""

    steps: list[tuple[SimPipeline, int]]
    deadline_ms: float
    frame_ts: float
    idx: int = 0


def simulate(
    pipelines: list[SimPipeline],
    frames: list[SimFrame],
    resources: dict[str, int],
    *,
    budget_ms: float | None = None,
) -> SimResult:
    """Run the discrete-event simulation and return aggregate metrics.

    Mirrors the router: per (frame x triggered branch) a sequential
    job; each step holds one slot of its resource class for its modeled
    cost; independent jobs contend for slots up to each class's
    capacity. A job step not *started* by ``budget_ms`` is dropped
    (and the rest of its branch with it), exactly like the live budget.
    """
    branches = _build_branches(pipelines)
    by_name_first: set[str] = set()  # pipelines that have run ≥1 call (warmup)

    # Build jobs: one per (frame, triggering branch).
    jobs: list[_Job] = []
    for frame in frames:
        kinds = {k for k, _ in frame.detections}
        for branch in branches:
            steps: list[tuple[SimPipeline, int]] = []
            for p in branch:
                hit = p.triggers_on & kinds
                if not hit:
                    continue
                n = sum(1 for k, _ in frame.detections if k in hit)
                if n:
                    steps.append((p, n))
            if steps:
                jobs.append(
                    _Job(steps=steps, deadline_ms=budget_ms or float("inf"), frame_ts=frame.ts)
                )

    # ready queue per resource class: list of jobs whose current step
    # wants that class. FIFO via insertion order.
    ready: dict[str, list[_Job]] = {}
    for job in jobs:
        rc = job.steps[0][0].resource_class
        ready.setdefault(rc, []).append(job)

    result = SimResult()
    busy: dict[str, float] = {}
    events: list[tuple[float, int, str, _Job]] = []  # (time, seq, class, job)
    seq = 0
    now = 0.0

    def dispatch(t: float) -> None:
        nonlocal seq
        for rc, queue in ready.items():
            cap = available.get(rc, resources.get(rc, 1))
            i = 0
            while i < len(queue) and available.get(rc, cap) > 0:
                job = queue[i]
                pipeline, n = job.steps[job.idx]
                if t > job.deadline_ms:
                    result.dropped.append(f"{pipeline.name}@{job.frame_ts:.3f}")
                    queue.pop(i)
                    continue
                # acquire slot
                available[rc] = available.get(rc, cap) - 1
                first = pipeline.name not in by_name_first
                by_name_first.add(pipeline.name)
                dur = pipeline.cost(n, first_call=first)
                result.per_pipeline_ms[pipeline.name] = (
                    result.per_pipeline_ms.get(pipeline.name, 0.0) + dur
                )
                busy[rc] = busy.get(rc, 0.0) + dur
                result.calls += 1
                heapq.heappush(events, (t + dur, seq, rc, job))
                seq += 1
                queue.pop(i)
            # (do not advance i past popped entries; popping shifts list)

    available = {rc: resources.get(rc, 1) for rc in {p.resource_class for p in pipelines}}
    dispatch(now)
    while events:
        t, _s, rc, job = heapq.heappop(events)
        now = t
        available[rc] = available.get(rc, 0) + 1
        job.idx += 1
        if job.idx < len(job.steps):
            nrc = job.steps[job.idx][0].resource_class
            ready.setdefault(nrc, []).append(job)
        dispatch(now)

    result.makespan_ms = round(now, 3)
    for rc, b in busy.items():
        result.per_resource_busy_ms[rc] = round(b, 3)
        cap = resources.get(rc, 1)
        denom = result.makespan_ms * cap
        result.utilization[rc] = round(b / denom, 3) if denom > 0 else 0.0
    result.per_pipeline_ms = {k: round(v, 3) for k, v in result.per_pipeline_ms.items()}
    return result


def sweep(
    pipelines: list[SimPipeline],
    frames: list[SimFrame],
    configs: list[dict[str, int]],
    *,
    budget_ms: float | None = None,
) -> list[tuple[dict[str, int], SimResult]]:
    """Run the same workload under each resource config; return
    ``(config, result)`` pairs for picking the latency/utilization
    sweet spot. (Accuracy is supplied separately from the offline
    corpus probes — the sim covers latency x utilization.)"""
    return [(cfg, simulate(pipelines, frames, cfg, budget_ms=budget_ms)) for cfg in configs]
