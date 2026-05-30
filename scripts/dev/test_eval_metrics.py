"""Tests for separability metrics (Epic #103, piece 3).

Synthetic embeddings with known ground truth — verifies the statistics
independent of any model. Run: uv run pytest scripts/dev/test_eval_metrics.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from eval_metrics import separability  # noqa: E402


def _norm(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_single_subject_separability_untestable():
    embs = _norm([[1, 0], [0.99, 0.01], [0.98, 0.02]])
    r = separability(embs, ["a", "a", "a"])
    assert r.n_imposter == 0
    assert r.roc_auc is None and r.eer is None and r.d_prime is None
    assert "UNTESTABLE" in r.summary()


def test_perfect_separation():
    # Two orthogonal-ish tight clusters: genuine ~1.0, imposter ~0.0.
    a = _norm([[1, 0], [1, 0.01], [1, 0.02]])
    b = _norm([[0, 1], [0.01, 1], [0.02, 1]])
    embs = np.vstack([a, b])
    labels = ["a", "a", "a", "b", "b", "b"]
    r = separability(embs, labels)
    assert r.n_subjects == 2
    assert r.genuine_mean > 0.99
    assert r.imposter_mean < 0.05
    assert r.roc_auc == 1.0  # every genuine > every imposter
    assert r.eer < 0.01
    assert r.d_prime > 5


def test_no_separation_auc_near_half():
    # All embeddings drawn from the same distribution but labeled into
    # two groups -> genuine and imposter cosines indistinguishable.
    rng = np.random.default_rng(0)
    v = _norm(rng.normal(size=(40, 8)))
    labels = ["a" if i % 2 == 0 else "b" for i in range(40)]
    r = separability(v, labels)
    assert 0.4 < r.roc_auc < 0.6  # chance-level
    assert 0.4 < r.eer < 0.6


def test_auc_matches_hand_computed():
    # genuine cosines {0.9,0.8}, imposter {0.5,0.1}. All genuine >
    # all imposter -> AUC = 1.0. Construct vectors giving those.
    # Use 1-D-ish: cos via angle. Simpler: build pairs directly is hard,
    # so verify the ranking property with a tiny set.
    a = _norm([[1, 0], [0.99, 0.14]])  # genuine pair cos ~0.99
    b = _norm([[0, 1]])  # imposter vs both a ~0.0-0.14
    embs = np.vstack([a, b])
    r = separability(embs, ["a", "a", "b"])
    assert r.n_genuine == 1  # one a-a pair
    assert r.n_imposter == 2  # two a-b pairs
    assert r.roc_auc == 1.0


def test_length_mismatch_raises():
    import pytest

    with pytest.raises(ValueError, match="mismatch"):
        separability(_norm([[1, 0]]), ["a", "b"])
