"""Notification dispatchers — push, TTS, conversational ask.

Architecture: docs/architecture/15-alerting-and-actions.md
Epic 8: #124 (push), #125 (TTS), #126 (ask flow).

The push + TTS dispatchers are thin adapters that translate an
:class:`ActionEvent` into an HA service call payload — actual HA invocation
is delegated to the (Epic 9) ha-agent via a service caller callable so this
module stays unit-testable and independent of the HA MCP wiring.

The :class:`AskFlow` implements pipeline suspend/resume for conversational
confirmations.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import structlog
from sentihome_shared.generated.events.action_event import ActionEvent, ActionType

logger = structlog.get_logger(__name__)


# Type for the HA-service invocation hook injected by tests / the ha-agent.
HACaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


# ─────────────────────────────────────────────────────────────────────
# Push notification dispatcher (#124)
# ─────────────────────────────────────────────────────────────────────


class PushDispatcher:
    """Sends push notifications via the HA Companion app's ``notify.*`` service.

    Per resident, HA exposes a service like ``notify.mobile_app_<device_id>``.
    The resident→service-name mapping is injected at construction.
    """

    def __init__(
        self,
        *,
        ha_caller: HACaller,
        resident_to_service: dict[str, str],
    ) -> None:
        self._ha = ha_caller
        self._service_for = resident_to_service

    async def dispatch(self, action: ActionEvent, *, silent: bool = False) -> list[dict[str, Any]]:
        if action.action_type != ActionType.notify_push:
            raise ValueError(f"PushDispatcher rejects {action.action_type}")
        responses: list[dict[str, Any]] = []
        for resident_id in action.targets or []:
            service = self._service_for.get(resident_id)
            if service is None:
                logger.warning("notify.push_missing_service", resident=resident_id)
                continue
            data: dict[str, Any] = {
                "title": _push_title(action),
                "message": action.message or "",
                "data": {
                    "action_id": action.action_id,
                    "tier": action.tier.value if action.tier else None,
                    "rules_fired": action.rules_fired or [],
                    "evidence_ref": action.evidence_ref,
                    "push": {"sound": "none" if silent else "default"},
                },
            }
            try:
                resp = await self._ha(service, data)
            except Exception as e:
                logger.error("notify.push_failed", service=service, error=str(e))
                continue
            responses.append({"resident": resident_id, "service": service, "response": resp})
        return responses


def _push_title(action: ActionEvent) -> str:
    base = "SentiHome alert"
    if action.tier is None:
        return base
    tier_map = {
        "tier_0_silent": "SentiHome (log)",
        "tier_1_in_app": "SentiHome",
        "tier_2_push": "SentiHome alert",
        "tier_3_wake": "SentiHome URGENT",
        "tier_4_emergency": "SentiHome EMERGENCY",
    }
    return tier_map.get(action.tier.value, base)


# ─────────────────────────────────────────────────────────────────────
# TTS dispatcher (#125)
# ─────────────────────────────────────────────────────────────────────


class TTSDispatcher:
    """Speaks via HA ``tts.*`` + ``media_player.*`` services."""

    def __init__(
        self,
        *,
        ha_caller: HACaller,
        media_player_entities: list[str],
        tts_service: str = "tts.cloud_say",
    ) -> None:
        self._ha = ha_caller
        self._players = media_player_entities
        self._tts_service = tts_service

    async def dispatch(self, action: ActionEvent) -> dict[str, Any]:
        if action.action_type != ActionType.notify_speak:
            raise ValueError(f"TTSDispatcher rejects {action.action_type}")
        message = action.message or "Alert from SentiHome."
        data = {
            "entity_id": self._players,
            "message": message,
            "cache": False,
        }
        return await self._ha(self._tts_service, data)


# ─────────────────────────────────────────────────────────────────────
# Conversational ask flow (#126)
# ─────────────────────────────────────────────────────────────────────


class AskOutcome(StrEnum):
    yes = "yes"
    no = "no"
    not_sure = "not_sure"
    timeout = "timeout"


@dataclass
class AskCallback:
    """A pending ask, awaiting a resident's response.

    The pipeline is "suspended" in the sense that downstream device-action
    execution waits on ``future``; the worker that owns the suspended call
    awaits ``future`` and then resumes.
    """

    ask_id: str
    question: str
    event_id: str
    created_at: datetime
    timeout_at: datetime
    future: asyncio.Future[AskOutcome] = field(default_factory=asyncio.Future)
    resolved_by: str | None = None
    outcome: AskOutcome | None = None
    on_resolve: Callable[[AskCallback], None] | None = None

    @property
    def pending(self) -> bool:
        return self.outcome is None


class AskFlow:
    """Pose questions to residents, await response, default to timeout outcome.

    Usage::

        flow = AskFlow(default_timeout=timedelta(seconds=60))
        cb = flow.ask("Was that Sarah at the front door?", event_id="e1")
        # ... push the question via PushDispatcher ...
        outcome = await flow.wait(cb.ask_id)   # resumes on respond() or timeout
    """

    def __init__(
        self,
        *,
        default_timeout: timedelta = timedelta(seconds=60),
        timeout_default: AskOutcome = AskOutcome.timeout,
    ) -> None:
        self._timeout = default_timeout
        self._default_outcome = timeout_default
        self._pending: dict[str, AskCallback] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}

    def ask(
        self,
        question: str,
        *,
        event_id: str,
        ask_id: str | None = None,
        timeout: timedelta | None = None,
        on_resolve: Callable[[AskCallback], None] | None = None,
        now: datetime | None = None,
    ) -> AskCallback:
        ask_id = ask_id or f"ask_{uuid.uuid4().hex[:12]}"
        now = now or datetime.now(UTC)
        timeout = timeout or self._timeout
        cb = AskCallback(
            ask_id=ask_id,
            question=question,
            event_id=event_id,
            created_at=now,
            timeout_at=now + timeout,
            on_resolve=on_resolve,
        )
        self._pending[ask_id] = cb
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self._timers[ask_id] = loop.call_later(timeout.total_seconds(), self._expire, ask_id)
        return cb

    def respond(self, ask_id: str, *, outcome: AskOutcome, resident_id: str) -> AskCallback | None:
        cb = self._pending.get(ask_id)
        if cb is None or not cb.pending:
            return None
        cb.outcome = outcome
        cb.resolved_by = resident_id
        if not cb.future.done():
            cb.future.set_result(outcome)
        self._cancel_timer(ask_id)
        if cb.on_resolve is not None:
            try:
                cb.on_resolve(cb)
            except Exception:
                logger.exception("ask.on_resolve_failed", ask_id=ask_id)
        return cb

    async def wait(self, ask_id: str) -> AskOutcome:
        cb = self._pending.get(ask_id)
        if cb is None:
            raise KeyError(ask_id)
        return await cb.future

    def _expire(self, ask_id: str) -> None:
        cb = self._pending.get(ask_id)
        if cb is None or not cb.pending:
            return
        cb.outcome = self._default_outcome
        if not cb.future.done():
            cb.future.set_result(self._default_outcome)
        if cb.on_resolve is not None:
            try:
                cb.on_resolve(cb)
            except Exception:
                logger.exception("ask.on_resolve_failed", ask_id=ask_id)

    def _cancel_timer(self, ask_id: str) -> None:
        timer = self._timers.pop(ask_id, None)
        if timer is not None:
            timer.cancel()

    def pending(self) -> list[AskCallback]:
        return [cb for cb in self._pending.values() if cb.pending]

    def get(self, ask_id: str) -> AskCallback | None:
        return self._pending.get(ask_id)


# ─────────────────────────────────────────────────────────────────────
# Notify worker — subscribes to actions.* and routes to dispatchers
# ─────────────────────────────────────────────────────────────────────


class NotifyWorker:
    """Consumes :class:`ActionEvent` and routes by action_type."""

    def __init__(
        self,
        *,
        push: PushDispatcher | None = None,
        tts: TTSDispatcher | None = None,
        ask_flow: AskFlow | None = None,
    ) -> None:
        self._push = push
        self._tts = tts
        self._ask = ask_flow

    @property
    def ask_flow(self) -> AskFlow | None:
        return self._ask

    async def handle(self, action: ActionEvent) -> None:
        if action.action_type == ActionType.notify_push and self._push is not None:
            await self._push.dispatch(action)
        elif action.action_type == ActionType.notify_speak and self._tts is not None:
            await self._tts.dispatch(action)
        elif action.action_type == ActionType.ask and self._ask is not None:
            self._ask.ask(
                question=action.message or "Confirm?",
                event_id=action.event_id,
                ask_id=action.action_id,
            )
        else:
            logger.debug("notify.skipping", action_type=action.action_type.value)
