"""Dispatcher — utterance → PlacementProposal (Part X §35).

Defines the `DispatcherProvider` protocol that the drawer calls on each
user turn. Three implementations:

  - **HeuristicDispatcherProvider** — pattern-matches the utterance for
    the four most common shapes (rule / transient_intent / dismissal /
    preference). Always available; doubles as offline / fallback mode.
  - **LLMDispatcherProvider** — calls a text-mode LLM through a
    pluggable `LLMClient` protocol and validates its structured output
    against `PlacementProposal`'s schema. On parse / schema failure,
    retries once with the schema error in the prompt; second failure
    raises so the composite can fall back.
  - **CompositeDispatcherProvider** — try LLM first; on any exception
    fall back to heuristic. The drawer wires to this in production.

All three return `PlacementProposal` — the schema-validated payload
the drawer renders as a preview card and `commit_guidance` writes
through.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from kukiihome_ha_agent.provenance_store import (
    PlacementProposal,
    validate_proposal,
)

logger = structlog.get_logger(__name__)

# ─── Context for the dispatcher's read-only system state ───────────


@dataclass
class RecentTurn:
    """One transcript turn surfaced to the LLM for multi-turn context.
    Only the minimum needed for the LLM to recognize refinement-of-prior-
    placement — the full proposal_json from the original system turn lives
    in ProvenanceStore but we don't replay it; we summarize."""

    role: str  # 'user' | 'system'
    text: str  # utterance text (system role = reasoning)
    committed_guidance_id: str = ""  # set on system turns that resulted in a commit
    storage_class: str = ""  # set when committed_guidance_id is set


@dataclass
class CommittedEntrySummary:
    """Compact summary of the most-recent guidance entry committed in the
    same session, so the LLM can reason about refining it on the next
    turn instead of authoring a fresh one."""

    guidance_id: str
    storage_class: str
    name: str
    intent_text: str
    scope: dict[str, str] = field(default_factory=dict)


@dataclass
class DispatcherContext:
    """The slice of system state the dispatcher can read to inform its
    placement. The route handler assembles this once per call from boot.

    Heuristic provider uses it for entity resolution (actor + area name
    matching). LLM provider uses it as both prompt context and the
    backing state for tool calls (Part X §35; memory-layer tools span
    Layers 4 + 5 per Part IX §26)."""

    known_actor_names: list[str]
    known_area_names: list[str]
    known_camera_names: list[str]
    # Page context (drawer was opened from /alert/X → carry the alert)
    page_context: str = ""
    alert_context: str = ""
    # Multi-turn (Part X §38) — recent conversation history + the last
    # entry committed in this session. When non-empty, the LLM is told
    # to consider refining the prior entry instead of authoring fresh.
    recent_turns: list[RecentTurn] = field(default_factory=list)
    last_committed: CommittedEntrySummary | None = None


class DispatcherProvider(Protocol):
    """Plug point. Swap the implementation without changing route code."""

    def propose(
        self,
        utterance: str,
        *,
        ctx: DispatcherContext,
    ) -> PlacementProposal: ...


# ─── Heuristic implementation ─────────────────────────────────────


_RULE_PATTERNS = (
    re.compile(r"\b(notify|alert|tell|let me know|ping)\b", re.IGNORECASE),
    re.compile(r"\bwhen\b", re.IGNORECASE),
)

_DISMISSAL_PATTERNS = (
    re.compile(r"\b(don'?t|ignore|suppress|stop|hide)\b.+(alert|notif|fire)", re.IGNORECASE),
    re.compile(r"\b(boring|noise|false positive)\b", re.IGNORECASE),
)

_PREFERENCE_PATTERNS = (
    re.compile(r"\bi care about\b", re.IGNORECASE),
    re.compile(r"\b(is our|are our) (dog|cat|pet|family|kid|child)\b", re.IGNORECASE),
    re.compile(r"\b(vigilance|sensitivity)\b", re.IGNORECASE),
)

