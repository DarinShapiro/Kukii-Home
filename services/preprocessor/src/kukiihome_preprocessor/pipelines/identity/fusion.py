"""Multi-modal identity fusion — combine per-modality ActorMatches for
one track into a single calibrated confidence (Epic 10.10.3).

The router emits independent :class:`ActorMatch` objects per modality
(face / body / pet / ... / future gait / shape / height). Without
fusion, the downstream correlation does last-write-wins per
``(track_id, frame_ts)`` — so when face AND body both match a track,
one silently clobbers the other and we throw away corroborating signal.

Fusion combines them instead. For each track, group matches by the
actor each modality voted for, then for each candidate actor combine
its per-modality similarities into one confidence via **weighted
noisy-OR**:

    fused = 1 - prod over modalities m of (1 - alpha_m * sim_m)

* ``alpha_m`` is a per-modality reliability weight (config) — how much
  we trust that modality for *identity* (not for *this camera*). Face is
  the durable high-precision anchor (alpha~1.0). The body modalities
  split by DURABILITY: OSNet ``body_id_osnet`` keys on clothing so it's
  a transient same-visit carry (alpha~0.6), while CC-ReID ``ccreid_cal``
  and ``gait_opengait`` are clothes-INVARIANT durable traits (alpha~0.85
  / 0.8) — they can anchor identity even across outfit changes and on
  face-fail cameras. Height is a soft prior (alpha~0.3).
* Noisy-OR is monotonic and corroborating: two independent 0.5 votes
  fuse *above* 0.5 (each adds evidence), while a single weak vote is
  damped by its alpha. It needs no training — the seam for a learned
  combiner (once the Epic 11 feedback loop produces labels) is
  :func:`fuse_track`'s body.

Disagreement is handled by keeping candidate actors separate and
returning the **best-scoring** actor per track — face saying "Alice"
and body saying "Bob" produce two candidates; the higher fused score
wins, and the provenance records both so the VLM / per-alert page can
see the conflict.

Modality-agnostic: a new modality participates the moment it appears
in ``ActorMatch.match_method`` + has a weight (unknown methods fall
back to :data:`DEFAULT_ALPHA`). Nothing here is wired to face/body/pet
specifically.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kukiihome_shared.preprocessor import ActorMatch

# Per-modality reliability weights for identity fusion. Tunable; these
# are sane priors, not learned. Higher = more trusted as an identity
# signal. The body modalities split by DURABILITY (see Epic 10.10 /
# 10.11): OSNet is clothing-dependent + transient (low); CC-ReID and
# gait are clothes-invariant durable traits (high, below face).
DEFAULT_WEIGHTS: dict[str, float] = {
    "face_arcface": 1.0,
    "plate_lpr": 1.0,
    "pet_dinov2": 0.9,
    "ccreid_cal": 0.85,  # durable: clothes-invariant body shape (10.11.5)
    "gait_opengait": 0.8,  # durable: walking dynamics, distance-robust (10.11.6)
    "body_id_osnet": 0.6,  # transient: clothing appearance, same-visit carry
    "height_calib": 0.3,  # soft prior (Epic 10.11.7)
}
DEFAULT_ALPHA = 0.5
"""Weight for a modality not in DEFAULT_WEIGHTS — trusted modestly so
a new pipeline contributes without first being tuned."""


@dataclass(frozen=True)
class FusedMatch:
    """One fused identity decision for a track.

    ``confidence`` is the noisy-OR combination; ``contributions`` is the
    provenance ``{match_method: sim}`` that produced it (for the VLM +
    per-alert page + debugging). ``frame_ts`` is the representative
    frame (the highest-sim contributing match's)."""

    track_id: str
    actor_id: str
    confidence: float
    frame_ts: float
    contributions: dict[str, float] = field(default_factory=dict)


def fuse_track(
    matches: list[ActorMatch],
    *,
    weights: dict[str, float] | None = None,
) -> FusedMatch | None:
    """Fuse all matches for a SINGLE track into the best actor decision.

    Groups by ``actor_id``, combines each actor's per-modality sims via
    weighted noisy-OR, returns the highest-confidence actor (or None if
    ``matches`` is empty). For one actor with one modality this reduces
    to ``alpha * sim`` — a single weak modality is damped, never
    inflated. Repeated matches of the same modality+actor (across
    frames) keep the strongest sim for that modality (independent
    observations of the *same* signal aren't independent evidence).
    """
    if not matches:
        return None
    w = weights or DEFAULT_WEIGHTS

    # actor_id -> {match_method -> best sim seen}, and best frame_ts.
    per_actor: dict[str, dict[str, float]] = {}
    actor_frame_ts: dict[str, tuple[float, float]] = {}  # actor -> (best_sim, ts)
    track_id: str | None = None
    for m in matches:
        track_id = m.track_id
        meth = per_actor.setdefault(m.actor_id, {})
        if m.confidence > meth.get(m.match_method, -1.0):
            meth[m.match_method] = m.confidence
        cur = actor_frame_ts.get(m.actor_id)
        if cur is None or m.confidence > cur[0]:
            actor_frame_ts[m.actor_id] = (m.confidence, m.frame_ts)

    if track_id is None:
        return None

    best: FusedMatch | None = None
    for actor_id, sims in per_actor.items():
        product = 1.0
        for method, sim in sims.items():
            alpha = w.get(method, DEFAULT_ALPHA)
            product *= 1.0 - alpha * sim
        fused = 1.0 - product
        if best is None or fused > best.confidence:
            best = FusedMatch(
                track_id=track_id,
                actor_id=actor_id,
                confidence=round(fused, 4),
                frame_ts=actor_frame_ts[actor_id][1],
                contributions=dict(sims),
            )
    return best


def fuse_matches(
    matches: tuple[ActorMatch, ...],
    *,
    weights: dict[str, float] | None = None,
) -> tuple[FusedMatch, ...]:
    """Fuse a window's matches into one FusedMatch per track.

    Groups by ``track_id`` (untracked matches are dropped — they can't
    be correlated to a detection downstream) and fuses each group.
    Returns one decision per track, sorted by track_id for determinism.
    """
    by_track: dict[str, list[ActorMatch]] = {}
    for m in matches:
        if m.track_id is None:
            continue
        by_track.setdefault(m.track_id, []).append(m)

    out: list[FusedMatch] = []
    for tid in sorted(by_track):
        fm = fuse_track(by_track[tid], weights=weights)
        if fm is not None:
            out.append(fm)
    return tuple(out)
