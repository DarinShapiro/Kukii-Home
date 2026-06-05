"""Keyframe selection — VLM-fps downsample (decoupled from tracking-fps)."""

from __future__ import annotations

from types import SimpleNamespace

from kukiihome_preprocessor.keyframes import select_keyframes


def _f(ts):
    return SimpleNamespace(ts=float(ts))


def test_returns_all_when_at_or_under_cap():
    frames = [_f(i) for i in range(5)]
    assert select_keyframes(frames, 10) == tuple(frames)
    assert len(select_keyframes(frames, 5)) == 5


def test_zero_or_negative_cap_returns_all():
    frames = [_f(i) for i in range(7)]
    assert len(select_keyframes(frames, 0)) == 7
    assert len(select_keyframes(frames, -1)) == 7


def test_downsamples_to_cap_with_endpoints_preserved():
    frames = [_f(i) for i in range(100)]
    out = select_keyframes(frames, 5)
    assert len(out) == 5
    assert out[0].ts == 0.0 and out[-1].ts == 99.0  # full arc kept
    ts = [o.ts for o in out]
    assert ts == sorted(ts)  # chronological
    # roughly even spacing
    gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    assert max(gaps) - min(gaps) <= 1.0


def test_cap_of_one_returns_a_single_frame():
    assert len(select_keyframes([_f(i) for i in range(10)], 1)) == 1


def test_sorts_unordered_input():
    out = select_keyframes([_f(3), _f(1), _f(2), _f(0)], 2)
    assert out[0].ts == 0.0 and out[-1].ts == 3.0