_TEMPORAL_PATTERNS = (
    re.compile(r"\b(tonight|today|tomorrow|this (afternoon|evening|week))\b", re.IGNORECASE),
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
        self,
        utterance: str,
        *,
        ctx: DispatcherContext,
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
                    "Explicit 'notify / alert / tell me' + persistent → Rule. Scope from entities."
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


def context_from_boot(
    boot: Any,
    *,
    session_id: str = "",
    history_window: int = 8,
) -> DispatcherContext:
    """Pluck names from boot state for the dispatcher. None-safe — empty
    lists when stores aren't wired.

    ``session_id`` (Part X §38): when provided, the most recent
    ``history_window`` turns from that session + the most recent
    committed guidance entry are folded into the context for multi-turn
    placement reasoning. Pass empty string for fire-and-forget calls
    that should be treated as fresh."""
    actor_names: list[str] = []
    area_names: list[str] = []
    camera_names: list[str] = []

    # Areas — friendly names.
    areas = boot.area_store.all_areas() if getattr(boot, "area_store", None) else []
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

    # Multi-turn history (Part X §38). Pull both turns + the last
    # committed entry summary so the LLM can recognize refinement.
    recent_turns: list[RecentTurn] = []
    last_committed: CommittedEntrySummary | None = None
    prov = getattr(boot, "provenance_store", None)
    if session_id and prov is not None:
        try:
            all_turns = prov.turns_for_session(session_id)
            for t in all_turns[-history_window:]:
                if t.role == "user":
                    recent_turns.append(
                        RecentTurn(
                            role="user",
                            text=t.utterance,
                        )
                    )
                elif t.committed_to:
                    # committed_to is the guidance_id; classify by id
                    # shape so the history line is informative for the
                    # LLM. Original code used startswith("rule"/"policy")
                    # which never matched because rule ids are bare
                    # slugs (winston_alone_front, not rule_xxx) and
                    # policy ids are pol_xxxxxxxx (not policy_xxx).
                    recent_turns.append(
                        RecentTurn(
                            role="system",
                            text=t.utterance,
                            committed_guidance_id=t.committed_to,
                            storage_class=_classify_guidance_id(t.committed_to),
                        )
                    )
                else:
                    # System turn carrying a proposal but never confirmed.
                    recent_turns.append(
                        RecentTurn(
                            role="system",
                            text=t.utterance,
                        )
                    )

            # Find the most recent committed system turn → resolve
            # the full entry summary.
            for t in reversed(all_turns):
                if t.role != "system" or not t.committed_to:
                    continue
                last_committed = _resolve_committed_summary(
                    boot,
                    t.committed_to,
                )
                break
        except Exception as e:
            logger.debug("dispatcher.context.history_failed", error=str(e))

    return DispatcherContext(
        known_actor_names=actor_names,
        known_area_names=area_names,
        known_camera_names=camera_names,
        recent_turns=recent_turns,
        last_committed=last_committed,
    )


def _classify_guidance_id(guidance_id: str) -> str:
    """Cheap shape-based classifier for a guidance_id. Used to label
    history turns + the refinement guard in commit_guidance. NOT for
    routing the write — the routers use proposal.storage_class.

    Conventions baked into the stores:
      - preferences:singleton / preferences:vigilance / ...
      - area:<area_id>
      - pol_<uuid8>   (PolicyStore — covers dismissal + transient_intent
                       + situational_context)
      - everything else → rule (RulesStore uses bare slugs from name)
    """
    if not guidance_id:
        return ""
    if guidance_id.startswith("preferences:"):
        return "preference"
    if guidance_id.startswith("area:"):
        return "area_posture"
    if guidance_id.startswith("pol_"):
        return "policy"
    return "rule"


def _resolve_committed_summary(
    boot: Any,
    guidance_id: str,
) -> CommittedEntrySummary | None:
    """Best-effort resolve guidance_id → CommittedEntrySummary by
    poking the per-class stores. Returns None on any failure so the
    dispatcher's last_committed remains None and the refinement
    block is skipped."""
    try:
        if guidance_id.startswith("preferences:"):
            ps = getattr(boot, "preferences_store", None)
            if ps is None:
                return None
            prefs = ps.get()
            return CommittedEntrySummary(
                guidance_id=guidance_id,
                storage_class="preference",
                name="What I care about" if "what_i_care" in guidance_id else "Preferences",
                intent_text=prefs.what_i_care_about,
                scope={},
            )
        if guidance_id.startswith("area:"):
            ars = getattr(boot, "area_store", None)
            if ars is None:
                return None
            area = ars.get(guidance_id.split(":", 1)[1])
            if area is None:
                return None
            return CommittedEntrySummary(
                guidance_id=guidance_id,
                storage_class="area_posture",
                name=f"{area.name} posture",
                intent_text=f"attention_mode={area.attention_mode}",
                scope={"area": area.id},
            )
        # Rule or policy — try rules first, then policies.
        rs = getattr(boot, "rules_store", None)
        if rs is not None:
            rule = rs.get(guidance_id)
            if rule is not None:
                return CommittedEntrySummary(
                    guidance_id=guidance_id,
                    storage_class="rule",
                    name=rule.name,
                    intent_text=rule.intent_text,
                    scope={
                        "areas": ",".join(rule.scope.areas),
                        "cameras": ",".join(rule.scope.cameras),
                    },
                )
        pols = getattr(boot, "policy_store", None)
        if pols is not None:
            pol = pols.get(guidance_id)
            if pol is not None:
                desc = pol.descriptor or {}
                is_sc = bool(desc.get("is_situational_context"))
                return CommittedEntrySummary(
                    guidance_id=guidance_id,
                    storage_class=(
                        "situational_context"
                        if is_sc
                        else (
                            "transient_intent"
                            if pol.kind == "transient_intent"
                            else "dismissal_policy"
                        )
                    ),
                    name=pol.name,
                    intent_text=desc.get("intent_text") or "",
                    scope={
                        k: v for k, v in desc.items() if isinstance(v, str) and k != "intent_text"
                    },
                )
    except Exception:
        return None
    return None


# ─── LLM-backed implementation ─────────────────────────────────────


# Pulled out as a module constant so tests can spy on / replace it.
DISPATCHER_SYSTEM_PROMPT = """\
You are the dispatcher for Kukii-Home — a local-first home AI agent.
Your job: take a user's plain-English utterance about what they want
the system to watch for, and place it on the right storage class so the
reasoner reads it correctly later.

The cube has three axes:
  - Scope: global / area / camera / actor / kind / pattern
  - Lifecycle: persistent / temporal / fire_once
  - Fire affordance: alert / shift_prior / dismiss / metadata

Storage classes you may pick:
  - rule              : persistent + explicit alert. Scoped.
  - preference        : global + persistent + soft prior shift.
  - transient_intent  : temporal + explicit alert. Self-prunes.
  - dismissal_policy  : persistent + dismiss. Suppresses pattern.
  - situational_context: temporal + soft prior shift. Context window.
  - area_posture      : metadata-only change on an existing Area.
  - access_profile    : per-actor expected pattern (defer for v1).

Return STRICT JSON matching this schema (and nothing else):

{
  "storage_class": "rule" | "preference" | ...,
  "name": "<short user-facing name>",
  "scope": {"actor"?, "area"?, "camera"?, "kind"?, "pattern"?,
            "actor_name"?, "area_name"?, "camera_name"?,
            "vigilance"?, "attention_mode"?, "role"?},
  "lifecycle": "persistent" | "temporal" | "fire_once",
  "lifecycle_ttl_iso": "<ISO-8601>" | null,   // required for temporal
  "fire_affordance": "alert" | "shift_prior" | "dismiss" | "metadata",
  "severity": "low" | "normal" | "critical" | null,
  "intent_text": "<the prose the VLM will read at eval time>",
  "reasoning": "<ONE sentence explaining why this storage class>",
  "confidence": 0.0-1.0,
  "clarifying_questions": ["<question>", ...]   // empty when confident
}

Rules of thumb:
  - 'notify / alert / tell me' + persistent → rule
  - 'don't / ignore / suppress' → dismissal_policy
  - 'tonight / today / for the next' + alert → transient_intent
  - household statements ('Winston is our dog') → preference
  - When you can't tell lifecycle OR fire affordance, return
    confidence < 0.7 and one or two clarifying_questions targeting
    those axes.

**Tools — call them BEFORE proposing to avoid duplicates + respect
existing context:**
  - search_existing_guidance(actor?, area?, camera?, kind?, text?)
    Use this whenever the utterance scopes to a specific actor /
    area / camera / kind to check if a matching rule or policy already
    exists. If it does, prefer refining it (see refinement block
    below) over creating a duplicate.
  - get_known_actor(name)
    Use this when the utterance names an actor (person / pet /
    vehicle). Tells you whether the system already recognizes them
    + reads the access_profile + behavioral_profile if set. Don't
    propose access-profile changes without checking.

**Refinement semantics — when the user is iterating on a prior
placement:**
  If the Recent conversation block shows a "Most recently committed
  guidance entry" AND the user's current utterance reads as a
  modification of THAT entry (narrows scope, adjusts severity, adds
  a condition, changes the lifecycle, etc.) — set
  scope.refines_guidance_id to that entry's id and emit the REFINED
  values for the other fields. The dispatcher will route this as an
  update instead of a fresh create.

  Don't refine across storage classes — if the user's iteration
  actually converts the entry to a different class (e.g., from rule
  to preference), propose a fresh entry of the new class and leave
  refines_guidance_id unset.

Use ONLY entities present in the provided known_actor_names /
known_area_names / known_camera_names lists. If the utterance mentions
something not in those lists, place it in intent_text but DO NOT
invent IDs.

**scope field format — strict:**
  - scope.actor / scope.area / scope.camera / scope.kind: ALWAYS a
    snake_case string id derived from the matching known name —
    `"winston"`, `"front_yard"`, `"pool_camera"`. Never a boolean,
    never the display-cased name, never an empty string, never null.
    Omit the key entirely if the axis doesn't apply.
  - scope.actor_name / scope.area_name / scope.camera_name: the
    canonical DISPLAY name verbatim from the known list — `"Winston"`,
    `"Front yard"`, `"Pool Camera"`. Always paired with the id field.
  - scope.pattern: free-form descriptor like `"alone"` or `"after_dusk"`.

Example for "Tell me when Winston is alone in the front yard":
  "scope": {
    "actor": "winston", "actor_name": "Winston",
    "area":  "front_yard", "area_name":  "Front yard",
    "pattern": "alone"
  }
"""


class LLMClient(Protocol):
    """Minimal text-mode client. The drawer's LLM provider only needs
    one method: send a system + user prompt, get back text. Implementations
    can wrap whatever HTTP client the boot configures (Anthropic / Gemini /
    Ollama / etc.); tests inject a deterministic fake."""

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 800,
    ) -> str: ...


