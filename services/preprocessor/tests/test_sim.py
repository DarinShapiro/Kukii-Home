"""Tests for the identity DAG simulator (Epic 10.11.3b)."""

from __future__ import annotations

from kukiihome_preprocessor.pipelines.identity.sim import (
    SimFrame,
    SimPipeline,
    simulate,
    sweep,
)


def _frame_person_dog() -> SimFrame:
    return SimFrame(ts=1.0, detections=(("person", "t1"), ("dog", "t2")))


def test_single_gpu_slot_serializes_independent_pipelines():
    face = SimPipeline("face", frozenset({"person"}), resource_class="gpu", base_ms=30)
    pet = SimPipeline("pet", frozenset({"dog"}), resource_class="gpu", base_ms=30)
    res = simulate([face, pet], [_frame_person_dog()], {"gpu": 1})
    assert res.makespan_ms == 60  # serialized on one slot
    assert res.utilization["gpu"] == 1.0  # fully busy
    assert res.calls == 2


def test_two_gpu_slots_run_in_parallel():
    face = SimPipeline("face", frozenset({"person"}), resource_class="gpu", base_ms=30)
    pet = SimPipeline("pet", frozenset({"dog"}), resource_class="gpu", base_ms=30)
    res = simulate([face, pet], [_frame_person_dog()], {"gpu": 2})
    assert res.makespan_ms == 30  # overlapped
    assert res.utilization["gpu"] == 1.0  # 60 busy / (30 * 2)


def test_batching_is_cheaper_for_multi_item():
    frame = SimFrame(
        ts=1.0,
        detections=tuple(("person", f"t{i}") for i in range(4)),
    )
    batched = SimPipeline(
        "body",
        frozenset({"person"}),
        resource_class="gpu",
        batchable=True,
        base_ms=10,
        per_item_ms=5,
    )
    unbatched = SimPipeline(
        "body",
        frozenset({"person"}),
        resource_class="gpu",
        batchable=False,
        base_ms=10,
        per_item_ms=5,
    )
    rb = simulate([batched], [frame], {"gpu": 1})
    ru = simulate([unbatched], [frame], {"gpu": 1})
    assert rb.makespan_ms == 30  # 10 + 5*4, one call
    assert ru.makespan_ms == 60  # 4 * (10+5), four calls
    assert rb.makespan_ms < ru.makespan_ms


def test_dependency_chain_runs_sequentially_via_real_topo_sort():
    """body depends_on face → _build_branches puts them in one branch,
    so they run sequentially even with spare slots (proves the sim uses
    the real router topo-sort)."""
    face = SimPipeline("face", frozenset({"person"}), resource_class="gpu", base_ms=20)
    body = SimPipeline(
        "body", frozenset({"person"}), resource_class="gpu", depends_on=("face",), base_ms=30
    )
    frame = SimFrame(ts=1.0, detections=(("person", "t1"),))
    res = simulate([face, body], [frame], {"gpu": 2})
    assert res.makespan_ms == 50  # 20 then 30, sequential despite 2 slots


def test_budget_drops_work_queued_past_deadline():
    """One gpu slot, two 30ms pipelines, budget 20ms: the first runs
    (starts at t=0), the second — which can't start until t=30 — is
    past the deadline and dropped."""
    face = SimPipeline("face", frozenset({"person"}), resource_class="gpu", base_ms=30)
    pet = SimPipeline("pet", frozenset({"dog"}), resource_class="gpu", base_ms=30)
    res = simulate([face, pet], [_frame_person_dog()], {"gpu": 1}, budget_ms=20.0)
    assert res.calls == 1  # only the first started
    assert len(res.dropped) == 1
    assert res.makespan_ms == 30  # the one that ran


def test_warmup_paid_once():
    face = SimPipeline(
        "face", frozenset({"person"}), resource_class="gpu", base_ms=10, warmup_ms=100
    )
    frames = [
        SimFrame(ts=1.0, detections=(("person", "t1"),)),
        SimFrame(ts=2.0, detections=(("person", "t1"),)),
    ]
    res = simulate([face], frames, {"gpu": 1})
    # First call 110 (warmup+base), second 10 → total 120.
    assert res.per_pipeline_ms["face"] == 120


def test_sweep_returns_result_per_config():
    face = SimPipeline("face", frozenset({"person"}), resource_class="gpu", base_ms=30)
    pet = SimPipeline("pet", frozenset({"dog"}), resource_class="gpu", base_ms=30)
    out = sweep([face, pet], [_frame_person_dog()], [{"gpu": 1}, {"gpu": 2}])
    assert [cfg for cfg, _ in out] == [{"gpu": 1}, {"gpu": 2}]
    assert out[0][1].makespan_ms == 60
    assert out[1][1].makespan_ms == 30
