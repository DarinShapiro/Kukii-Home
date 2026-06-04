"""Action runtime — perception + protective dispatcher.

Two lanes, sharing the same HA-service-call backend:

  - **Perception (class 2).** TRANSIENT changes the agent makes during its
    own reasoning loop — turn the porch light on briefly to see better,
    PTZ-zoom to read a license plate. Each request schedules a *revert*
    that re-runs the inverse call after ``revert_after_s``. The runtime
    coalesces overlapping requests on the same target so a second
    "porch light on" inside a still-pending revert just extends the
    timer instead of stacking calls.

  - **Protective (class 3).** PERSISTENT mitigation in response to an
    assessed situation — lock the back door, trigger the siren. Each
    recommendation goes through :func:`action_store.gate_recommendation`
    (whitelist + severity/confidence/blackout) and a redundancy counter
    before execution. Every attempt is logged to ``protective_actions_log``
    whether or not it executed.

Both lanes use ``HAClient.call_service``; the runtime only needs that one
method + the action store + an asyncio loop for the revert timers. No HA
imports leak in here so unit tests can substitute a fake caller.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from .action_store import (
    ActionStore,
    GateDecision,
    ProtectiveLogRow,
    gate_recommendation,
)
from .action_store import (
    now as _now,
)

logger = structlog.get_logger(__name__)


# ─── Payload dataclasses ────────────────────────────────────────────


@dataclass
class PerceptionRequest:
    """One transient-action request from the VLM's perception_requests list."""

    kind: Literal["ha_service", "camera_api"]
    service: str | None = None       # ha_service path: "light.turn_on"
    target: str | None = None        # entity_id
    data: dict[str, Any] = field(default_factory=dict)
    camera_id: str | None = None     # required for camera_api
    op: str | None = None            # camera_api op ("ptz_zoom")
    revert_after_s: float = 45.0
    rationale: str | None = None


@dataclass
class ProtectiveRecommendation:
    """One protective recommendation from the VLM's recommendations list."""

    action_class: str
    service: str
    target: str
    data: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    urgency: str | None = None       # mirrors severity (critical/normal/low)
    rationale: str | None = None
    camera_id: str | None = None


HACallerProto = Callable[..., Awaitable[Any]]
"""``call_service(domain, service, *, entity_id, data) -> awaitable``.

Decoupled from :class:`HAClient` so the runtime is testable with a fake."""


# ─── Perception lane ────────────────────────────────────────────────


_INVERSE_SERVICE: dict[str, str] = {
    # Best-effort revert mapping for the common ha_service cases. Add more
    # as the perception vocabulary grows; unknown service → revert is a
    # no-op (logged) since we can't safely guess the inverse.
    "light.turn_on": "light.turn_off",
    "light.turn_off": "light.turn_on",
    "switch.turn_on": "switch.turn_off",
    "switch.turn_off": "switch.turn_on",
}


def _inverse(service: str) -> str | None:
    return _INVERSE_SERVICE.get(service)


@dataclass
class _PendingRevert:
    """Tracking record for a perception target whose revert is pending."""

    task: asyncio.Task
    request: PerceptionRequest