@dataclass
class _PromptBundle:
    system: str
    user: str


def _build_user_prompt(
    utterance: str,
    *,
    ctx: DispatcherContext,
    retry_note: str = "",
) -> str:
    """Compose the user-side prompt. Includes:
    - system state (known actor/area/camera names + page/alert context)
    - recent conversation history (last N turns from this session) +
      the most recent committed guidance entry, so the LLM can
      recognize refinement-of-prior-placement utterances (Part X §38)
    - the user's current utterance
    - optional retry note when the schema retry path is active
    """
    facts = {
        "known_actor_names": ctx.known_actor_names,
        "known_area_names": ctx.known_area_names,
        "known_camera_names": ctx.known_camera_names,
        "page_context": ctx.page_context,
        "alert_context": ctx.alert_context,
    }
    parts = [
        "## System state",
        json.dumps(facts, indent=2),
    ]

    # Multi-turn block (Part X §38). Only render when there's actually
    # prior history — keeps single-shot calls clean + cheap.
    if ctx.recent_turns or ctx.last_committed:
        parts += ["", "## Recent conversation in this session"]
        if ctx.last_committed:
            parts.append(
                "Most recently committed guidance entry:\n"
                + json.dumps(
                    {
                        "guidance_id": ctx.last_committed.guidance_id,
                        "storage_class": ctx.last_committed.storage_class,
                        "name": ctx.last_committed.name,
                        "intent_text": ctx.last_committed.intent_text,
                        "scope": ctx.last_committed.scope,
                    },
                    indent=2,
                )
            )
        if ctx.recent_turns:
            history = []
            for t in ctx.recent_turns:
                if t.role == "user":
                    history.append(f"  user: {t.text}")
                elif t.committed_guidance_id:
                    history.append(
                        f"  system: committed {t.storage_class} "
                        f"<{t.committed_guidance_id}>: {t.text}"
                    )
                else:
                    history.append(f"  system: proposed {t.text}")
            parts += ["", "Turn history (oldest → newest):", *history]
        parts.append(
            "\nIf the user's current utterance refines the most recent "
            "committed entry (e.g., narrows its scope, changes its "
            "severity, adds a condition), set "
            "scope.refines_guidance_id to that entry's id and emit the "
            "refined values for the OTHER fields. Otherwise place as a "
            "new entry."
        )

    parts += [
        "",
        "## User utterance",
        utterance.strip(),
    ]
    if retry_note:
        parts += [
            "",
            "## Retry — previous attempt failed schema validation",
            retry_note,
            "Return ONLY the corrected JSON.",
        ]
    return "\n".join(parts)


