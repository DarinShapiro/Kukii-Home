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

# rules_store imported lazily inside _maybe_evaluate_rules so the gate
# stays importable in environments without the rules backend.

logger = structlog.get_logger(__name__)


@dataclass
class TriageGate:
    """Reason about each recorded alert and gate the notification."""

    reasoner: Reasoner
    notifier: AlertNotifier
    event_store: EventStore
    alert_log: AlertLog
    preprocessor: PreprocessorClient | None = None
    # Task 9: rules pipeline. Both are optional so tests / older boot paths
    # work unchanged — when either is None, the gate skips rule evaluation
    # and the system behaves exactly as before.
    rules_runtime: object | None = None  # RulesRuntime — avoid hard import cycle
    rules_store: object | None = None  # RulesStore — for audit-row writes
    ha_event_fire: object | None = None  # callable(event_type, data) -> awaitable

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

        # Task 9: rule evaluation. Runs AFTER the reasoner so we have the
        # full evidence + identified actors in the alert. Shortcut rules
        # are evaluated deterministically here; NL rules will be folded
        # into the VLM prompt when the real reasoner lands (see
        # rules_runtime.build_nl_prompt_section).
        await self._maybe_evaluate_rules(alert, event_id=event_id, decision=decision)

    async def _maybe_evaluate_rules(self, alert: dict, *, event_id: str, decision) -> None:
        """Fire kukiihome_alert per matched shortcut rule. No-op when the
        rules pipeline isn't wired (older boot path / tests)."""
        if self.rules_runtime is None or self.ha_event_fire is None:
            return
        try:
            camera_id = alert.get("camera_id")
            area_id = alert.get("area_id")
            ts = float(alert.get("trigger_ts") or 0.0) or None
            outcomes = self.rules_runtime.shortcuts_for(
                alert=alert,
                camera_id=camera_id,
                area_id=area_id,
                ts=ts,
            )
        except Exception as e:
            logger.warning("triage.rules_eval_failed", error=str(e))
            return

        for outcome in outcomes:
            payload = self._build_alert_event_payload(
                alert,
                outcome=outcome,
                decision=decision,
                event_id=event_id,
            )
            alert_emitted = True
            try:
                await self.ha_event_fire("kukiihome_alert", payload)
                logger.info(
                    "triage.kukiihome_alert_fired",
                    rule_id=outcome.rule.id,
                    severity=outcome.severity,
                    event_id=event_id,
                )
            except Exception as e:
                logger.warning(
                    "triage.kukiihome_alert_failed",
                    rule_id=outcome.rule.id,
                    error=str(e),
                )
                alert_emitted = False
            # Record the match for the per-rule audit page, whether or not
            # the HA event POST succeeded.
            if self.rules_store is not None:
                try:
                    import time as _time

                    from .rules_store import RuleMatch

                    self.rules_store.record_match(
                        RuleMatch(
                            rule_id=outcome.rule.id,
                            incident_id=event_id,
                            matched_at=_time.time(),
                            severity=outcome.severity,
                            confidence=None,
                            reasoning="shortcut identity match",
                            matched=True,
                            alert_emitted=alert_emitted,
                        )
                    )
                except Exception as e:
                    logger.warning("triage.match_record_failed", error=str(e))

    @staticmethod
    def _build_alert_event_payload(alert: dict, *, outcome, decision, event_id: str) -> dict:
        """Build the kukiihome_alert event body per Task 9 §HA event payload.

        Kept conservative: include the fields HA automations will branch on
        (rule_id, rule_name, severity, scene_description, camera_*, ts) plus
        the alert_id for de-dupe. The richer fields (clip_url, actions_taken)
        land when their producers do."""
        rule = outcome.rule
        return {
            "alert_id": f"alert_{event_id}_{rule.id}",
            "incident_id": event_id,
            "rule_id": rule.id,
            "rule_name": rule.name,
            "ts": float(alert.get("trigger_ts") or 0.0),
            "severity": outcome.severity,
            "confidence": getattr(decision, "confidence", None),
            "scene_description": getattr(decision, "explanation", "") or "",
            "reasoning": "shortcut identity match",
            "camera_id": alert.get("camera_id"),
            "camera_name": alert.get("camera_friendly_name") or alert.get("camera_name"),
            "area_id": alert.get("area_id"),
            "kind": alert.get("sensor_classification") or alert.get("kind"),
            "actors": [outcome.matched_subject_id] if outcome.matched_subject_id else [],
        }

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
