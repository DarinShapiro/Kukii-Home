"""commit_guidance — the single write surface for every guidance entry.

Part X §37. Every guidance write — conversational, form-authored, or
system-proposed — funnels through one function. The function:

1. Validates the proposal against the storage class's schema
2. Routes to the right store (RulesStore / PreferencesStore / ...)
3. Writes the entry there
4. Writes a guidance_provenance row in the ProvenanceStore
5. Returns the new entry id

Refinement reuses the same path: a refined proposal + a new transcript
turn updates the entry's data in its store AND appends the new
transcript_id to the existing provenance row's ``refinement_transcript_ids``.

The function takes an explicit ``Stores`` bundle so the route handler
can wire it once at boot and tests can inject fakes. The bundle is a
plain dataclass — easier to test than a global ``boot`` dependency.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

import structlog

from kukiihome_ha_agent.action_store import (
    ActionStore,
)
from kukiihome_ha_agent.area_store import AreaStore
from kukiihome_ha_agent.policy_store import Policy, PolicyStore
from kukiihome_ha_agent.preferences_store import PreferencesStore
from kukiihome_ha_agent.provenance_store import (
    PlacementProposal,
    Provenance,
    ProvenanceStore,
)
from kukiihome_ha_agent.rules_store import Rule, RuleScope, RulesStore

logger = structlog.get_logger(__name__)


Origin = Literal["conversation", "form", "system_proposed"]


@dataclass
class GuidanceStores:
    """Bundle of every per-class store the dispatcher routes to. None
    means *that storage class isn't wired in this build* — commit_guidance
    raises a clear error rather than crashing on attribute access."""

    rules: RulesStore | None = None
    preferences: PreferencesStore | None = None
    policies: PolicyStore | None = None
    actions: ActionStore | None = None
    areas: AreaStore | None = None
    provenance: ProvenanceStore | None = None


# ─── The write surface ─────────────────────────────────────────────


def commit_guidance(
    proposal: PlacementProposal, *,
    stores: GuidanceStores,
    origin: Origin = "conversation",
    transcript_id: str = "",
    user_utterance: str = "",
    now_ts: float | None = None,
) -> str:
    """Single write surface. Returns the new guidance entry's id.

    Refinement path: when the proposal's scope carries
    ``refines_guidance_id``, the entry pointed to is updated in place
    via ``refine_guidance`` and the provenance row's
    ``refinement_transcript_ids`` is appended. This is how multi-turn
    drawer conversations build on a prior placement instead of
    duplicating it (Part X §38)."""
    if stores.provenance is None:
        raise RuntimeError(
            "commit_guidance requires a ProvenanceStore — none wired",
        )

    # Refinement short-circuit: when scope.refines_guidance_id is set,
    # the dispatcher decided this utterance updates a prior entry. The
    # field is stripped before per-class commit so it doesn't leak into
    # the stored scope.
    refines_id = (proposal.scope or {}).pop("refines_guidance_id", "")
    if refines_id and transcript_id:
        existing = stores.provenance.get_provenance(refines_id)
        if existing is not None:
            refine_guidance(
                refines_id, proposal, stores=stores,
                transcript_id=transcript_id,
                user_utterance=user_utterance,
                now_ts=now_ts,
            )
            return refines_id

    now = now_ts or time.time()
    sc = proposal.storage_class

    if sc == "rule":
        guidance_id = _commit_rule(proposal, stores=stores)
    elif sc == "preference":
        guidance_id = _commit_preference(proposal, stores=stores)
    elif sc == "transient_intent":
        guidance_id = _commit_transient_intent(proposal, stores=stores)
    elif sc == "dismissal_policy":
        guidance_id = _commit_dismissal_policy(proposal, stores=stores)
    elif sc == "situational_context":
        # SituationalContexts ride on the policies store for v1 since they
        # share the temporal + soft-prior shape. The kind discriminator
        # ('situational_context') keeps them distinct in reads.
        guidance_id = _commit_situational_context(proposal, stores=stores)
    elif sc == "access_profile":
        # Access profiles are KnownActor.access_profile under the hood —
        # they're guidance-shape but live in the identity gallery. For v1,
        # write to a degenerate Rule (or expand IdentityStore later).
        raise NotImplementedError(
            "access_profile commit lands when /identities expansion ships",
        )
    elif sc == "area_posture":
        guidance_id = _commit_area_posture(proposal, stores=stores)
    else:
        raise ValueError(f"unknown storage_class: {sc}")

    stores.provenance.record_provenance(Provenance(
        guidance_id=guidance_id,
        origin=origin,
        transcript_id=transcript_id,
        user_utterance=user_utterance,
        placement_reasoning=proposal.reasoning,
        user_confirmed_at=now,
    ))
    logger.info(
        "guidance.committed",
        storage_class=sc, guidance_id=guidance_id, origin=origin,
    )
    return guidance_id


def refine_guidance(
    guidance_id: str, proposal: PlacementProposal, *,
    stores: GuidanceStores,
    transcript_id: str,
    user_utterance: str = "",  # noqa: ARG001 — accepted for symmetry with commit_guidance; reserved for future denormalization
    now_ts: float | None = None,
) -> str:
    """Update an existing guidance entry with a refined proposal +
    append the new transcript turn id to its provenance.

    The refined proposal MUST have the same storage_class as the
    existing entry. Conversion across storage classes (e.g., turn a
    rule into a preference) is a separate operation (Part X §39
    backstop #2) — call commit_guidance with the new class + delete
    the old entry.
    """
    if stores.provenance is None:
        raise RuntimeError("refine_guidance requires a ProvenanceStore")
    existing = stores.provenance.get_provenance(guidance_id)
    if existing is None:
        raise ValueError(f"no provenance for {guidance_id}")

    # now_ts kept on the signature for symmetry with commit_guidance even
    # though refine doesn't re-stamp user_confirmed_at — the original
    # confirmation timestamp is the canonical one.
    _ = now_ts
    sc = proposal.storage_class

    if sc == "rule":
        _update_rule(guidance_id, proposal, stores=stores)
    elif sc == "preference":
        _update_preference(proposal, stores=stores)
    elif sc == "transient_intent":
        _update_policy(guidance_id, proposal, stores=stores)
    elif sc == "dismissal_policy":
        _update_policy(guidance_id, proposal, stores=stores)
    elif sc == "situational_context":
        _update_policy(guidance_id, proposal, stores=stores)
    elif sc == "area_posture":
        _update_area_posture(proposal, stores=stores)
    else:
        raise ValueError(f"refine: unknown storage_class: {sc}")

    stores.provenance.append_refinement(guidance_id, transcript_id)
    logger.info(
        "guidance.refined", guidance_id=guidance_id, transcript_id=transcript_id,
    )
    return guidance_id


# ─── Per-class routers ────────────────────────────────────────────


def _require(store: Any, name: str) -> Any:
    if store is None:
        raise RuntimeError(f"commit_guidance: {name} store not wired")
    return store


def _scope_from_proposal(p: PlacementProposal) -> RuleScope:
    """Map the proposal's free-form ``scope`` dict to the RulesStore
    ``RuleScope`` triplet. Actor / kind / pattern keys are not part of
    the rule schema today; they ride in the intent_text the VLM reads
    until the schema grows to support them."""
    return RuleScope(
        cameras=[p.scope["camera"]] if p.scope.get("camera") else [],
        areas=[p.scope["area"]] if p.scope.get("area") else [],
        time_windows=[],
    )


def _commit_rule(p: PlacementProposal, *, stores: GuidanceStores) -> str:
    rs = _require(stores.rules, "rules")
    rule = Rule(
        id="", name=p.name, mode="nl", intent_text=p.intent_text,
        scope=_scope_from_proposal(p),
        severity_static=p.severity if p.severity else None,
        enabled=True,
    )
    created = rs.create(rule)
    return created.id


def _update_rule(rid: str, p: PlacementProposal, *, stores: GuidanceStores) -> None:
    rs = _require(stores.rules, "rules")
    rs.update(
        rid, name=p.name, intent_text=p.intent_text,
        severity_static=p.severity if p.severity else None,
        scope=_scope_from_proposal(p),
    )


def _commit_preference(p: PlacementProposal, *, stores: GuidanceStores) -> str:
    ps = _require(stores.preferences, "preferences")
    # Preferences is singleton-style; the proposal's intent_text becomes
    # the what_i_care_about field by default. Other fields (vigilance,
    # quiet_hours) are routed via fire_affordance/scope hints when the
    # LLM extracted them; otherwise the singleton row's existing values
    # are preserved.
    patch: dict[str, Any] = {}
    if "vigilance" in p.scope:
        patch["vigilance"] = p.scope["vigilance"]
    if p.intent_text:
        patch["what_i_care_about"] = p.intent_text
    ps.update(**patch)
    # Preferences is singleton; identifier is a constant string. The
    # provenance row keys off this constant so /memory can resolve it.
    return "preferences:singleton"


def _update_preference(p: PlacementProposal, *, stores: GuidanceStores) -> None:
    ps = _require(stores.preferences, "preferences")
    patch: dict[str, Any] = {}
    if "vigilance" in p.scope:
        patch["vigilance"] = p.scope["vigilance"]
    if p.intent_text:
        patch["what_i_care_about"] = p.intent_text
    ps.update(**patch)


def _commit_transient_intent(
    p: PlacementProposal, *, stores: GuidanceStores,
) -> str:
    pols = _require(stores.policies, "policies")
    expires_at = _parse_iso_to_epoch(p.lifecycle_ttl_iso)
    pol = Policy(
        id="", kind="transient_intent", name=p.name,
        descriptor={**p.scope, "intent_text": p.intent_text,
                    "fire_once": p.lifecycle == "fire_once"},
        rationale=p.reasoning,
        expires_at=expires_at,
    )
    created = pols.create(pol)
    return created.id


def _commit_dismissal_policy(
    p: PlacementProposal, *, stores: GuidanceStores,
) -> str:
    pols = _require(stores.policies, "policies")
    expires_at = _parse_iso_to_epoch(p.lifecycle_ttl_iso)
    pol = Policy(
        id="", kind="dismissal", name=p.name,
        descriptor={**p.scope, "intent_text": p.intent_text},
        rationale=p.reasoning,
        expires_at=expires_at,
    )
    created = pols.create(pol)
    return created.id


def _commit_situational_context(
    p: PlacementProposal, *, stores: GuidanceStores,
) -> str:
    """SituationalContexts ride on the policies table with kind='transient_intent'
    + a 'situational_context' marker in descriptor for v1. When/if they earn
    their own store, the migration is one ALTER + a kind rename."""
    pols = _require(stores.policies, "policies")
    expires_at = _parse_iso_to_epoch(p.lifecycle_ttl_iso)
    pol = Policy(
        id="", kind="transient_intent", name=p.name,
        descriptor={**p.scope, "intent_text": p.intent_text,
                    "is_situational_context": True},
        rationale=p.reasoning,
        expires_at=expires_at,
    )
    created = pols.create(pol)
    return created.id


def _update_policy(pid: str, p: PlacementProposal, *, stores: GuidanceStores) -> None:
    pols = _require(stores.policies, "policies")
    existing = pols.get(pid)
    if existing is None:
        raise ValueError(f"no policy {pid}")
    existing.name = p.name
    existing.descriptor = {**p.scope, "intent_text": p.intent_text}
    existing.rationale = p.reasoning
    new_ttl = _parse_iso_to_epoch(p.lifecycle_ttl_iso)
    if new_ttl is not None:
        existing.expires_at = new_ttl
    # PolicyStore lacks an update() today — re-create via a delete+create
    # would lose hit history. For v1 we patch the row directly via SQL.
    pols._conn.execute(  # type: ignore[attr-defined]
        "UPDATE policies SET name = ?, descriptor = ?, rationale = ?, "
        "expires_at = ? WHERE id = ?",
        (existing.name, _json_dumps(existing.descriptor), existing.rationale,
         existing.expires_at, pid),
    )
    pols._conn.commit()  # type: ignore[attr-defined]


def _commit_area_posture(
    p: PlacementProposal, *, stores: GuidanceStores,
) -> str:
    """Area posture (attention_mode + role) is an Area.update() call.
    The proposal's scope must include the area id."""
    ars = _require(stores.areas, "areas")
    area_id = p.scope.get("area") or p.scope.get("area_id")
    if not area_id:
        raise ValueError("area_posture proposal must scope to an area id")
    patch: dict[str, Any] = {}
    # scope keys carry the new values for area posture commits
    if "attention_mode" in p.scope:
        patch["attention_mode"] = p.scope["attention_mode"]
    if "role" in p.scope:
        patch["role"] = p.scope["role"]
    if not patch:
        raise ValueError("area_posture: nothing to update")
    ars.update(area_id, **patch)
    # The guidance id for an area posture commit is the area id itself,
    # so the provenance row co-locates with the area.
    return f"area:{area_id}"


def _update_area_posture(p: PlacementProposal, *, stores: GuidanceStores) -> None:
    _commit_area_posture(p, stores=stores)


# ─── Helpers ────────────────────────────────────────────────────────


def _parse_iso_to_epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _json_dumps(d: Any) -> str:
    import json
    return json.dumps(d)