def _strip_code_fence(text: str) -> str:
    """LLMs love wrapping JSON in ```json ... ``` blocks. Tolerate it."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


class LLMDispatcherProvider:
    """LLM-backed placement with multi-turn tool-call support.

    Flow per propose_async call:

      1. Seed messages with [system, user(prompt + recent_turns +
         last_committed)]
      2. Loop up to ``max_tool_rounds`` times:
         - Call client.complete_chat(messages, tools)
         - If response carries tool_calls: execute each tool, append
           the tool-result messages, continue
         - Else: try to parse + validate response.content as JSON
           proposal; retry once on schema failure with error appended
         - On second schema failure: raise so Composite falls back
      3. If we exhaust tool rounds without ever getting a final
         content response, raise

    Tool failures inside the loop don't abort — the tool returns an
    ``{"error": ...}`` dict and the LLM can decide what to do with it.
    """

    def __init__(
        self,
        client: LLMClient,
        tools: list[Any] | None = None,
        max_tool_rounds: int = 5,
    ) -> None:
        self.client = client
        self.tools = tools or []
        self.max_tool_rounds = max_tool_rounds

    async def propose_async(
        self,
        utterance: str,
        *,
        ctx: DispatcherContext,
    ) -> PlacementProposal:
        from kukiihome_ha_agent.dispatcher_tools import (
            resolve_tool_call,
            safe_parse_tool_args,
            tool_specs_for_llm,
        )

        user_prompt = _build_user_prompt(utterance, ctx=ctx)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": DISPATCHER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        tool_specs = tool_specs_for_llm(self.tools) if self.tools else None

        # Cap total LLM calls per propose: tool rounds + 2 schema retries
        # for the final content response. Keeps a bad model from hanging
        # the drawer indefinitely.
        rounds_remaining = self.max_tool_rounds + 2
        schema_retry_used = False
        while rounds_remaining > 0:
            rounds_remaining -= 1
            try:
                message = await self._call_chat(messages, tool_specs)
            except Exception as e:
                logger.warning(
                    "dispatcher.llm_call_failed",
                    rounds_remaining=rounds_remaining,
                    error=str(e),
                )
                raise

            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                # Append the assistant message verbatim (including
                # tool_calls) so the model sees its own request when
                # we feed the result back.
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                )
                for call in tool_calls:
                    fn = call.get("function") or {}
                    name = fn.get("name", "")
                    args = safe_parse_tool_args(fn.get("arguments"))
                    tool = resolve_tool_call(self.tools, name)
                    if tool is None:
                        result = {"error": f"unknown tool: {name}"}
                    else:
                        try:
                            result = await tool.execute(args)
                        except Exception as e:
                            logger.warning(
                                "dispatcher.tool.raised",
                                tool=name,
                                error=str(e),
                            )
                            result = {"error": f"tool raised: {e}"}
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": json.dumps(result),
                        }
                    )
                continue

            # No tool_calls → the model produced a final content blob.
            content = message.get("content") or ""
            try:
                data = json.loads(_strip_code_fence(content))
            except json.JSONDecodeError as e:
                if schema_retry_used:
                    raise ValueError(
                        f"LLM returned non-JSON after retry: {e}",
                    ) from e
                schema_retry_used = True
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"That response wasn't valid JSON ({e}). "
                            "Return ONLY a JSON object matching the schema."
                        ),
                    }
                )
                continue

            try:
                return validate_proposal(data)
            except ValueError as e:
                if schema_retry_used:
                    raise
                schema_retry_used = True
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"That response failed schema validation: {e}. "
                            "Re-emit the JSON with the field corrected."
                        ),
                    }
                )
                continue

        raise RuntimeError(
            "dispatcher: exhausted tool rounds without a final proposal",
        )

    async def _call_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Call the client's chat method. Supports both the new
        ``complete_chat(messages, tools)`` interface (OpenAIChatClient)
        and the legacy ``complete(system, user)`` interface (test fakes
        without tool support) — when the legacy path is used, tools are
        silently ignored."""
        if hasattr(self.client, "complete_chat"):
            return await self.client.complete_chat(
                messages=messages,
                tools=tools,
            )
        # Legacy path — concat assistant/tool messages into a single
        # user-style prompt and use the simpler complete() method.
        system = ""
        user_parts: list[str] = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "") or system
            else:
                user_parts.append(f"[{m.get('role')}] {m.get('content', '')}")
        raw = await self.client.complete(
            system=system,
            user="\n".join(user_parts),
        )
        return {"content": raw, "tool_calls": []}

    def propose(
        self,
        utterance: str,
        *,
        ctx: DispatcherContext,
    ) -> PlacementProposal:
        """Sync wrapper for callers that aren't async. Runs the LLM call
        in a fresh event loop — only safe outside an active loop. The
        drawer's POST handler awaits ``propose_async`` directly."""
        import asyncio

        return asyncio.run(self.propose_async(utterance, ctx=ctx))


