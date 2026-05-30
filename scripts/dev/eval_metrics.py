#!/usr/bin/env python
"""Separability metrics for identity eval (Epic #103, piece 3).

The statistics that turn "directional" into "defensible". Given a set of
L2-normalized embeddings each labeled with a ground-truth subject_id,
split all pairs into:

  * GENUINE  pairs (same subject) — should have HIGH cosine
  * IMPOSTER pairs (different subjects) — should have LOW cosine

and report the distributions plus threshold-free separability:

  * genuine/imposter mean + std
  * ROC-AUC  (P[genuine cosine > imposter cosine]; 0.5 = chance, 1.0 = perfect)
  * EER      (equal-error rate: FAR == FRR; lower is better) + its threshold
  * d-prime  (mean separation in pooled-std units)

This is what self-recall CANNOT give: recall alone says "it re-finds me",
but says nothing about whether a STRANGER also scores high. Separability
needs >=2 subjects; with one subject the harness reports imposter pairs=0
and refuses to claim separability (honest, not a silent 1.0).

Pure numpy — no model/torch deps, so the math is unit-tested with
synthetic vectors, decoupled from the (slow, hardware-bound) embedding
extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np


@dataclass(frozen=True)
class SeparabilityReport:
    n_embeddings: int
    n_subjects: int
    n_genuine: int
    n_imposter: int
    genuine_mean: float
    genuine_std: float
    imposter_mean: float
    imposter_std: float
    roc_auc: float | None  # None when no imposter pairs (untestable)
    eer: float | None
    eer_threshold: float | None
    d_prime: float | None

    def summary(self) -> str:
        if self.roc_auc is None:
            return (
                f"n={self.n_embeddings} subjects={self.n_subjects} "
                f"genuine={self.n_genuine} (mean {self.genuine_mean:.3f}) | "
                f"imposter=0 -> separability UNTESTABLE (need >=2 subjects)"
            )
        return (
            f"n={self.n_embeddings} subjects={self.n_subjects} | "
            f"genuine {self.genuine_mean:.3f}±{self.genuine_std:.3f} "
            f"imposter {self.imposter_mean:.3f}±{self.imposter_std:.3f} | "
            f"AUC={self.roc_auc:.3f} EER={self.eer:.3f}@{self.eer_threshold:.3f} "
            f"d'={self.d_prime:.2f}"
        )


def _cosine_pairs(embeddings: np.ndarray, labels: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """All unordered pairs' cosine, split into (genuine, imposter).
    Assumes rows are L2-normalized (cosine == dot)."""
    gen: list[float] = []
    imp: list[float] = []
    for i, j in combinations(range(len(labels)), 2):
        c = float(np.dot(embeddings[i], embeddings[j]))
        (gen if labels[i] == labels[j] else imp).append(c)
    return np.asarray(gen), np.asarray(imp)


def _roc_auc(genuine: np.ndarray, imposter: np.ndarray) -> float:
    """P[random genuine > random imposter] via the Mann-Whitney U
    statistic (rank-based, exact, no threshold sweep)."""
    g, m = len(genuine), len(imposter)
    allv = np.concatenate([genuine, imposter])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(len(allv), dtype=np.float64)
    ranks[order] = np.arange(1, len(allv) + 1)
    # average ranks for ties
    _assign_tie_ranks(allv, ranks)
    rank_sum_genuine = ranks[:g].sum()
    u_genuine = rank_sum_genuine - g * (g + 1) / 2.0
    return float(u_genuine / (g * m))


def _assign_tie_ranks(values: np.ndarray, ranks: np.ndarray) -> None:
    order = values.argsort(kind="mergesort")
    sv = values[order]
    i = 0
    n = len(sv)
    while i < n:
        j = i
        while j + 1 < n and sv[j + 1] == sv[i]:
            j += 1
        if j > i:
            avg = (ranks[order[i]] + ranks[order[j]]) / 2.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
        i = j + 1


def _eer(genuine: np.ndarray, imposter: np.ndarray) -> tuple[float, float]:
    """Equal-error rate + threshold. Sweep candidate thresholds (all
    observed cosines); accept pair if cosine >= threshold. FAR =
    imposters accepted; FRR = genuines rejected. EER where |FAR-FRR| min."""
    thresholds = np.unique(np.concatenate([genuine, imposter]))
    best_gap = np.inf
    best_eer = 1.0
    best_thr = 0.0
    for thr in thresholds:
        far = float((imposter >= thr).mean())
        frr = float((genuine < thr).mean())
        gap = abs(far - frr)
        if gap < best_gap:
            best_gap = gap
            best_eer = (far + frr) / 2.0
            best_thr = float(thr)
    return best_eer, best_thr


def separability(embeddings: np.ndarray, labels: list[str]) -> SeparabilityReport:
    """Compute the full separability report from labeled embeddings.

    ``embeddings`` (N, D) should be L2-normalized; ``labels`` length N
    are ground-truth subject ids. Imposter-free input (one subject)
    yields a report with roc_auc/eer/d_prime = None.
    """
    if len(labels) != len(embeddings):
        raise ValueError("labels and embeddings length mismatch")
    n_subjects = len(set(labels))
    gen, imp = _cosine_pairs(embeddings, labels)

    g_mean = float(gen.mean()) if len(gen) else 0.0
    g_std = float(gen.std()) if len(gen) else 0.0
    i_mean = float(imp.mean()) if len(imp) else 0.0
    i_std = float(imp.std()) if len(imp) else 0.0

    if len(gen) == 0 or len(imp) == 0:
        return SeparabilityReport(
            n_embeddings=len(embeddings),
            n_subjects=n_subjects,
            n_genuine=len(gen),
            n_imposter=len(imp),
            genuine_mean=g_mean,
            genuine_std=g_std,
            imposter_mean=i_mean,
            imposter_std=i_std,
            roc_auc=None,
            eer=None,
            eer_threshold=None,
            d_prime=None,
        )

    auc = _roc_auc(gen, imp)
    eer, thr = _eer(gen, imp)
    pooled = np.sqrt((g_std**2 + i_std**2) / 2.0)
    d_prime = float((g_mean - i_mean) / pooled) if pooled > 1e-9 else float("inf")
    return SeparabilityReport(
        n_embeddings=len(embeddings),
        n_subjects=n_subjects,
        n_genuine=len(gen),
        n_imposter=len(imp),
        genuine_mean=g_mean,
        genuine_std=g_std,
        imposter_mean=i_mean,
        imposter_std=i_std,
        roc_auc=round(auc, 4),
        eer=round(eer, 4),
        eer_threshold=round(thr, 4),
        d_prime=round(d_prime, 3),
    )