class PerceptionRuntime:
    """Executes class-2 perception requests + their reverts.

    Coalesce policy: keyed on ``(target_kind, target_or_camera_op)``. A
    second request for the same target cancels the prior revert and
    extends the window to the new ``revert_after_s``.
    """

    def __init__(
        self,
        store: ActionStore,
        ha_caller: HACallerProto,
        *,
        camera_tune: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.store = store
        self.ha_caller = ha_caller
        # camera_tune(camera_id, op, data) — wired to preprocessor /tune
        # when present; None in tests / when preprocessor isn't reachable.
        self.camera_tune = camera_tune
        self._pending: dict[tuple[str, str], _PendingRevert] = {}

    def _whitelisted(self, camera_id: str, req: PerceptionRequest) -> bool:
        """True if this perception request is in the camera's whitelist.

        Empty whitelist = nothing authorized. We don't auto-permit even
        innocuous-looking class-2 requests; class 2's transient-ness is a
        contract, not a guess."""
        target_kind = req.kind
        target = (
            f"{req.service}:{req.target}" if req.kind == "ha_service"
            else (req.op or "")
        )
        for entry in self.store.perception_for(camera_id):
            if entry.target_kind == target_kind and entry.target == target:
                return True
        return False

    async def execute(
        self, req: PerceptionRequest, *, incident_id: str
    ) -> str:
        """Run one perception request. Returns one of:
          "ok"                 — call executed; revert scheduled
          "no_authorization"   — whitelist missed (no call made)
          "no_camera_scope"    — missing camera_id (no call made)
          "failed"             — execution raised (revert not scheduled)
        """
        if not req.camera_id:
            logger.info(
                "perception.no_camera_scope", incident_id=incident_id,
                kind=req.kind, target=req.target,
            )
            return "no_camera_scope"
        if not self._whitelisted(req.camera_id, req):
            logger.info(
                "perception.rejected", incident_id=incident_id,
                camera_id=req.camera_id, kind=req.kind,
                target=req.target or req.op,
                reason="no_authorization",
            )
            return "no_authorization"

        coalesce_key = (
            req.kind,
            f"{req.service}:{req.target}" if req.kind == "ha_service"
            else f"{req.camera_id}:{req.op}",
        )
        try:
            await self._apply(req)
        except Exception as e:
            logger.warning(
                "perception.failed", incident_id=incident_id,
                kind=req.kind, target=req.target or req.op, error=str(e),
            )
            return "failed"

        # Cancel any previously pending revert for this key (coalesce).
        prior = self._pending.pop(coalesce_key, None)
        if prior is not None:
            prior.task.cancel()
        # Schedule revert.
        task = asyncio.create_task(self._schedule_revert(req, coalesce_key))
        self._pending[coalesce_key] = _PendingRevert(task=task, request=req)
        logger.info(
            "perception.executed",
            incident_id=incident_id, kind=req.kind,
            target=req.target or req.op, revert_in_s=req.revert_after_s,
        )
        return "ok"

    async def _apply(self, req: PerceptionRequest) -> None:
        if req.kind == "ha_service":
            domain, service = (req.service or "").split(".", 1)
            await self.ha_caller(
                domain, service, entity_id=req.target, data=req.data,
            )
        elif req.kind == "camera_api":
            if self.camera_tune is None:
                raise RuntimeError("camera_tune backend not wired")
            await self.camera_tune(req.camera_id, req.op, req.data)

    async def _schedule_revert(
        self, req: PerceptionRequest, key: tuple[str, str]
    ) -> None:
        try:
            await asyncio.sleep(req.revert_after_s)
        except asyncio.CancelledError:
            # Coalesced — a newer request took over this key.
            return
        try:
            await self._revert(req)
            logger.info("perception.reverted",
                        kind=req.kind, target=req.target or req.op)
        except Exception as e:
            logger.warning(
                "perception.revert_failed",
                kind=req.kind, target=req.target or req.op, error=str(e),
            )
        finally:
            # Clean up the tracking record.
            current = self._pending.get(key)
            if current is not None and current.request is req:
                self._pending.pop(key, None)

    async def _revert(self, req: PerceptionRequest) -> None:
        if req.kind == "ha_service":
            inv = _inverse(req.service or "")
            if inv is None:
                logger.info(
                    "perception.revert_skipped_unknown_inverse",
                    service=req.service,
                )
                return
            domain, service = inv.split(".", 1)
            await self.ha_caller(domain, service, entity_id=req.target, data={})
        elif req.kind == "camera_api":
            # Camera ops revert via the same /tune call with a "revert=true"
            # hint; the preprocessor decides how. None of our current ops
            # have an inverse to invoke directly here.
            if self.camera_tune is None:
                return
            await self.camera_tune(
                req.camera_id, req.op, {**req.data, "_revert": True},
            )

    def pending_count(self) -> int:
        """Diagnostic: number of perception-revert tasks currently scheduled."""
        return sum(1 for r in self._pending.values() if not r.task.done())


# ─── Protective lane ────────────────────────────────────────────────


class ProtectiveRuntime:
    """Executes class-3 recommendations against the per-camera whitelist +
    policy + redundancy buffer. Every attempt → :class:`ProtectiveLogRow`.

    Redundancy: when a whitelist entry requires N consecutive
    recommendations, we count *unique-incident* occurrences with the same
    ``(camera, action_class, service, target)`` shape. The counter is
    kept in-process; persistence across restarts is a future iteration —
    in practice the same camera doesn't accumulate a multi-restart streak
    of recommendations for the same protective action.
    """

    def __init__(
        self, store: ActionStore, ha_caller: HACallerProto,
    ) -> None:
        self.store = store
        self.ha_caller = ha_caller
        # (camera_id, action_class, service, target) → set of seen incident ids
        self._redundancy: dict[tuple[str, str, str, str], set[str]] = (
            defaultdict(set)
        )

    async def execute(
        self,
        rec: ProtectiveRecommendation,
        *,
        incident_id: str,
        severity: str | None = None,
        now_ts: float | None = None,
    ) -> ProtectiveLogRow:
        """Gate + (maybe) execute. Always returns a log row reflecting the
        outcome; the row is also inserted into ``protective_actions_log``.

        ``severity`` is the incident's reasoned severity. If not provided,
        the recommendation's ``urgency`` is used as a proxy."""
        severity = severity or rec.urgency
        ts = now_ts or _now()
        decision: GateDecision = gate_recommendation(
            store=self.store,
            camera_id=rec.camera_id, action_class=rec.action_class,
            service=rec.service, target=rec.target,
            severity=severity, confidence=rec.confidence, now_ts=ts,
        )

        # Redundancy: only enforced after the base gates pass.
        if decision.execute and decision.matched_entry is not None:
            req_n = decision.matched_entry.redundancy_required or 0
            if req_n > 0:
                key = (rec.camera_id or "", rec.action_class, rec.service, rec.target)
                self._redundancy[key].add(incident_id)
                seen_n = len(self._redundancy[key])
                if seen_n < req_n:
                    decision = GateDecision(
                        False,
                        f"redundancy_pending ({seen_n}/{req_n})",
                        decision.matched_entry,
                    )

        if not decision.execute:
            status = (
                "whitelisted_rejected" if decision.reason == "no_authorization"
                else "gated"
            )
            row = ProtectiveLogRow(
                incident_id=incident_id, camera_id=rec.camera_id, ts=ts,
                action_class=rec.action_class, service=rec.service,
                target=rec.target,
                data_json=json.dumps(rec.data) if rec.data else None,
                status=status, gate_reason=decision.reason,
                vlm_confidence=rec.confidence, vlm_rationale=rec.rationale,
            )
            row.id = self.store.log_protective(row)
            logger.info(
                "protective.gated",
                incident_id=incident_id, camera_id=rec.camera_id,
                action_class=rec.action_class, service=rec.service,
                target=rec.target, reason=decision.reason,
            )
            return row

        try:
            domain, service = rec.service.split(".", 1)
            await self.ha_caller(
                domain, service, entity_id=rec.target, data=rec.data,
            )
            status: Literal["ok", "failed"] = "ok"
            gate_reason = None
        except Exception as e:
            status = "failed"
            gate_reason = f"execution_error: {e}"
            logger.warning(
                "protective.failed", incident_id=incident_id,
                action_class=rec.action_class, service=rec.service,
                target=rec.target, error=str(e),
            )

        row = ProtectiveLogRow(
            incident_id=incident_id, camera_id=rec.camera_id, ts=ts,
            action_class=rec.action_class, service=rec.service,
            target=rec.target,
            data_json=json.dumps(rec.data) if rec.data else None,
            status=status, gate_reason=gate_reason,
            vlm_confidence=rec.confidence, vlm_rationale=rec.rationale,
        )
        row.id = self.store.log_protective(row)
        if status == "ok":
            logger.info(
                "protective.executed",
                incident_id=incident_id, camera_id=rec.camera_id,
                action_class=rec.action_class, service=rec.service,
                target=rec.target,
            )
        return row


# ─── VLM payload parsing ────────────────────────────────────────────


def parse_perception_requests(
    payload: list[dict[str, Any]] | None,
) -> list[PerceptionRequest]:
    """``VLMResponse.perception_requests`` → :class:`PerceptionRequest`
    list. Tolerates missing fields by leaving them None / 0 — the
    runtime's whitelist check rejects malformed entries safely."""
    if not payload:
        return []
    out: list[PerceptionRequest] = []
    for p in payload:
        if not isinstance(p, dict):
            continue
        out.append(PerceptionRequest(
            kind=p.get("kind") or "ha_service",
            service=p.get("service"),
            target=p.get("target"),
            data=dict(p.get("data") or {}),
            camera_id=p.get("camera_id"),
            op=p.get("op"),
            revert_after_s=float(p.get("revert_after_s", 45.0)),
            rationale=p.get("rationale"),
        ))
    return out


def parse_recommendations(
    payload: list[dict[str, Any]] | None,
) -> list[ProtectiveRecommendation]:
    """``VLMResponse.recommendations`` → :class:`ProtectiveRecommendation`
    list. ``action_class`` is required; rows without it are skipped (we
    can't gate something whose class we don't know)."""
    if not payload:
        return []
    out: list[ProtectiveRecommendation] = []
    for r in payload:
        if not isinstance(r, dict) or not r.get("action_class"):
            continue
        out.append(ProtectiveRecommendation(
            action_class=str(r["action_class"]),
            service=str(r.get("service") or ""),
            target=str(r.get("target") or ""),
            data=dict(r.get("data") or {}),
            confidence=(
                float(r["confidence"]) if r.get("confidence") is not None
                else None
            ),
            urgency=r.get("urgency"),
            rationale=r.get("rationale"),
            camera_id=r.get("camera_id"),
        ))
    return out
