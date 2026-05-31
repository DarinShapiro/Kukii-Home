"""Integration tests for the TriageGate — reason → maybe notify.

Wires a real AlertLog + EventStore (tmp) with a fake notifier so we can
assert: warranted events notify and record 'alerted'; dismissed events
do NOT notify and record 'dismissed' + why; test alerts bypass the gate;
a reasoner crash fails OPEN (notifies).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from kukiihome_ha_agent.event_store import EventStore
from kukiihome_ha_agent.http_api import AlertLog
from kukiihome_ha_agent.reasoning import StubReasoner
from kukiihome_ha_agent.triage import TriageGate

pytestmark = pytest.mark.asyncio


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, alert: dict) -> None:
        self.sent.append(alert)


class _BoomReasoner:
    async def reason(self, alert, evidence):
        raise RuntimeError("kaboom")


def _wire(tmp_path: Path, reasoner=None):
    """Build an AlertLog wired to EventStore + a gate, returning the
    pieces a test needs."""
    alert_log = AlertLog()
    event_store = EventStore(root=tmp_path / "events")
    notifier = _FakeNotifier()
    gate = TriageGate(
        reasoner=reasoner or StubReasoner(),
        notifier=notifier,
        event_store=event_store,
        alert_log=alert_log,
        preprocessor=None,  # no inference box → HA-classification fallback
    )
    # Mirror production wiring order: event dir first, then the gate.
    alert_log.add_on_record(event_store.record_from_alert)
    alert_log.add_on_record(gate.on_alert)
    return alert_log, event_store, notifier, gate


async def _drain(gate: TriageGate) -> None:
    if gate._pending_tasks:
        await asyncio.gather(*list(gate._pending_tasks), return_exceptions=True)


def _alert(alert_id="evt1", **extra) -> dict:
    base = {
        "alert_id": alert_id,
        "camera_id": "poolcam",
        "camera_name": "Pool Cam",
        "headline": "Motion at Pool Cam",
        "recorded_at": "2026-05-28T15:30:00+00:00",
        "ha_last_changed": "2026-05-28T15:30:00+00:00",
        "sensor_classification": "person",
    }
    base.update(extra)
    return base


async def test_person_event_notifies_and_records_alerted(tmp_path):
    alert_log, event_store, notifier, gate = _wire(tmp_path)
    alert_log.record(_alert(sensor_classification="person"))
    await _drain(gate)

    assert len(notifier.sent) == 1  # notified
    meta = event_store.get("evt1")
    assert meta["triage_status"] == "alerted"
    assert meta["vlm_response"]["criticality"] == "alert"
    assert meta["recognition_status"] == "ha_sensor_only"
    assert alert_log.get("evt1")["triage_status"] == "alerted"


async def test_unclassified_motion_is_silenced(tmp_path):
    """The pool-ripple case: generic motion → no notification, but it's
    still recorded as dismissed with the reason."""
    alert_log, event_store, notifier, gate = _wire(tmp_path)
    alert_log.record(_alert(sensor_classification="motion"))
    await _drain(gate)

    assert notifier.sent == []  # NOT notified
    meta = event_store.get("evt1")
    assert meta["triage_status"] == "dismissed"
    assert meta["vlm_response"]["criticality"] == "info"
    entry = alert_log.get("evt1")
    assert entry["triage_status"] == "dismissed"
    assert "dismissed" in entry["triage_explanation"].lower()


async def test_animal_is_silenced(tmp_path):
    alert_log, _es, notifier, gate = _wire(tmp_path)
    alert_log.record(_alert(sensor_classification="animal"))
    await _drain(gate)
    assert notifier.sent == []


async def test_test_alert_bypasses_gate(tmp_path):
    """Diagnostic alerts (suppress_auto_notify) are dispatched by the
    test path itself — the gate must not reason about them or notify."""
    alert_log, _es, notifier, gate = _wire(tmp_path)
    alert_log.record(_alert(sensor_classification="motion", suppress_auto_notify=True))
    await _drain(gate)
    assert notifier.sent == []
    assert len(gate._pending_tasks) == 0  # no task even scheduled


async def test_reasoner_crash_fails_open(tmp_path):
    """A bug in reasoning must NOT swallow a potentially real event —
    fail open: notify, and record the failure."""
    alert_log, _es, notifier, gate = _wire(tmp_path, reasoner=_BoomReasoner())
    alert_log.record(_alert(sensor_classification="motion"))
    await _drain(gate)

    assert len(notifier.sent) == 1  # alerted to be safe
    assert alert_log.get("evt1")["triage_status"] == "alerted"


async def test_event_without_id_is_ignored(tmp_path):
    _log, _es, notifier, gate = _wire(tmp_path)
    # record() needs an id to persist sanely; call the gate directly.
    await gate._evaluate({"camera_id": "x"})  # no alert_id
    assert notifier.sent == []
