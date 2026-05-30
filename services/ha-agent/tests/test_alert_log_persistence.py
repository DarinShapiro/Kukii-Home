"""Tests for AlertLog persistence (v0.3.12)."""

from __future__ import annotations

from pathlib import Path

from sentihome_ha_agent.http_api import AlertLog


def test_in_memory_only_when_no_path():
    log = AlertLog()
    log.record({"alert_id": "a"})
    assert len(log.recent(10)) == 1


def test_record_persists_to_disk(tmp_path: Path):
    p = tmp_path / "alerts.json"
    log = AlertLog(persist_path=str(p))
    log.record({"alert_id": "x", "headline": "h"})
    assert p.exists()


def test_alerts_survive_recreate(tmp_path: Path):
    """The whole point: alerts should reappear after a restart."""
    p = tmp_path / "alerts.json"
    log1 = AlertLog(persist_path=str(p))
    log1.record({"alert_id": "a", "headline": "first"})
    log1.record({"alert_id": "b", "headline": "second"})

    # Fresh AlertLog reading the same file = same state.
    log2 = AlertLog(persist_path=str(p))
    recent = log2.recent(10)
    assert len(recent) == 2
    assert [a["alert_id"] for a in recent] == ["a", "b"]


def test_load_handles_missing_file(tmp_path: Path):
    p = tmp_path / "alerts.json"
    log = AlertLog(persist_path=str(p))
    assert log.recent(10) == []


def test_load_handles_corrupt_file(tmp_path: Path):
    p = tmp_path / "alerts.json"
    p.write_text("{not json", encoding="utf-8")
    # Must not raise — empty start is fine.
    log = AlertLog(persist_path=str(p))
    assert log.recent(10) == []


def test_load_drops_non_dict_entries(tmp_path: Path):
    """Hand-edited bad entry shouldn't crash the loader."""
    p = tmp_path / "alerts.json"
    p.write_text(
        '[{"alert_id": "good"}, "bad string", {"alert_id": "good2"}]',
        encoding="utf-8",
    )
    log = AlertLog(persist_path=str(p))
    ids = [a["alert_id"] for a in log.recent(10)]
    assert ids == ["good", "good2"]


def test_record_trims_to_max_entries(tmp_path: Path):
    p = tmp_path / "alerts.json"
    log = AlertLog(persist_path=str(p), max_entries=3)
    for i in range(5):
        log.record({"alert_id": f"a{i}"})
    ids = [a["alert_id"] for a in log.recent(10)]
    assert ids == ["a2", "a3", "a4"]


def test_acknowledge_persists(tmp_path: Path):
    p = tmp_path / "alerts.json"
    log = AlertLog(persist_path=str(p))
    log.record({"alert_id": "a"})
    log.acknowledge("a", feedback="correct")
    log2 = AlertLog(persist_path=str(p))
    assert log2.get("a")["acknowledged"] is True
    assert log2.get("a")["feedback"] == "correct"


def test_on_record_callback_fires(tmp_path: Path):
    log = AlertLog(persist_path=str(tmp_path / "x.json"))
    seen: list[dict] = []
    log.add_on_record(seen.append)
    log.record({"alert_id": "z"})
    assert len(seen) == 1
    assert seen[0]["alert_id"] == "z"


def test_add_on_record_is_idempotent(tmp_path: Path):
    """Registering the same callback twice must not fire it twice — an
    accidental double-wire (e.g. a re-bind on reconnect) can't be
    allowed to multiply notifications per alert."""
    log = AlertLog(persist_path=str(tmp_path / "x.json"))
    seen: list[dict] = []
    log.add_on_record(seen.append)
    log.add_on_record(seen.append)  # duplicate registration
    log.record({"alert_id": "z"})
    assert len(seen) == 1  # fired once, not twice


def test_on_record_callback_exception_doesnt_break_record(tmp_path: Path):
    log = AlertLog(persist_path=str(tmp_path / "x.json"))

    def boom(alert):
        raise RuntimeError("nope")

    log.add_on_record(boom)
    log.record({"alert_id": "z"})  # must not raise
    assert len(log.recent(10)) == 1
