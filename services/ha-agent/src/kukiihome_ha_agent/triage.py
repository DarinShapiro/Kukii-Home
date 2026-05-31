"""TriageGate — reason about each event, notify only when warranted.

This is the control flow the project is built around: a camera event
does NOT become a notification on its own. Every recorded alert flows
through the gate, which gathers evidence (preprocessor frame window when
an inference box is configured, otherwise just HA's AI classification),
asks the :class:`~kukiihome_ha_agent.reasoning.Reasoner` for a decision,
persists that decision onto the event, and fires the notification *only*
when the decision's ``criticality`` warrants it.

The gate replaces the notifier as the ``AlertLog.add_on_record``
subscriber. So the wiring becomes:

    AlertLog.record(alert)
        → EventStore.record_from_alert   (timeline, always)
        → TriageGate.on_alert            (reason → maybe notify)

Diagnostic alerts (the "Send test alert" buttons) set
``suppress_auto_notify`` and dispatch themselves via
``AlertNotifier.test_send``; the gate skips them so a test always
notifies without being second-guessed by the reasoner.

Everything degrades gracefully: a sleeping inference box yields no
frame window, so the reasoner falls back to HA's classification; a
reasoner error fails OPEN (notify) so a bug in the new path can't
silently swallow a real event.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog

from .enricher import _event_unix_ts
from .event_store import EventStore
from .http_api import AlertLog
from .notifier import AlertNotifier
from .preprocessor_client import PreprocessorClient
from .reasoning import Reasoner, should_notify

logger = structlog.get_logger(__name__)


@dataclass
class TriageGate:
    """Reason about each recorded alert and gate the notification."""

    reasoner: Reasoner
    notifier: AlertNotifier
    event_store: EventStore
    alert_log: AlertLog
    preprocessor: PreprocessorClient | None = None

    window_before_s: float = 4.0
    window_after_s: float = 2.0

    _pending_tasks: set[asyncio.Task] = field(default_factory=set)

    def on_alert(self, alert: dict) -> None:
        """Synchronous ``AlertLog`` callback — schedule async triage.

        Skips alerts flagged ``suppress_auto_notify`` (test diagnostics,
        which dispatch themselves) so the reasoner never silences a test.
        """
        if alert.get("suppress_auto_notify"):
            return
        task = asyncio.create_task(self._evaluate(alert))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _evaluate(self, alert: dict) -> None:
        event_id = str(alert.get("alert_id") or "")
        if not event_id:
            return

        evidence = await self._gather_evidence(alert)

        try:
            decision = await self.reasoner.reason(alert, evidence)
        except Exception as e:
            # Fail OPEN: a bug in the reasoning path must not swallow a
            # potentially real event. Notify, and surface the failure.
            logger.warning("triage.reason_failed", event_id=event_id, error=str(e))
            await self._notify(alert)
            self.alert_log.set_triage(
                event_id,
                status="alerted",
                explanation=f"Reasoning failed ({e}); alerted to be safe.",
                criticality="alert",
            )
            return

        notify = should_notify(decision)
        recognition_status = (
            "preprocessor"
            if evidence is not None and self._has_evidence(evidence)
            else "ha_sensor_only"
        )

        self.event_store.record_decision(
            event_id,
            criticality=decision.criticality.value,
            explanation=decision.explanation or "",
            confidence=decision.confidence,
            backend=decision.backend,
            notified=notify,
            recognition_status=recognition_status,
        )
        self.alert_log.set_triage(
            event_id,
            status="alerted" if notify else "dismissed",
            explanation=decision.explanation or "",
            criticality=decision.criticality.value,
        )

        if notify:
            await self._notify(alert)
        else:
            logger.info(
                "triage.dismissed",
                event_id=event_id,
                criticality=decision.criticality.value,
                explanation=decision.explanation,
            )

    async def _gather_evidence(self, alert: dict):
        """Pull the preprocessor frame window for this event, or None.

        None when no inference box is configured, it's unreachable, or
        the event has no usable timestamp — in all cases the reasoner
        falls back to HA's classification.
        """
        if self.preprocessor is None:
            return None
        camera_id = alert.get("camera_id")
        if not camera_id:
            return None
        event_ts = _event_unix_ts(alert)
        if event_ts is None:
            return None
        return await self.preprocessor.get_frame_window(
            camera_id=str(camera_id),
            ts_start=event_ts - self.window_before_s,
            ts_end=event_ts + self.window_after_s,
        )

    @staticmethod
    def _has_evidence(evidence) -> bool:
        return bool(evidence.identified_entities or evidence.detections)

    async def _notify(self, alert: dict) -> None:
        try:
            await self.notifier.send(alert)
        except Exception as e:
            logger.warning("triage.notify_failed", event_id=alert.get("alert_id"), error=str(e))
