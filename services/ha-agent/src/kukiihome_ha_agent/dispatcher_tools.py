"""Read-only memory-layer tools the dispatcher LLM can call (Part X §35).

Each tool corresponds to a memory layer in the model (Part IX §26):

  - Layer 4 (Identity) — ``get_known_actor``
    Surfaces a labeled subject's display name + species + modalities
    + appearance count + the access profile that the VLM also reads
    when scoring future events.

  - Layer 5 (Semantic) — ``search_existing_guidance``
    Finds rules / preferences / policies / situational contexts /
    area postures already in the system that match a candidate
    placement's scope. The LLM uses this to decide whether to refine
    an existing entry vs. author a fresh one — closes the
    duplicate-rule failure mode that single-turn dispatching can't
    catch.

  - Layer 3 (Episodic) — ``get_recent_events`` (added in a follow-up
    once we have a query primitive on ``AlertLog`` for filter-by-
    actor + filter-by-camera; the alert_log shape doesn't support
    it cleanly yet).

All tools are STRICTLY read-only. The single write surface is
``commit_guidance`` which only fires when the user confirms a
preview card — never from inside a tool call.

Tools follow the OpenAI Chat Completions tool-calling protocol:
  - ``spec()`` returns the JSON dict the model sees in ``tools=[…]``
  - ``execute(args)`` runs the tool with the model-provided args
  - the LLM's response carries a ``tool_calls`` array; the dispatcher
    loop resolves each call and feeds the result back via a
    ``tool`` role message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


# ─── Tool protocol ─────────────────────────────────────────────────


class Tool(Protocol):
    """One callable the LLM may invoke during placement reasoning."""

    name: str
    description: str

    def spec(self) -> dict[str, Any]:
        """Return the OpenAI tool spec — ``{"type": "function",
        "function": {"name": ..., "description": ..., "parameters": ...}}``."""
        ...

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        """Run the tool with the model-provided arguments. Returns a
        JSON-serializable dict the dispatcher feeds back to the model.
        Errors should be returned as ``{"error": "..."}`` rather than
        raised so the LLM can react."""
        ...


# ─── search_existing_guidance ──────────────────────────────────────


@dataclass
class SearchExistingGuidance:
    """Layer 5 (Semantic): find guidance entries already in the system
    matching the candidate scope. The dispatcher hands the LLM enough
    info to choose between *"refine existing"* vs. *"author new"*.

    The tool reads through the same store bundle ``commit_guidance``
    writes through. Empty stores → empty results, no errors.
    """

    name: str = "search_existing_guidance"
    description: str = (
        "Find Rules, Preferences, DismissalPolicies, TransientIntents, "
        "SituationalContexts, or area postures already in the system that "
        "scope-match the candidate placement. Use this BEFORE proposing a "
        "fresh Rule about an actor / area / camera / kind to check if a "
        "matching entry already exists and would be better refined than "
        "duplicated. All filters are optional — supply only what you have."
    )

    def __init__(
        self,
        *,
        rules_store: Any = None,
        policy_store: Any = None,
        preferences_store: Any = None,
        area_store: Any = None,
    ) -> None:
        self.rules_store = rules_store
        self.policy_store = policy_store
        self.preferences_store = preferences_store
        self.area_store = area_store

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "actor": {
                            "type": "string",
                            "description": "snake_case actor id, e.g. 'winston'",
                        },
                        "area": {
                            "type": "string",
                            "description": "snake_case area id, e.g. 'front_yard'",
                        },
                        "camera": {"type": "string", "description": "snake_case camera id"},
                        "kind": {
                            "type": "string",
                            "description": "detection kind, e.g. 'person' / 'dog'",
                        },
                        "text": {
                            "type": "string",
                            "description": "free-text substring to match against intent_text",
                        },
                    },
                    "required": [],
                },
            },
        }

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            actor = (args.get("actor") or "").lower().strip()
            area = (args.get("area") or "").lower().strip()
            camera = (args.get("camera") or "").lower().strip()
            text = (args.get("text") or "").lower().strip()

            matches: list[dict[str, Any]] = []

            # Rules — match by scope (area / camera) + intent_text contains
            if self.rules_store is not None:
                try:
                    for rule in self.rules_store.all_rules():
                        if area and area not in [a.lower() for a in rule.scope.areas]:
                            continue
                        if camera and camera not in [c.lower() for c in rule.scope.cameras]:
                            continue
                        intent = (rule.intent_text or "").lower()
                        if (text and text not in intent) or (
                            actor and actor not in intent and actor not in rule.name.lower()
                        ):
                            if actor or text:
                                continue
                        matches.append(
                            {
                                "guidance_id": rule.id,
                                "storage_class": "rule",
                                "name": rule.name,
                                "intent_text": rule.intent_text,
                                "scope": {
                                    "areas": list(rule.scope.areas),
                                    "cameras": list(rule.scope.cameras),
                                },
                            }
                        )
                except Exception as e:
                    logger.debug("tool.search.rules_failed", error=str(e))

            # Policies — descriptor matching
            if self.policy_store is not None:
                try:
                    for p in self.policy_store.all_policies():
                        desc = p.descriptor or {}
                        if actor and (desc.get("actor") or "").lower() != actor:
                            continue
                        if area and (desc.get("area") or "").lower() != area:
                            continue
                        if camera and (desc.get("camera") or "").lower() != camera:
                            continue
                        if text and text not in (desc.get("intent_text") or "").lower():
                            continue
                        is_sc = bool(desc.get("is_situational_context"))
                        matches.append(
                            {
                                "guidance_id": p.id,
                                "storage_class": (
                                    "situational_context"
                                    if is_sc
                                    else (
                                        "transient_intent"
                                        if p.kind == "transient_intent"
                                        else "dismissal_policy"
                                    )
                                ),
                                "name": p.name,
                                "intent_text": desc.get("intent_text") or "",
                                "scope": {
                                    k: v
                                    for k, v in desc.items()
                                    if isinstance(v, str) and k != "intent_text"
                                },
                            }
                        )
                except Exception as e:
                    logger.debug("tool.search.policies_failed", error=str(e))

            # Preferences — always one entry; match on text only
            if self.preferences_store is not None and text:
                try:
                    p = self.preferences_store.get()
                    if text in (p.what_i_care_about or "").lower():
                        matches.append(
                            {
                                "guidance_id": "preferences:singleton",
                                "storage_class": "preference",
                                "name": "What I care about",
                                "intent_text": p.what_i_care_about,
                                "scope": {},
                            }
                        )
                except Exception as e:
                    logger.debug("tool.search.prefs_failed", error=str(e))

            # Area postures
            if self.area_store is not None:
                try:
                    for a in self.area_store.all_areas():
                        if area and area != a.id.lower():
                            continue
                        if a.attention_mode == "normal" and not a.role:
                            continue
                        matches.append(
                            {
                                "guidance_id": f"area:{a.id}",
                                "storage_class": "area_posture",
                                "name": f"{a.name} posture",
                                "intent_text": (
                                    f"attention_mode={a.attention_mode}"
                                    + (f" role={a.role}" if a.role else "")
                                ),
                                "scope": {"area": a.id},
                            }
                        )
                except Exception as e:
                    logger.debug("tool.search.areas_failed", error=str(e))

            return {
                "count": len(matches),
                "matches": matches[:10],  # cap to keep token budget sane
                "truncated": len(matches) > 10,
            }
        except Exception as e:
            return {"error": f"search failed: {e}"}


# ─── get_known_actor ───────────────────────────────────────────────


@dataclass
class GetKnownActor:
    """Layer 4 (Identity): pull a labeled subject's record + access
    profile from the preprocessor.

    The dispatcher uses this to decide things like:
      - Should *"alert me about Bob"* propose a Rule, or note that Bob
        already has an access profile saying he's a household member
        who's *expected* in this area?
      - When the user refines a rule about an actor, does the
        existing KnownActor have a behavioral profile that the LLM
        should respect?

    Read-only access via ``PreprocessorClient.list_identity_subjects``
    — same code path the /identities page uses.
    """

    name: str = "get_known_actor"
    description: str = (
        "Look up a labeled identity (person / pet / vehicle) by name or id "
        "to read their access profile + behavioral profile + appearance "
        "count. Use this before placing guidance about a named actor to "
        "check if the system already knows them and what's already encoded."
    )

    def __init__(self, *, preprocessor_client: Any = None) -> None:
        self.preprocessor_client = preprocessor_client

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Display name or snake_case id "
                            "of the actor (e.g. 'Winston' or 'winston').",
                        },
                    },
                    "required": ["name"],
                },
            },
        }

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        name = (args.get("name") or "").strip()
        if not name:
            return {"error": "name required"}
        if self.preprocessor_client is None:
            return {"error": "preprocessor client not configured"}
        try:
            from kukiihome_ha_agent.web_ui.identities import (
                build_identity_subjects,
            )

            payload = await self.preprocessor_client.list_identity_subjects()
            subjects = build_identity_subjects(payload)
            needle = name.lower()
            match = next(
                (
                    s
                    for s in subjects
                    if s.display_name.lower() == needle or s.subject_id.lower() == needle
                ),
                None,
            )
            if match is None:
                return {
                    "known": False,
                    "hint": f"no enrolled identity named {name!r}; if the "
                    "user mentions them in placement, set "
                    "scope.actor_name to the display name and omit "
                    "scope.actor",
                }
            return {
                "known": True,
                "subject_id": match.subject_id,
                "display_name": match.display_name,
                "kind": match.kind,
                "species": match.species,
                "modalities": match.modalities,
                "appearances": match.appearances,
                # Access + behavioral profiles aren't surfaced through
                # /identity/subjects yet (Part X §35 deferred); when they
                # land here, the LLM reads them too.
                "access_profile": None,
                "behavioral_profile": None,
            }
        except Exception as e:
            logger.debug("tool.get_known_actor.failed", error=str(e))
            return {"error": f"lookup failed: {e}"}


# ─── Helpers ───────────────────────────────────────────────────────


def tools_from_boot(boot: Any) -> list[Tool]:
    """Construct the default tool set wired to the boot state. Returns
    an empty list when neither stores nor the preprocessor client are
    available (test environments, fresh installs)."""
    out: list[Tool] = []
    out.append(
        SearchExistingGuidance(
            rules_store=getattr(boot, "rules_store", None),
            policy_store=getattr(boot, "policy_store", None),
            preferences_store=getattr(boot, "preferences_store", None),
            area_store=getattr(boot, "area_store", None),
        )
    )
    pp = getattr(boot, "preprocessor_client", None)
    if pp is not None:
        out.append(GetKnownActor(preprocessor_client=pp))
    return out


def tool_specs_for_llm(tools: list[Tool]) -> list[dict[str, Any]]:
    """Flatten tool specs into the array shape the OpenAI Chat
    Completions API expects under ``tools=[…]``."""
    return [t.spec() for t in tools]


def resolve_tool_call(
    tools: list[Tool],
    name: str,
) -> Tool | None:
    return next((t for t in tools if t.name == name), None)


def safe_parse_tool_args(raw: str | dict | None) -> dict:
    """LLMs sometimes hand back tool call arguments as a JSON string,
    sometimes as a dict (the spec says string). Tolerate both."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        return {}
