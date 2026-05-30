"""Tests for multi-modal identity fusion (Epic 10.10.3)."""

from __future__ import annotations

from sentihome_preprocessor.pipelines.identity.fusion import (
    DEFAULT_ALPHA,
    DEFAULT_WEIGHTS,
    fuse_matches,
    fuse_track,
)
from sentihome_shared.preprocessor import ActorMatch


def _m(actor, method, conf, track="t1", ts=1.0) -> ActorMatch:
    return ActorMatch(
        actor_id=actor, confidence=conf, match_method=method, frame_ts=ts, track_id=track
    )


def test_empty_returns_none():
    assert fuse_track([]) is None
    assert fuse_matches(()) == ()


def test_single_modality_is_alpha_times_sim():
    # body alpha 0.6, sim 0.9 -> 0.54
    fm = fuse_track([_m("darin", "body_id_osnet", 0.9)])
    assert fm is not None
    assert fm.actor_id == "darin"
    assert abs(fm.confidence - 0.54) < 1e-3


def test_face_full_weight_passthrough():
    # face alpha 1.0, sim 0.8 -> 0.8
    fm = fuse_track([_m("darin", "face_arcface", 0.8)])
    assert abs(fm.confidence - 0.8) < 1e-3


def test_two_modalities_corroborate_above_either():
    # face 0.5 (alpha1.0) + body 0.5 (alpha0.6):
    # 1 - (1-0.5)(1-0.3) = 1 - 0.5*0.7 = 0.65
    fm = fuse_track([_m("darin", "face_arcface", 0.5), _m("darin", "body_id_osnet", 0.5)])
    assert abs(fm.confidence - 0.65) < 1e-3
    assert fm.confidence > 0.5  # corroboration boosts above either alone
    assert set(fm.contributions) == {"face_arcface", "body_id_osnet"}


def test_disagreement_keeps_separate_actors_picks_best():
    # face says alice @0.9 (->0.9); body says bob @0.9 (->0.54). Alice wins.
    fm = fuse_track([_m("alice", "face_arcface", 0.9), _m("bob", "body_id_osnet", 0.9)])
    assert fm.actor_id == "alice"
    assert abs(fm.confidence - 0.9) < 1e-3
    # bob's body-only vote (0.54) lost to alice's face vote (0.9)


def test_weights_are_respected():
    # Custom weight makes body fully trusted -> body 0.9 = 0.9.
    fm = fuse_track([_m("darin", "body_id_osnet", 0.9)], weights={"body_id_osnet": 1.0})
    assert abs(fm.confidence - 0.9) < 1e-3


def test_unknown_modality_uses_default_alpha():
    fm = fuse_track([_m("darin", "gait_opengait", 1.0)], weights={})  # empty -> default
    assert abs(fm.confidence - DEFAULT_ALPHA) < 1e-3


def test_repeated_same_modality_keeps_strongest_not_double_counts():
    # Same body modality across frames: should NOT noisy-OR with itself
    # (not independent evidence) — keeps the strongest sim only.
    fm = fuse_track(
        [
            _m("darin", "body_id_osnet", 0.6, ts=1.0),
            _m("darin", "body_id_osnet", 0.9, ts=2.0),
            _m("darin", "body_id_osnet", 0.7, ts=3.0),
        ]
    )
    # strongest 0.9 * alpha 0.6 = 0.54, NOT a triple noisy-OR
    assert abs(fm.confidence - 0.54) < 1e-3
    assert fm.frame_ts == 2.0  # representative = strongest contribution


def test_fuse_matches_one_per_track():
    matches = (
        _m("darin", "face_arcface", 0.8, track="t1"),
        _m("darin", "body_id_osnet", 0.7, track="t1"),
        _m("alice", "face_arcface", 0.9, track="t2"),
    )
    out = fuse_matches(matches)
    assert [fm.track_id for fm in out] == ["t1", "t2"]
    t1 = next(fm for fm in out if fm.track_id == "t1")
    # face0.8 + body(0.6*0.7=0.42): 1-(0.2)(0.58)=0.884
    assert abs(t1.confidence - 0.884) < 1e-3


def test_fuse_matches_drops_untracked():
    matches = (
        ActorMatch(
            actor_id="x", confidence=0.9, match_method="face_arcface", frame_ts=1.0, track_id=None
        ),
    )
    assert fuse_matches(matches) == ()


def test_default_weights_ordering_sane():
    # Face/plate should be the most-trusted; height the least.
    assert DEFAULT_WEIGHTS["face_arcface"] >= DEFAULT_WEIGHTS["body_id_osnet"]
    assert DEFAULT_WEIGHTS["body_id_osnet"] > DEFAULT_WEIGHTS["height_calib"]