# ─── Composite — try LLM, fall back to heuristic ──────────────────


class CompositeDispatcherProvider:
    """Wired in production. Tries the LLM provider; on any exception
    (network, schema, etc.) falls back to the heuristic so the drawer
    never deadends. Both providers return PlacementProposal — the
    fallback is invisible to the caller except for the lower
    confidence + the reasoning hint."""

    def __init__(
        self,
        *,
        llm: LLMDispatcherProvider | None,
        heuristic: HeuristicDispatcherProvider | None = None,
        health: Any | None = None,
    ) -> None:
        self.llm = llm
        self.heuristic = heuristic or HeuristicDispatcherProvider()
        # Optional LLMHealthTracker the /memory page reads to render
        # the degraded-mode banner. None-safe: when not provided, the
        # composite still falls back silently as before.
        self.health = health

    async def propose_async(
        self,
        utterance: str,
        *,
        ctx: DispatcherContext,
    ) -> PlacementProposal:
        if self.llm is not None:
            try:
                proposal = await self.llm.propose_async(utterance, ctx=ctx)
                if self.health is not None:
                    self.health.record_success()
                return proposal
            except Exception as e:
                logger.warning(
                    "dispatcher.composite.fallback_to_heuristic",
                    error=str(e),
                )
                if self.health is not None:
                    self.health.record_failure(str(e))
        proposal = self.heuristic.propose(utterance, ctx=ctx)
        # Tag the fallback in reasoning so the audit row makes it visible.
        if self.llm is not None:
            proposal.reasoning = f"(LLM unavailable; heuristic placement.) {proposal.reasoning}"
        return proposal

    def propose(
        self,
        utterance: str,
        *,
        ctx: DispatcherContext,
    ) -> PlacementProposal:
        import asyncio

        return asyncio.run(self.propose_async(utterance, ctx=ctx))
