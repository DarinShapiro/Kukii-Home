"""Tests for the notify dispatchers (push, TTS, ask flow)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sentihome_notify.dispatcher import (
    AskFlow,
    AskOutcome,
    NotifyWorker,
    PushDispatcher,
    TTSDispatcher,
)
from sentihome_shared.generated.events.action_event import ActionEvent, ActionType, Tier


def _push(targets: list[str], *, message: str = "msg") -> ActionEvent:
    return ActionEvent(
        action_id="act_1",
        event_id="e1",
        action_type=ActionType.notify_push,
        tier=Tier.tier_2_push,
        targets=targets,
        message=message,
        rules_fired=["r1"],
    )


# ─────────────────────────────────────────────────────────────────────
# PushDispatcher
# ─────────────────────────────────────────────────────────────────────


async def test_push_dispatcher_calls_ha_service_per_resident():
    calls: list[tuple[str, dict]] = []

    async def ha(service: str, data: dict) -> dict:
        calls.append((service, data))
        return {"ok": True}

    disp = PushDispatcher(
        ha_caller=ha,
        resident_to_service={"r1": "notify.mobile_app_r1", "r2": "notify.mobile_app_r2"},
    )
    resp = await disp.dispatch(_push(["r1", "r2"]))
    assert len(resp) == 2
    assert calls[0][0] == "notify.mobile_app_r1"
    assert calls[0][1]["data"]["push"]["sound"] == "default"


async def test_push_dispatcher_silent_flag_overrides_sound():
    async def ha(service, data):
        return data

    disp = PushDispatcher(ha_caller=ha, resident_to_service={"r1": "notify.r1"})
    [resp] = await disp.dispatch(_push(["r1"]), silent=True)
    assert resp["response"]["data"]["push"]["sound"] == "none"


async def test_push_dispatcher_skips_unknown_resident():
    async def ha(service, data):
        return data

    disp = PushDispatcher(ha_caller=ha, resident_to_service={})
    resp = await disp.dispatch(_push(["unknown"]))
    assert resp == []


# ─────────────────────────────────────────────────────────────────────
# TTSDispatcher
# ─────────────────────────────────────────────────────────────────────


async def test_tts_dispatcher_invokes_tts_service():
    captured: dict = {}

    async def ha(service, data):
        captured["service"] = service
        captured["data"] = data
        return {"ok": True}

    disp = TTSDispatcher(
        ha_caller=ha,
        media_player_entities=["media_player.bedroom", "media_player.kitchen"],
    )
    action = ActionEvent(
        action_id="act_speak",
        event_id="e1",
        action_type=ActionType.notify_speak,
        tier=Tier.tier_3_wake,
        message="Intruder alert.",
    )
    await disp.dispatch(action)
    assert captured["service"] == "tts.cloud_say"
    assert captured["data"]["message"] == "Intruder alert."
    assert "media_player.bedroom" in captured["data"]["entity_id"]


# ─────────────────────────────────────────────────────────────────────
# AskFlow
# ─────────────────────────────────────────────────────────────────────


async def test_ask_flow_resolves_on_respond():
    flow = AskFlow()
    cb = flow.ask("Was that Sarah?", event_id="e1")
    asyncio.get_running_loop().call_soon(
        lambda: flow.respond(cb.ask_id, outcome=AskOutcome.yes, resident_id="r1")
    )
    outcome = await flow.wait(cb.ask_id)
    assert outcome == AskOutcome.yes
    rec = flow.get(cb.ask_id)
    assert rec is not None and rec.resolved_by == "r1"


async def test_ask_flow_times_out_to_default():
    flow = AskFlow(default_timeout=timedelta(milliseconds=50), timeout_default=AskOutcome.no)
    cb = flow.ask("Confirm?", event_id="e1")
    outcome = await asyncio.wait_for(flow.wait(cb.ask_id), timeout=1.0)
    assert outcome == AskOutcome.no


async def test_ask_flow_callback_fires_on_resolve():
    calls: list[str] = []
    flow = AskFlow()
    cb = flow.ask("?", event_id="e1", on_resolve=lambda c: calls.append(c.ask_id))
    flow.respond(cb.ask_id, outcome=AskOutcome.yes, resident_id="r1")
    assert calls == [cb.ask_id]


# ─────────────────────────────────────────────────────────────────────
# NotifyWorker
# ─────────────────────────────────────────────────────────────────────


async def test_notify_worker_routes_by_action_type():
    pushed: list[ActionEvent] = []
    spoken: list[ActionEvent] = []

    class FakePush:
        async def dispatch(self, action, silent=False):
            pushed.append(action)
            return []

    class FakeTTS:
        async def dispatch(self, action):
            spoken.append(action)
            return {}

    flow = AskFlow()
    worker = NotifyWorker(push=FakePush(), tts=FakeTTS(), ask_flow=flow)

    await worker.handle(_push(["r1"]))
    await worker.handle(
        ActionEvent(
            action_id="speak1",
            event_id="e1",
            action_type=ActionType.notify_speak,
            message="hi",
        )
    )
    await worker.handle(
        ActionEvent(
            action_id="ask1",
            event_id="e1",
            action_type=ActionType.ask,
            message="Confirm?",
        )
    )
    assert len(pushed) == 1
    assert len(spoken) == 1
    assert flow.get("ask1") is not None


def test_push_dispatcher_rejects_wrong_action_type():
    async def ha(service, data):
        return data

    disp = PushDispatcher(ha_caller=ha, resident_to_service={})
    action = ActionEvent(action_id="x", event_id="e", action_type=ActionType.notify_speak)
    with pytest.raises(ValueError):
        asyncio.run(disp.dispatch(action))
