"""Unit tests for StepTimings."""

from __future__ import annotations

import time

from kukiihome_shared.timing import StepTimings


def test_span_records_duration():
    t = StepTimings()
    with t.span("work"):
        time.sleep(0.01)
    d = t.as_dict()
    assert "work" in d
    assert d["work"] >= 9.0  # ~10ms, allow scheduler slop


def test_span_accumulates_same_name():
    t = StepTimings()
    for _ in range(3):
        with t.span("loop"):
            time.sleep(0.005)
    # Three ~5ms spans roll up under one name.
    assert t.as_dict()["loop"] >= 13.0


def test_records_duration_even_on_exception():
    t = StepTimings()
    try:
        with t.span("boom"):
            time.sleep(0.005)
            raise ValueError("x")
    except ValueError:
        pass
    assert t.as_dict()["boom"] >= 4.0  # timed despite the raise


def test_record_adds_external_measurement():
    t = StepTimings()
    t.record("vlm_inference", 1234.5)
    t.record("vlm_inference", 5.5)  # accumulates
    assert t.as_dict()["vlm_inference"] == 1240.0


def test_as_dict_is_a_copy():
    t = StepTimings()
    t.record("a", 1.0)
    d = t.as_dict()
    d["a"] = 999.0
    assert t.as_dict()["a"] == 1.0  # mutating the copy doesn't leak back


def test_total_and_bool():
    t = StepTimings()
    assert not t
    t.record("a", 10.0)
    t.record("b", 5.0)
    assert t
    assert t.total_ms() == 15.0
