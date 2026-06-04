"""Build view models for the cameras page from the runtime state.

Pure functions — easy to test. The route handler hands these the live
``BootState`` + the ``AlertLog`` + the ``ActionStore`` and gets back the
``CameraSummary`` / ``CameraDetailViewModel`` the renderers consume.

Kept separate from cameras.py so the rendering layer stays import-free of
boot-time concerns and the data-mapping logic gets its own unit tests.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterable
from typing import Any

from kukiihome_ha_agent.web_ui.cameras import (
    CameraDetailViewModel,
    CameraSummary,
    CapabilityRow,
    PerceptionEntryView,
    ProtectiveEntryView,
)
from kukiihome_ha_agent.web_ui.shell import camera_display_name

# ─── List page ──────────────────────────────────────────────────────


def _events_within(alerts: Iterable[dict], *, camera_id: str, since: float) -> int:
    n = 0
    for a in alerts:
        if a.get("camera_id") != camera_id:
            continue
        ts = float(a.get("trigger_ts") or 0.0)
        if ts >= since:
            n += 1
    return n


def _last_motion_for(alerts: Iterable[dict], *, camera_id: str) -> float | None:
    last: float | None = None
    for a in alerts:
        if a.get("camera_id") != camera_id:
            continue
        ts = float(a.get("trigger_ts") or 0.0)
        if last is None or ts > last:
            last = ts
    return last if last and last > 0 else None


def build_camera_summaries(
    *, registry_statuses: list[Any], ha_loops: list[Any],
    alerts: list[dict], now_ts: float | None = None,
) -> list[CameraSummary]:
    """Merge the RTSP-loop registry with the HA-loop list, dedup by
    camera_id, attach 24h event counts + last_motion_ts from the alert log."""
    now = now_ts or time.time()
    since = now - 86400.0
    by_id: dict[str, dict[str, Any]] = {}

    for st in registry_statuses or []:
        cid = getattr(st, "camera_id", None) or ""
        if not cid:
            continue
        by_id[cid] = {
            "camera_id": cid,
            "name": cid,
            "state": getattr(st, "state", "starting"),
            "last_error": getattr(st, "last_error", "") or "",
            "frames_read": getattr(st, "frames_read", 0),
            "motion_events": getattr(st, "motion_events", 0),
        }

    for loop in ha_loops or []:
        cid = getattr(loop, "camera_id", None) or getattr(loop, "id", "")
        if not cid:
            continue
        slot = by_id.setdefault(cid, {
            "camera_id": cid, "name": cid, "state": "unknown",
        })
        slot["name"] = (
            getattr(loop, "friendly_name", "") or slot.get("name") or cid
        )
        # HA loop status doesn't currently expose state; left as is.

    out: list[CameraSummary] = []
    for cid, slot in by_id.items():
        out.append(CameraSummary(
            camera_id=cid,
            name=camera_display_name(slot.get("name") or cid) or cid,
            state=slot.get("state", "unknown"),
            last_error=slot.get("last_error", ""),
            events_24h=_events_within(alerts, camera_id=cid, since=since),
            last_motion_ts=_last_motion_for(alerts, camera_id=cid),
        ))
    out.sort(key=lambda c: c.name.lower())
    return out


# ─── Detail view model ──────────────────────────────────────────────


def infer_capability_matrix(
    alerts: list[dict], *, camera_id: str,
) -> list[CapabilityRow]:
    """Best-effort matrix from observed alert classifications.

    Until the preprocessor exposes a per-camera capability profile, we
    infer source-of-truth from the alert stream: if HA's classification
    fires events on this camera, we assume that signal is at least
    AUGMENTED (HA triggers, we may enrich); when only the preprocessor
    identifies a subject of a given kind, SUBSTITUTED. Motion is
    always present (otherwise we wouldn't have any events).

    Signals shown:
      - motion (always NATIVE if any events at all)
      - person, vehicle, dog/cat — AUGMENTED if seen in classifications,
        else MISSING (warns: most users want at least person)
      - package — MISSING by default; needs upstream support
    """
    seen_kinds: Counter[str] = Counter()
    for a in alerts:
        if a.get("camera_id") != camera_id:
            continue
        kind = (a.get("sensor_classification") or "").strip().lower()
        if kind:
            seen_kinds[kind] += 1

    rows: list[CapabilityRow] = []
    has_any = sum(seen_kinds.values()) > 0 or bool(seen_kinds)

    rows.append(CapabilityRow(
        signal="motion",
        source="NATIVE" if has_any else "MISSING",
        detail="HA motion sensor" if has_any else "no events recorded yet",
        critical_if_missing=True,
        needs_action=not has_any,
    ))

    for label, kinds, critical in (
        ("person", ("person",), False),
        ("vehicle", ("vehicle", "car", "truck"), False),
        ("dog/cat", ("dog", "cat", "animal", "pet"), False),
        ("package", ("package",), False),
    ):
        if any(k in seen_kinds for k in kinds):
            rows.append(CapabilityRow(
                signal=label, source="AUGMENTED",
                detail=f"HA classification ({', '.join(k for k in kinds if k in seen_kinds)})",
            ))
        else:
            rows.append(CapabilityRow(
                signal=label, source="MISSING",
                detail="not produced by this camera or HA",
                critical_if_missing=critical,
                needs_action=False,
            ))
    return rows


def build_camera_detail(
    *, camera_id: str, registry_statuses: list[Any], ha_loops: list[Any],
    alerts: list[dict], perception_entries: list[Any],
    protective_entries: list[Any], now_ts: float | None = None,
) -> CameraDetailViewModel | None:
    """Compose the full detail view model. Returns None when the camera is
    completely unknown to the registry + ha_loops (404)."""
    summaries = build_camera_summaries(
        registry_statuses=registry_statuses, ha_loops=ha_loops,
        alerts=alerts, now_ts=now_ts,
    )
    summary = next((c for c in summaries if c.camera_id == camera_id), None)
    if summary is None:
        return None

    health: dict[str, Any] = {}
    for st in registry_statuses or []:
        if getattr(st, "camera_id", "") == camera_id:
            health["frames_read"] = getattr(st, "frames_read", 0)
            health["motion_events"] = getattr(st, "motion_events", 0)
            health["last_error"] = getattr(st, "last_error", "") or ""
            break

    return CameraDetailViewModel(
        camera_id=camera_id,
        name=summary.name,
        state=summary.state,
        last_error=summary.last_error,
        events_24h=summary.events_24h,
        last_motion_ts=summary.last_motion_ts,
        snapshot_url=f"cameras/{camera_id}/snapshot",
        capabilities=infer_capability_matrix(alerts, camera_id=camera_id),
        perception_whitelist=[
            PerceptionEntryView(
                target_kind=p.target_kind, target=p.target,
                max_duration_s=p.max_duration_s,
            )
            for p in (perception_entries or [])
        ],
        protective_whitelist=[
            ProtectiveEntryView(
                action_class=p.action_class, service=p.service,
                target=p.target, min_severity=p.min_severity,
                min_confidence=p.min_confidence,
            )
            for p in (protective_entries or [])
        ],
        health=health,
    )
