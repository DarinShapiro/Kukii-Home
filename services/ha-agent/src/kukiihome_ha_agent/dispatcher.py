"""Dispatcher — utterance → PlacementProposal (Part X §35).

Defines the `DispatcherProvider` protocol that the drawer calls on each
user turn. Two implementations:

  - **HeuristicDispatcherProvider** — pattern-matches the utterance for
    the four most common shapes (rule / transient_intent / dismissal /
    preference). Useful by itself until the LLM lands; will become the
    drift-detection fallback / offline mode.
  - **LLMDispatcherProvider** — lands in Task 46. Will replace the
    heuristic provider as the default while keeping it as a fallback
    when the VLM is unreachable.

Both return `PlacementProposal` (Part X §35) — the schema-validated
payload the drawer renders as a preview card and `commit_guidance`
writes through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from kukiihome_ha_agent.provenance_store import PlacementProposal

# ─── Context for the dispatcher's read-only system state ───────────


@dataclass
class DispatcherContext:
    """The slice of system state the dispatcher can read to inform its
    placement. The route handler assembles this once per call from boot.

    Heuristic provider uses it for entity resolution (actor + area name
    matching). LLM provider will use it as tool-call context."""

    known_actor_names: list[str]
    known_area_names: list[str]
    known_camera_names: list[str]
    # Page context (drawer was opened from /alert/X → carry the alert)
    page_context: str = ""
    alert_context: str = ""


class DispatcherProvider(Protocol):
    """Plug point. Swap the implementation without changing route code."""

    def propose(
        self, utterance: str, *, ctx: DispatcherContext,
    ) -> PlacementProposal: ...


# ─── Heuristic implementation ─────────────────────────────────────


_RULE_PATTERNS = (
    re.compile(r"\b(notify|alert|tell|let me know|ping)\b", re.IGNORECASE),
    re.compile(r"\bwhen\b", re.IGNORECASE),
)

_DISMISSAL_PATTERNS = (
    re.compile(r"\b(don'?t|ignore|suppress|stop|hide)\b.+(alert|notif|fire)",
                re.IGNORECASE),
    re.compile(r"\b(boring|noise|false positive)\b", re.IGNORECASE),
)

_PREFERENCE_PATTERNS = (
    re.compile(r"\bi care about\b", re.IGNORECASE),
    re.compile(r"\b(is our|are our) (dog|cat|pet|family|kid|child)\b",
                re.IGNORECASE),
    re.compile(r"\b(vigilance|sensitivity)\b", re.IGNORECASE),
)

_TEMPORAL_PATTERNS = (
    re.compile(r"\b(tonight|today|tomorrow|this (afternoon|evening|week))\b",
                re.IGNORECASE),
    re.compile(r"\bfor the next\b", re.IGNORECASE),
)


def _first_match(words: str, names: list[str]) -> str | None:
    """Case-insensitive whole-word match for the first known name found
    in the utterance, in declared name order."""
    lower = words.lower()
    for name in names:
        if not name:
            continue
        if re.search(rf"\b{re.escape(name.lower())}\b", lower):
            return name
    return None


class HeuristicDispatcherProvider:
    """Pattern-based placement. Always returns a valid PlacementProposal;
    low confidence + clarifying_questions when the placement is uncertain."""

    def propose(
        self, utterance: str, *, ctx: DispatcherContext,
    ) -> PlacementProposal:
        # Extract entities first — used in every storage class.
        actor = _first_match(utterance, ctx.known_actor_names)
        area = _first_match(utterance, ctx.known_area_names)
        camera = _first_match(utterance, ctx.known_camera_names)
        is_temporal = any(p.search(utterance) for p in _TEMPORAL_PATTERNS)

        scope: dict[str, str] = {}
        if actor:
            scope["actor"] = actor.lower()
            scope["actor_name"] = actor
        if area:
            scope["area"] = area.lower().replace(" ", "_")
            scope["area_name"] = area
        if camera and not area:
            scope["camera"] = camera.lower().replace(" ", "_")
            scope["camera_name"] = camera

        # Dismissal first — "don't alert on X" is unambiguous.
        if any(p.search(utterance) for p in _DISMISSAL_PATTERNS):
            return PlacementProposal(
                storage_class="dismissal_policy",
                name=_short_name(utterance, prefix="Dismiss"),
                scope=scope,
                lifecycle="temporal" if is_temporal else "persistent",
                lifecycle_ttl_iso=None,
                fire_affordance="dismiss",
                intent_text=utterance.strip(),
                reasoning=(
                    "'don't / ignore / suppress' → DismissalPolicy. "
                    "Scope inferred from entities mentioned."
                ),
                confidence=0.78,
            )

        # Preference patterns — "I care about" + "is our dog" etc.
        if any(p.search(utterance) for p in _PREFERENCE_PATTERNS):
            return PlacementProposal(
                storage_class="preference",
                name="What I care about",
                scope={},
                lifecycle="persistent",
                fire_affordance="shift_prior",
                intent_text=utterance.strip(),
                reasoning=(
                    "Global statement about household / what matters → "
                    "Preference. Folds into VLM baseline."
                ),
                confidence=0.74,
            )

        # Rule / transient_intent — fire-affordance present.
        if any(p.search(utterance) for p in _RULE_PATTERNS):
            if is_temporal:
                return PlacementProposal(
                    storage_class="transient_intent",
                    name=_short_name(utterance, prefix="Watch for"),
                    scope=scope,
                    lifecycle="temporal",
                    lifecycle_ttl_iso=None,  # caller defaults to end-of-day
                    fire_affordance="alert",
                    intent_text=utterance.strip(),
                    reasoning=(
                        "'tonight / today / for the next' + 'notify' → "
                        "TransientIntent. Self-prunes on TTL."
                    ),
                    confidence=0.72,
                )
            return PlacementProposal(
                storage_class="rule",
                name=_short_name(utterance, prefix="Rule"),
                scope=scope,
                lifecycle="persistent",
                fire_affordance="alert",
                severity="normal",
                intent_text=utterance.strip(),
                reasoning=(
                    "Explicit 'notify / alert / tell me' + persistent → "
                    "Rule. Scope from entities."
                ),
                confidence=0.7,
            )

        # Low-confidence fallback — likely a rule, but ask the two axes.
        return PlacementProposal(
            storage_class="rule",
            name=_short_name(utterance, prefix="Rule"),
            scope=scope,
            lifecycle="persistent",
            fire_affordance="alert",
            intent_text=utterance.strip(),
            reasoning="Uncertain placement — asking lifecycle + fire affordance.",
            confidence=0.45,
            clarifying_questions=[
                "Just for tonight, or always?",
                "Should it ping you, or just change how I judge things?",
            ],
        )


# ─── Helpers ──────────────────────────────────────────────────────


def _short_name(utterance: str, *, prefix: str = "") -> str:
    """Best-effort name from the utterance — first 6 words, title-cased.
    Used when the user doesn't provide an explicit name."""
    words = re.findall(r"\b\w+\b", utterance.strip())[:6]
    if not words:
        return prefix or "Untitled"
    short = " ".join(words)
    if prefix:
        return f"{prefix}: {short}"
    return short


def context_from_boot(boot: Any) -> DispatcherContext:
    """Pluck names from boot state for the dispatcher. None-safe — empty
    lists when stores aren't wired."""
    actor_names: list[str] = []
    area_names: list[str] = []
    camera_names: list[str] = []

    # Areas — friendly names.
    areas = (
        boot.area_store.all_areas() if getattr(boot, "area_store", None)
        else []
    )
    area_names = [a.name for a in areas if a.name]

    # Cameras — friendly names from HA loops + registry.
    for loop in getattr(boot, "ha_camera_loops", []) or []:
        name = getattr(loop, "friendly_name", "") or ""
        if name:
            camera_names.append(name)

    # Actors / pets — KnownActor names. v1 doesn't expose a list; the
    # preferences relationships dict + identity store carry names. For
    # the heuristic, pull the relationships keys.
    if getattr(boot, "preferences_store", None):
        try:
            prefs = boot.preferences_store.get()
            actor_names = list(prefs.relationships.keys())
        except Exception:
            actor_names = []

    return DispatcherContext(
        known_actor_names=actor_names,
        known_area_names=area_names,
        known_camera_names=camera_names,
    )
