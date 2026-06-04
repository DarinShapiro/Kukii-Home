"""Build GuidanceEntry rows from every store for /memory (Part IX §28).

Pure functions — given a bundle of stores, returns the flattened entry
list the renderer consumes. Keeps the renderer testable without
constructing a full boot state.
"""

from __future__ import annotations

from typing import Any

from kukiihome_ha_agent.web_ui.memory import GuidanceEntry


def _scope_from_rule(rule: Any) -> tuple[str, dict[str, Any]]:
    """RuleScope → (display summary, scope_fields dict for classification)."""
    fields: dict[str, Any] = {}
    parts: list[str] = []
    if rule.scope.areas:
        fields["area"] = rule.scope.areas[0]
        parts.append(" / ".join(rule.scope.areas))
    if rule.scope.cameras:
        fields["camera"] = rule.scope.cameras[0]
        parts.append(" / ".join(rule.scope.cameras))
    if rule.scope.time_windows:
        parts.append(f"{len(rule.scope.time_windows)} time windows")
    return " · ".join(parts), fields


def _scope_from_descriptor(desc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Policy descriptor → (display, fields). The descriptor is JSON so we
    don't trust types blindly."""
    fields: dict[str, Any] = {}
    parts: list[str] = []
    for key in ("actor", "area", "camera", "kind"):
        v = desc.get(key)
        if isinstance(v, str) and v:
            fields[key] = v
            parts.append(v)
    return " · ".join(parts), fields


def build_guidance_entries(
    *,
    rules: list[Any],
    preferences: Any | None,
    policies: list[Any],
    areas: list[Any],
    provenance_store: Any | None = None,
) -> list[GuidanceEntry]:
    """Collect every guidance row across stores. Order: Rules → Preferences
    → Policies → SituationalContexts → Area postures."""
    out: list[GuidanceEntry] = []

    # Rules ──────────────────────────────────────────────
    for r in rules:
        scope_summary, scope_fields = _scope_from_rule(r)
        prov = _lookup_provenance(provenance_store, r.id)
        out.append(GuidanceEntry(
            guidance_id=r.id, name=r.name, storage_class="rule",
            scope_summary=scope_summary, scope_fields=scope_fields,
            lifecycle="persistent",
            last_applied_ts=getattr(r, "last_matched_at", None),
            apply_count=getattr(r, "matched_count", 0),
            provenance_origin=prov,
            detail_url=f"intent/rules/{r.id}/edit",
        ))

    # Preferences (singleton flattened to up to 4 rows) ─────────
    if preferences is not None:
        prov = _lookup_provenance(provenance_store, "preferences:singleton")
        out.append(GuidanceEntry(
            guidance_id="preferences:vigilance", name="Vigilance baseline",
            storage_class="preference",
            scope_summary=f"current: {preferences.vigilance}",
            lifecycle="persistent",
            provenance_origin=prov,
            detail_url="intent",
        ))
        if preferences.what_i_care_about:
            out.append(GuidanceEntry(
                guidance_id="preferences:what_i_care_about",
                name="What I care about",
                storage_class="preference",
                scope_summary=(preferences.what_i_care_about[:80] + "…")
                if len(preferences.what_i_care_about) > 80
                else preferences.what_i_care_about,
                lifecycle="persistent",
                provenance_origin=prov,
                detail_url="intent",
            ))
        if preferences.quiet_hours:
            out.append(GuidanceEntry(
                guidance_id="preferences:quiet_hours",
                name="Quiet hours",
                storage_class="preference",
                scope_summary=f"{len(preferences.quiet_hours)} window"
                f"{'s' if len(preferences.quiet_hours) != 1 else ''}",
                lifecycle="persistent",
                provenance_origin=prov,
                detail_url="intent",
            ))
        if preferences.relationships:
            out.append(GuidanceEntry(
                guidance_id="preferences:relationships",
                name="Actor relationships",
                storage_class="preference",
                scope_summary=f"{len(preferences.relationships)} labeled",
                lifecycle="persistent",
                provenance_origin=prov,
                detail_url="identities",
            ))

    # Policies (dismissals + transient intents + situational contexts) ──
    for p in policies:
        scope_summary, scope_fields = _scope_from_descriptor(p.descriptor or {})
        is_sc = bool((p.descriptor or {}).get("is_situational_context"))
        storage_class = (
            "situational_context" if is_sc
            else ("transient_intent" if p.kind == "transient_intent"
                  else "dismissal_policy")
        )
        lifecycle = "temporal" if p.expires_at else "persistent"
        prov = _lookup_provenance(provenance_store, p.id)
        out.append(GuidanceEntry(
            guidance_id=p.id, name=p.name, storage_class=storage_class,
            scope_summary=scope_summary, scope_fields=scope_fields,
            lifecycle=lifecycle, expires_at=p.expires_at,
            last_applied_ts=p.last_applied_at,
            apply_count=p.apply_count,
            provenance_origin=prov,
            detail_url=f"policies#{p.id}",
        ))

    # Area postures (only when attention_mode != normal OR role set) ──
    for a in areas:
        if a.attention_mode == "normal" and not a.role:
            continue
        prov = _lookup_provenance(provenance_store, f"area:{a.id}")
        fields = {"area": a.id, "area_name": a.name}
        bits = [f"AttentionMode: {a.attention_mode}"]
        if a.role:
            bits.append(f"role: {a.role}")
        out.append(GuidanceEntry(
            guidance_id=f"area:{a.id}",
            name=f"{a.name} posture",
            storage_class="area_posture",
            scope_summary=" · ".join(bits),
            scope_fields=fields,
            lifecycle="persistent",
            provenance_origin=prov,
            detail_url=f"areas/{a.id}/edit",
        ))

    return out


def _lookup_provenance(provenance_store: Any | None, guidance_id: str) -> str:
    if provenance_store is None:
        return "pre_provenance"
    try:
        p = provenance_store.get_provenance(guidance_id)
    except Exception:
        return "pre_provenance"
    return p.origin if p else "pre_provenance"
