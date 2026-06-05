"""``resolve_event`` — the *resolve* half of always-embed → persist → resolve.

The identity pipelines embed every track they see and the worker persists
those vectors (see :class:`~kukiihome_shared.preprocessor.TrackEmbedding` +
``DetectionStore.add_embeddings``) *whether or not* any actor was enrolled at
the time. This module closes the loop: given a freshly enrolled (or updated)
corpus, it walks the embeddings persisted for an event and matches them —
retroactively naming people the system couldn't name when it first saw them,
with **no re-inference over the original frames**.

That re-inference avoidance is the whole point. Frames live ~10 min in the
preprocessor buffer and aren't kept cold; the embedding is the durable trace.
So "enroll Alice today, find every past event she appeared in" is a cheap
cosine sweep over stored vectors, not a re-run of OSNet/ArcFace over footage
we no longer have.

A resolution is deliberately indistinguishable from a live match: it emits the
same :class:`~kukiihome_shared.preprocessor.ActorMatch`, stamped with the
``match_method`` the producing pipeline recorded (``body_id_osnet`` etc.), so
the downstream fusion/correlation layer weights a resolved body-ID signal
exactly as it would a live one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from kukiihome_shared.preprocessor import ActorMatch

    from kukiihome_preprocessor.detection_store import DetectionStore, EmbeddingRow
    from kukiihome_preprocessor.pipelines.identity.router import EnrolledCorpus


# Per-modality cosine thresholds for a confident retroactive match. These
# mirror the live per-pipeline match thresholds (OSNet body-ID lives in a
# looser space than ArcFace, hence the higher 0.6) and are overridable per
# call — the feedback loop can retune them per camera/condition just like the
# live KnobAdjustment path does. A modality absent here falls back to
# ``_FALLBACK_THRESHOLD`` rather than silently matching everything.
DEFAULT_RESOLVE_THRESHOLDS: dict[str, float] = {
    "body": 0.6,
    "body_shape": 0.5,
    "gait": 0.5,
    "face": 0.5,
    "pet": 0.5,
}
_FALLBACK_THRESHOLD = 0.6


def _best_match(
    embedding: np.ndarray,
    enrolled: dict[str, np.ndarray],
    threshold: float,
) -> tuple[str | None, float]:
    """Highest cosine similarity above ``threshold``. Both sides are assumed
    L2-normalized (pipelines normalize before persisting; enrollment events
    carry normalized templates), so a dot product *is* the cosine. Same shape
    as the per-pipeline ``_match`` helpers, kept here so resolution doesn't
    reach into a specific pipeline's internals."""
    best_id: str | None = None
    best_sim = -1.0
    for actor_id, enrolled_emb in enrolled.items():
        sim = float(np.dot(embedding, enrolled_emb))
        if sim > best_sim:
            best_sim = sim
            best_id = actor_id
    if best_id is not None and best_sim >= threshold:
        return best_id, best_sim
    return None, 0.0


def resolve_event(
    store: DetectionStore,
    event_id: str,
    corpus: EnrolledCorpus,
    *,
    thresholds: Mapping[str, float] | None = None,
    modalities: Sequence[str] | None = None,
) -> tuple[ActorMatch, ...]:
    """Resolve the identities of a persisted event against ``corpus``.

    Reads the embeddings stored for ``event_id``, groups them by modality,
    and cosine-matches each against the corpus slice for that modality. Emits
    one :class:`ActorMatch` per embedding that clears its modality's
    threshold — i.e. one per (track, frame) the corpus could name. Tracks the
    corpus can't name stay silent (no "unknown" matches), exactly as the live
    path behaves.

    ``thresholds`` overrides :data:`DEFAULT_RESOLVE_THRESHOLDS` per modality;
    ``modalities`` restricts which modalities to resolve (default: every
    modality that has *both* stored embeddings and a non-empty corpus slice).
    Cheap and idempotent — safe to re-run whenever the corpus changes.
    """
    from kukiihome_shared.preprocessor import ActorMatch

    thresholds = thresholds or {}

    rows: list[EmbeddingRow] = store.embeddings_for_event(event_id)
    by_modality: dict[str, list[EmbeddingRow]] = {}
    for r in rows:
        if modalities is not None and r.modality not in modalities:
            continue
        by_modality.setdefault(r.modality, []).append(r)

    out: list[ActorMatch] = []
    for modality, mod_rows in by_modality.items():
        slice_ = corpus.slice(modality)
        # Keep only vector templates — a modality whose corpus values are
        # strings (plate text) isn't cosine-resolvable and never produces
        # embeddings anyway.
        enrolled = {
            actor_id: np.asarray(tmpl, dtype=np.float32)
            for actor_id, tmpl in slice_.items()
            if not isinstance(tmpl, str)
        }
        if not enrolled:
            continue
        threshold = thresholds.get(
            modality, DEFAULT_RESOLVE_THRESHOLDS.get(modality, _FALLBACK_THRESHOLD)
        )
        for r in mod_rows:
            actor_id, sim = _best_match(r.embedding, enrolled, threshold)
            if actor_id is None:
                continue
            out.append(
                ActorMatch(
                    actor_id=actor_id,
                    # float32 normalized dot can land a hair above 1.0;
                    # ActorMatch.confidence is strictly le=1.0.
                    confidence=min(1.0, sim),
                    match_method=r.match_method,  # type: ignore[arg-type]
                    frame_ts=r.frame_ts,
                    track_id=r.track_id,
                )
            )
    return tuple(out)
