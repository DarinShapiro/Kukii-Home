"""Action runtime — perception lane (coalesce + revert) + protective lane."""

from __future__ import annotations

import asyncio

import pytest
from kukiihome_ha_agent.action_runtime import (
    PerceptionRequest,
    PerceptionRuntime,
    ProtectiveRecommendation,
    ProtectiveRuntime,
    _inverse,
    parse_perception_requests,
    parse_recommendations,
)
from kukiihome_ha_agent.action_store import (
    ActionStore,
    PerceptionEntry,
    ProtectiveEntry,
)


@pytest.fixture
def store():
    s = ActionStore(path=None)
    yield s
    s.close()


# ─── fake HA caller ────────────────────────────────────────────────


class FakeCaller:
    def __init__(self, *, fail_on=None):
        self.calls: list[dict] = []
        self.fail_on = fail_on  # ("domain", "service") that should raise

    async def __call__(self, domain, service, *, entity_id=None, data=None):
        if self.fail_on and (domain, service) == self.fail_on:
            raise RuntimeError("simulated HA failure")
        self.calls.append(
            {
                "domain": domain,
                "service": service,
                "entity_id": entity_id,
                "data": data or {},
            }
        )
        return {}


# ─── inverse map ───────────────────────────────────────────────────


def test_inverse_known_services():
    assert _inverse("light.turn_on") == "light.turn_off"
    assert _inverse("switch.turn_off") == "switch.turn_on"


def test_inverse_unknown_returns_none():
    assert _inverse("media_player.play_media") is None


# ─── perception lane ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_perception_rejects_without_camera_id(store):
    rt = PerceptionRuntime(store, FakeCaller())
    req = PerceptionRequest(
        kind="ha_service",
        service="light.turn_on",
        target="light.porch",
        revert_after_s=0.01,
    )
    assert await rt.execute(req, incident_id="i") == "no_camera_scope"


@pytest.mark.asyncio
async def test_perception_rejects_when_not_whitelisted(store):
    rt = PerceptionRuntime(store, FakeCaller())
    req = PerceptionRequest(
        kind="ha_service",
        camera_id="front",
        service="light.turn_on",
        target="light.porch",
        revert_after_s=0.01,
    )
    assert await rt.execute(req, incident_id="i") == "no_authorization"


@pytest.mark.asyncio
async def test_perception_executes_and_schedules_revert(store):
    caller = FakeCaller()
    store.upsert_perception(
        PerceptionEntry(
            camera_id="front",
            target_kind="ha_service",
            target="light.turn_on:light.porch",
        )
    )
    rt = PerceptionRuntime(store, caller)
    req = PerceptionRequest(
        kind="ha_service",
        camera_id="front",
        service="light.turn_on",
        target="light.porch",
        revert_after_s=0.05,
    )
    assert await rt.execute(req, incident_id="i1") == "ok"
    assert caller.calls[0]["service"] == "turn_on"
    # Revert fires
    await asyncio.sleep(0.15)
    assert any(c["service"] == "turn_off" for c in caller.calls)


@pytest.mark.asyncio
async def test_perception_coalesces_overlapping_requests(store):
    """A second request for the same target while the revert is pending
    should cancel the existing revert and extend the window."""
    caller = FakeCaller()
    store.upsert_perception(
        PerceptionEntry(
            camera_id="front",
            target_kind="ha_service",
            target="light.turn_on:light.porch",
        )
    )
    rt = PerceptionRuntime(store, caller)
    req1 = PerceptionRequest(
        kind="ha_service",
        camera_id="front",
        service="light.turn_on",
        target="light.porch",
        revert_after_s=0.1,
    )
    req2 = PerceptionRequest(
        kind="ha_service",
        camera_id="front",
        service="light.turn_on",
        target="light.porch",
        revert_after_s=0.1,
    )
    await rt.execute(req1, incident_id="i1")
    # Small sleep < revert_after_s so the first is still pending
    await asyncio.sleep(0.02)
    await rt.execute(req2, incident_id="i2")
    # After both applies, ONE pending revert remains (not two)
    assert rt.pending_count() == 1
    await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_perception_skips_revert_when_inverse_unknown(store):
    """media_player has no listed inverse — revert should be skipped, not
    crash. The whitelist still authorizes the apply call."""
    caller = FakeCaller()
    store.upsert_perception(
        PerceptionEntry(
            camera_id="front",
            target_kind="ha_service",
            target="media_player.play_media:media_player.living_room",
        )
    )
    rt = PerceptionRuntime(store, caller)
    req = PerceptionRequest(
        kind="ha_service",
        camera_id="front",
        service="media_player.play_media",
        target="media_player.living_room",
        revert_after_s=0.02,
    )
    assert await rt.execute(req, incident_id="i") == "ok"
    await asyncio.sleep(0.1)
    # Only the apply call happened; no revert call attempted
    assert len(caller.calls) == 1


@pytest.mark.asyncio
async def test_perception_apply_failure_returns_failed(store):
    caller = FakeCaller(fail_on=("light", "turn_on"))
    store.upsert_perception(
        PerceptionEntry(
            camera_id="front",
            target_kind="ha_service",
            target="light.turn_on:light.porch",
        )
    )
    rt = PerceptionRuntime(store, caller)
    req = PerceptionRequest(
        kind="ha_service",
        camera_id="front",
        service="light.turn_on",
        target="light.porch",
        revert_after_s=0.01,
    )
    assert await rt.execute(req, incident_id="i") == "failed"
    # No revert scheduled
    assert rt.pending_count() == 0


# ─── protective lane ───────────────────────────────────────────────


