"""Durable-modality enrollment + fusion (Epic 10.11.5 / 10.11.6 / Stage 3).

CC-ReID (``body_shape``) and gait (``gait``) are clothes-invariant
durable identity traits. These tests cover the plumbing that lets them
flow end-to-end:

* ActorEnrollmentEvent carries body_shape_embedding + gait_embedding
* the ActorCache merges them without clobbering other modalities
* EnrolledCorpus.from_cache projects them into their own slices
* fusion weights the durable signals above transient OSNet body-ID
"""

from __future__ import annotations

import numpy as np
import pytest
from kukiihome_preprocessor.pipelines.identity.fusion import DEFAULT_WEIGHTS, fuse_track
from kukiihome_preprocessor.pipelines.identity.router import EnrolledCorpus
from kukiihome_preprocessor.state import ActorCache
from kukiihome_shared.preprocessor import ActorEnrollmentEvent, ActorMatch


def _enroll(actor_id: str, **kw) -> ActorEnrollmentEvent:
    return ActorEnrollmentEvent(actor_id=actor_id, action="enrolled", **kw)


# ─── contract ───────────────────────────────────────────────────────


def test_enrollment_event_carries_durable_embeddings():
    ev = _enroll(
        "alice",
        body_shape_embedding=(0.1, 0.2, 0.3),
        gait_embedding=(0.4, 0.5),
    )
    assert ev.body_shape_embedding == (0.1, 0.2, 0.3)
    assert ev.gait_embedding == (0.4, 0.5)
    # Backward-compatible: omitting them is fine.
    assert _enroll("bob").body_shape_embedding is None
    assert _enroll("bob").gait_embedding is None


# ─── cache merge ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_merges_durable_modalities_independently():
    cache = ActorCache()
    # Face first, then a separate CC-ReID enrollment, then gait.
    await cache.upsert(_enroll("alice", name="Alice", face_embedding=(1.0, 0.0)))
    await cache.upsert(_enroll("alice", body_shape_embedding=(0.0, 1.0, 0.0)))
    await cache.upsert(_enroll("alice", gait_embedding=(0.0, 0.0, 1.0)))

    (actor,) = await cache.snapshot()
    # Each modality preserved; none clobbered the others.
    assert actor.face_embedding == (1.0, 0.0)
    assert actor.body_shape_embedding == (0.0, 1.0, 0.0)
    assert actor.gait_embedding == (0.0, 0.0, 1.0)
    assert actor.name == "Alice"


# ─── corpus projection ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_corpus_projects_body_shape_and_gait_slices():
    cache = ActorCache()
    await cache.upsert(
        _enroll(
            "alice",
            body_shape_embedding=(0.1, 0.2),
            gait_embedding=(0.3, 0.4, 0.5),
        )
    )
    corpus = await EnrolledCorpus.from_cache(cache)

    body_shape = corpus.slice("body_shape")
    gait = corpus.slice("gait")
    assert set(body_shape) == {"alice"}
    assert set(gait) == {"alice"}
    np.testing.assert_allclose(body_shape["alice"], np.array([0.1, 0.2], dtype=np.float32))
    np.testing.assert_allclose(gait["alice"], np.array([0.3, 0.4, 0.5], dtype=np.float32))
    # body_shape is distinct from the (empty) transient body slice.
    assert corpus.slice("body") == {}


# ─── fusion durability ──────────────────────────────────────────────


def test_durable_modalities_weighted_above_transient_body():
    assert DEFAULT_WEIGHTS["ccreid_cal"] > DEFAULT_WEIGHTS["body_id_osnet"]
    assert DEFAULT_WEIGHTS["gait_opengait"] > DEFAULT_WEIGHTS["body_id_osnet"]
    assert DEFAULT_WEIGHTS["ccreid_cal"] <= DEFAULT_WEIGHTS["face_arcface"]


def test_ccreid_fuses_higher_than_osnet_at_equal_sim():
    """A clothes-invariant CC-ReID vote should outweigh an OSNet vote of
    the same cosine — durability is encoded in the alpha."""
    sim = 0.7
    ccreid = fuse_track(
        [
            ActorMatch(
                actor_id="a", confidence=sim, match_method="ccreid_cal", frame_ts=1.0, track_id="t1"
            )
        ]
    )
    osnet = fuse_track(
        [
            ActorMatch(
                actor_id="a",
                confidence=sim,
                match_method="body_id_osnet",
                frame_ts=1.0,
                track_id="t1",
            )
        ]
    )
    assert ccreid is not None and osnet is not None
    assert ccreid.confidence > osnet.confidence
