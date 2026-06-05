"""Dual-write the add-on's events + policies into the memory graph.

Epic 10.2, Phase 1. Production stores stay SQLite (rules, policies,
areas, …); these helpers *additionally* mirror the genuinely
graph-shaped data — Events (from fired alerts) and Policies (from
commit_guidance) — into the graph substrate as nodes. The graph is
read by /diagnostics today and will back vector identity search +
RAG retrieval as the substrate matures.

Two hard rules:

1. **Never fatal.** Every mirror is wrapped so a graph failure (bad
   data, Neo4j hiccup) can't break alert recording or guidance commit.
   The SQLite write is authoritative; the graph is a shadow.
2. **Translate, don't trust.** The add-on's alert dict + SQLite Policy
   have different shapes than the graph's :class:`Event` / :class:`Policy`
   dataclasses. We map defensively, coercing/defaulting every field so a
   malformed source row degrades to a sparse node rather than an
   exception.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def mirror_event_from_alert(graph_client: Any, alert: dict[str, Any]) -> None:
    """Write an :class:`Event` node mirroring a fired alert.

    Hooked into ``AlertLog.add_on_record`` at boot, alongside the
    EventStore's ``record_from_alert``. Fires synchronously on the
    record path; cheap (one dict→node write). No-op + log on any error.
    """
    if graph_client is None:
        return
    try:
        from kukiihome_memory.graph.types import Event

        event_id = alert.get("alert_id") or alert.get("event_id")
        if not event_id:
            return
        graph_client.write_event(
            Event(
                id=str(event_id),
                ts=_coerce_ts(alert),
                camera_id=str(alert.get("camera_id") or ""),
                tag_set=_tag_set_from_alert(alert),
                matched_actor_ids=_actor_ids_from_alert(alert),
                metadata=_event_metadata(alert),
            )
        )
    except Exception as e:
        logger.debug("graph_mirror.event_failed", error=str(e))


def mirror_policy(graph_client: Any, policy: Any) -> None:
    """Write a :class:`Policy` node mirroring a SQLite PolicyStore entry.

    Called from commit_guidance after a dismissal / transient_intent
    commits. ``policy`` is the ha-agent ``policy_store.Policy`` (descriptor
    dict + expires_at), translated to the graph's flatter Policy shape.
    """
    if graph_client is None or policy is None:
        return
    try:
        from kukiihome_memory.graph.types import Policy as GraphPolicy

        descriptor = getattr(policy, "descriptor", None) or {}
        created = float(getattr(policy, "created_at", 0.0) or 0.0) or time.time()
        expires = getattr(policy, "expires_at", None)
        ttl = max(0.0, float(expires) - created) if expires is not None else 0.0
        graph_client.write_policy(
            GraphPolicy(
                id=str(getattr(policy, "id", "") or ""),
                kind=str(getattr(policy, "kind", "") or "dismissal"),
                scope_camera=(descriptor.get("camera") or None),
                match_tag_subset=_tag_subset_from_descriptor(descriptor),
                ttl_seconds=ttl,
                created_ts=created,
                rationale=str(descriptor.get("intent_text") or getattr(policy, "name", "") or ""),
            )
        )
    except Exception as e:
        logger.debug("graph_mirror.policy_failed", error=str(e))


# ─── translation helpers (all defensive) ────────────────────────────


def _coerce_ts(alert: dict[str, Any]) -> float:
    for key in ("ts", "timestamp", "matched_at", "created_at"):
        v = alert.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return time.time()


def _tag_set_from_alert(alert: dict[str, Any]) -> tuple[str, ...]:
    """Best-effort tag set. Alerts don't carry a canonical tag list yet;
    we pull from the most likely fields and normalize to a sorted tuple
    of non-empty strings so the graph stays queryable."""
    raw: Any = alert.get("tag_set") or alert.get("tags") or alert.get("subject") or ()
    if isinstance(raw, str):
        raw = [raw]
    out = (
        sorted({s.strip() for s in raw if isinstance(s, str) and s.strip()})
        if isinstance(raw, (list, tuple, set))
        else []
    )
    return tuple(out)


def _actor_ids_from_alert(alert: dict[str, Any]) -> tuple[str, ...]:
    raw: Any = alert.get("matched_actor_ids") or alert.get("identities") or ()
    if not isinstance(raw, (list, tuple, set)):
        return ()
    return tuple(str(a) for a in raw if isinstance(a, (str, int)) and str(a))


def _event_metadata(alert: dict[str, Any]) -> dict[str, str]:
    """A small, string-valued metadata slice for audit/debug. Neo4j only
    takes scalar properties, so we keep this flat + stringified."""
    md: dict[str, str] = {}
    for key in ("severity", "headline", "area_id", "triage_decision"):
        v = alert.get(key)
        if v is not None and v != "":
            md[key] = str(v)
    return md


def _tag_subset_from_descriptor(descriptor: dict[str, Any]) -> tuple[str, ...]:
    """Derive a dismissal policy's tag-subset match condition from its
    descriptor. Falls back to the `subject` when no explicit tag set."""
    raw: Any = (
        descriptor.get("match_tag_subset")
        or descriptor.get("tags")
        or descriptor.get("subject")
        or ()
    )
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return ()
    return tuple(sorted({s.strip() for s in raw if isinstance(s, str) and s.strip()}))