def _whitelist_lock(store, **kw):
    base: dict = dict(  # noqa: C408
        camera_id="backyard",
        action_class="lock",
        service="lock.lock",
        target="lock.back_door",
        min_severity="critical",
        min_confidence=0.7,
    )
    base.update(kw)
    store.upsert_protective(ProtectiveEntry(**base))


def _lock_rec(**kw):
    base: dict = dict(  # noqa: C408
        action_class="lock",
        service="lock.lock",
        target="lock.back_door",
        confidence=0.9,
        urgency="critical",
        camera_id="backyard",
    )
    base.update(kw)
    return ProtectiveRecommendation(**base)


@pytest.mark.asyncio
async def test_protective_executes_when_whitelisted_and_gated(store):
    caller = FakeCaller()
    _whitelist_lock(store)
    rt = ProtectiveRuntime(store, caller)
    row = await rt.execute(_lock_rec(), incident_id="inc1")
    assert row.status == "ok"
    assert caller.calls and caller.calls[0]["service"] == "lock"
    # Audit log row matches
    log = store.log_for_incident("inc1")
    assert log[0].status == "ok"


@pytest.mark.asyncio
async def test_protective_rejected_when_not_whitelisted(store):
    caller = FakeCaller()
    rt = ProtectiveRuntime(store, caller)
    row = await rt.execute(_lock_rec(), incident_id="inc2")
    assert row.status == "whitelisted_rejected"
    assert row.gate_reason == "no_authorization"
    # No HA call attempted
    assert caller.calls == []
    # Still audited
    log = store.log_for_incident("inc2")
    assert log[0].status == "whitelisted_rejected"


@pytest.mark.asyncio
async def test_protective_gated_by_severity(store):
    caller = FakeCaller()
    _whitelist_lock(store, min_severity="critical")
    rt = ProtectiveRuntime(store, caller)
    row = await rt.execute(_lock_rec(urgency="normal"), incident_id="inc3")
    assert row.status == "gated"
    assert row.gate_reason == "severity_below_threshold"
    assert caller.calls == []


@pytest.mark.asyncio
async def test_protective_gated_by_confidence(store):
    caller = FakeCaller()
    _whitelist_lock(store, min_confidence=0.95)
    rt = ProtectiveRuntime(store, caller)
    row = await rt.execute(_lock_rec(confidence=0.6), incident_id="inc4")
    assert row.status == "gated"
    assert row.gate_reason == "confidence_below_threshold"


@pytest.mark.asyncio
async def test_protective_redundancy_blocks_until_threshold(store):
    caller = FakeCaller()
    _whitelist_lock(store, redundancy_required=2)
    rt = ProtectiveRuntime(store, caller)
    # First recommendation: redundancy pending
    row1 = await rt.execute(_lock_rec(), incident_id="inc1")
    assert row1.status == "gated"
    assert row1.gate_reason.startswith("redundancy_pending")
    # Second recommendation from a DIFFERENT incident: fires
    row2 = await rt.execute(_lock_rec(), incident_id="inc2")
    assert row2.status == "ok"
    assert caller.calls and caller.calls[0]["service"] == "lock"


@pytest.mark.asyncio
async def test_protective_redundancy_same_incident_counted_once(store):
    """If the same incident sends two recommendations, redundancy counter
    should not double-count it — we want N *distinct* incidents."""
    caller = FakeCaller()
    _whitelist_lock(store, redundancy_required=2)
    rt = ProtectiveRuntime(store, caller)
    await rt.execute(_lock_rec(), incident_id="inc1")
    row = await rt.execute(_lock_rec(), incident_id="inc1")
    assert row.status == "gated"  # still pending, only 1 unique incident


@pytest.mark.asyncio
async def test_protective_logs_failure_when_call_raises(store):
    caller = FakeCaller(fail_on=("lock", "lock"))
    _whitelist_lock(store)
    rt = ProtectiveRuntime(store, caller)
    row = await rt.execute(_lock_rec(), incident_id="inc5")
    assert row.status == "failed"
    assert row.gate_reason and "execution_error" in row.gate_reason


# ─── payload parsing ───────────────────────────────────────────────


def test_parse_perception_requests_handles_empty_and_malformed():
    assert parse_perception_requests(None) == []
    assert parse_perception_requests([]) == []
    # Malformed entry skipped; well-formed kept
    out = parse_perception_requests(
        [
            "not a dict",
            {
                "kind": "ha_service",
                "service": "light.turn_on",
                "target": "light.porch",
                "revert_after_s": 30,
            },
        ]
    )
    assert len(out) == 1
    assert out[0].service == "light.turn_on"


def test_parse_perception_requests_defaults_revert_to_45():
    out = parse_perception_requests(
        [
            {"kind": "ha_service", "service": "light.turn_on", "target": "light.x"},
        ]
    )
    assert out[0].revert_after_s == 45.0


def test_parse_recommendations_skips_unclassified():
    out = parse_recommendations(
        [
            {"service": "lock.lock", "target": "lock.x"},  # missing action_class
            {
                "action_class": "lock",
                "service": "lock.lock",
                "target": "lock.y",
                "confidence": 0.9,
                "urgency": "critical",
            },
        ]
    )
    assert len(out) == 1
    assert out[0].action_class == "lock"
    assert out[0].confidence == pytest.approx(0.9)


def test_parse_recommendations_none_confidence_when_absent():
    out = parse_recommendations(
        [
            {"action_class": "lock", "service": "lock.lock", "target": "lock.x"},
        ]
    )
    assert out[0].confidence is None
