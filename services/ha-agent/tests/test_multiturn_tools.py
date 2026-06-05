"""Multi-turn + tool-calling dispatcher (Task 53).

Tests:
  - history-in-prompt: recent turns + last_committed appear in the
    user prompt, refinement block renders, single-shot calls stay clean
  - tool-call loop: assistant returns tool_calls → execute → feed
    result back → final placement
  - tool fallbacks: unknown tool name, tool raising
  - refinement commit path: commit_guidance routes scope.refines_
    guidance_id through refine_guidance instead of creating fresh
"""

from __future__ import annotations

import json

import pytest
from kukiihome_ha_agent.commit_guidance import (
    GuidanceStores,
    commit_guidance,
)
from kukiihome_ha_agent.dispatcher import (
    CommittedEntrySummary,
    DispatcherContext,
    LLMDispatcherProvider,
    RecentTurn,
    _build_user_prompt,
)
from kukiihome_ha_agent.dispatcher_tools import (
    GetKnownActor,
    SearchExistingGuidance,
    safe_parse_tool_args,
    tool_specs_for_llm,
)
from kukiihome_ha_agent.policy_store import PolicyStore
from kukiihome_ha_agent.preferences_store import PreferencesStore
from kukiihome_ha_agent.provenance_store import (
    PlacementProposal,
    ProvenanceStore,
)
from kukiihome_ha_agent.rules_store import Rule, RulesStore


def _ctx(**kw):
    base: dict = dict(  # noqa: C408
        known_actor_names=["Winston", "Bob"],
        known_area_names=["Front yard", "Pool"],
        known_camera_names=["Pool Camera"],
    )
    base.update(kw)
    return DispatcherContext(**base)


# ─── history-in-prompt ────────────────────────────────────────────


def test_prompt_omits_history_block_when_no_turns():
    prompt = _build_user_prompt("test", ctx=_ctx())
    assert "Recent conversation" not in prompt


def test_prompt_includes_recent_turns():
    ctx = _ctx(
        recent_turns=[
            RecentTurn(role="user", text="alert me when Winston is alone"),
            RecentTurn(
                role="system",
                text="explicit fire + persistent → Rule",
                committed_guidance_id="rule_abc",
                storage_class="rule",
            ),
        ]
    )
    prompt = _build_user_prompt("only at night", ctx=ctx)
    assert "Recent conversation" in prompt
    assert "user: alert me when Winston is alone" in prompt
    assert "committed rule <rule_abc>" in prompt


def test_prompt_includes_last_committed_summary():
    ctx = _ctx(
        last_committed=CommittedEntrySummary(
            guidance_id="rule_abc",
            storage_class="rule",
            name="Winston alone in Front yard",
            intent_text="Winston in Front yard alone — alert critical.",
            scope={"actor": "winston", "area": "front_yard"},
        )
    )
    prompt = _build_user_prompt("only at night", ctx=ctx)
    assert "rule_abc" in prompt
    assert "Winston alone in Front yard" in prompt
    assert "refines_guidance_id" in prompt  # instruction surfaces


def test_prompt_has_user_utterance_after_history():
    ctx = _ctx(recent_turns=[RecentTurn(role="user", text="first")])
    prompt = _build_user_prompt("second", ctx=ctx)
    # History block precedes current utterance
    assert prompt.index("first") < prompt.index("second")


# ─── search_existing_guidance tool ────────────────────────────────


@pytest.fixture
def stores():
    r = RulesStore(path=None)
    p = PolicyStore(path=None)
    pr = PreferencesStore(path=None)
    yield r, p, pr
    r.close()
    p.close()
    pr.close()


@pytest.mark.asyncio
async def test_search_tool_finds_matching_rule(stores):
    r, p, pr = stores
    from kukiihome_ha_agent.rules_store import RuleScope

    r.create(
        Rule(
            id="",
            name="Winston alone in Front yard",
            mode="nl",
            intent_text="Winston in Front yard alone — alert critical.",
            scope=RuleScope(areas=["front_yard"]),
        )
    )
    tool = SearchExistingGuidance(rules_store=r, policy_store=p, preferences_store=pr)
    out = await tool.execute({"area": "front_yard", "actor": "winston"})
    assert out["count"] == 1
    assert out["matches"][0]["storage_class"] == "rule"


@pytest.mark.asyncio
async def test_search_tool_no_match_returns_empty(stores):
    r, p, pr = stores
    tool = SearchExistingGuidance(rules_store=r, policy_store=p, preferences_store=pr)
    out = await tool.execute({"area": "ghost_area"})
    assert out["count"] == 0
    assert out["matches"] == []


@pytest.mark.asyncio
async def test_search_tool_handles_all_stores_none():
    tool = SearchExistingGuidance()
    out = await tool.execute({"actor": "winston"})
    assert out["count"] == 0


@pytest.mark.asyncio
async def test_search_tool_caps_matches_at_ten(stores):
    r, p, pr = stores
    from kukiihome_ha_agent.rules_store import RuleScope

    for i in range(15):
        r.create(
            Rule(id="", name=f"R{i}", mode="nl", intent_text="x", scope=RuleScope(areas=["x_area"]))
        )
    tool = SearchExistingGuidance(rules_store=r, policy_store=p, preferences_store=pr)
    out = await tool.execute({"area": "x_area"})
    assert out["count"] == 15
    assert len(out["matches"]) == 10
    assert out["truncated"] is True


def test_search_tool_spec_has_required_shape():
    tool = SearchExistingGuidance()
    spec = tool.spec()
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "search_existing_guidance"
    assert "parameters" in spec["function"]


# ─── get_known_actor tool ────────────────────────────────────────


class _FakePreprocClient:
    def __init__(self, subjects):
        self._subjects = subjects

    async def list_identity_subjects(self):
        return {"subjects": self._subjects}


@pytest.mark.asyncio
async def test_get_known_actor_finds_subject_by_display_name():
    client = _FakePreprocClient(
        [
            {
                "subject_id": "winston",
                "kind": "pet",
                "display_name": "Winston",
                "species": "dog",
                "modalities": ["pet", "gait"],
                "appearances": 12,
            },
        ]
    )
    tool = GetKnownActor(preprocessor_client=client)
    out = await tool.execute({"name": "Winston"})
    assert out["known"] is True
    assert out["subject_id"] == "winston"
    assert out["kind"] == "pet"
    assert "pet" in out["modalities"]


@pytest.mark.asyncio
async def test_get_known_actor_returns_known_false_for_unknown():
    client = _FakePreprocClient([])
    tool = GetKnownActor(preprocessor_client=client)
    out = await tool.execute({"name": "Stranger"})
    assert out["known"] is False
    assert "hint" in out


@pytest.mark.asyncio
async def test_get_known_actor_handles_missing_preprocessor():
    tool = GetKnownActor(preprocessor_client=None)
    out = await tool.execute({"name": "Bob"})
    assert "error" in out


@pytest.mark.asyncio
async def test_get_known_actor_requires_name():
    tool = GetKnownActor(preprocessor_client=_FakePreprocClient([]))
    out = await tool.execute({})
    assert "error" in out


# ─── tool-call loop ──────────────────────────────────────────────


class _FakeChatClient:
    """Sequenced fake — each call returns the next pre-canned message dict."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.calls: list[dict] = []

    async def complete_chat(self, *, messages, tools=None, max_tokens=1500):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self.messages:
            raise RuntimeError("test ran out of canned responses")
        return self.messages.pop(0)


def _good_content():
    return json.dumps(
        {
            "storage_class": "rule",
            "name": "Winston front yard",
            "scope": {"area": "front_yard"},
            "lifecycle": "persistent",
            "fire_affordance": "alert",
            "intent_text": "alert when winston in front yard",
            "reasoning": "explicit fire + persistent → Rule.",
            "confidence": 0.92,
        }
    )


@pytest.mark.asyncio
async def test_tool_loop_no_tool_calls_returns_proposal():
    """When the model goes straight to a content response, no tool
    round-trip happens. This is the common path for simple utterances."""
    client = _FakeChatClient([{"content": _good_content()}])
    provider = LLMDispatcherProvider(client, tools=[])
    p = await provider.propose_async("alert me about Winston", ctx=_ctx())
    assert p.storage_class == "rule"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_tool_loop_executes_tool_then_returns_proposal(stores):
    """The model asks to search guidance, gets a result, then emits
    the proposal."""
    r, p, pr = stores
    tools = [SearchExistingGuidance(rules_store=r, policy_store=p, preferences_store=pr)]
    client = _FakeChatClient(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "search_existing_guidance",
                            "arguments": '{"actor": "winston"}',
                        },
                    }
                ],
            },
            {"content": _good_content()},
        ]
    )
    provider = LLMDispatcherProvider(client, tools=tools)
    proposal = await provider.propose_async("alert about Winston", ctx=_ctx())
    assert proposal.storage_class == "rule"
    # Second call's message history includes the tool result
    assert any(m.get("role") == "tool" for m in client.calls[1]["messages"])


@pytest.mark.asyncio
async def test_tool_loop_handles_unknown_tool_gracefully():
    """The model invents a tool name — we surface an error to the
    model in the next round."""
    client = _FakeChatClient(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "nonexistent_tool",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {"content": _good_content()},
        ]
    )
    provider = LLMDispatcherProvider(client, tools=[])
    p = await provider.propose_async("test", ctx=_ctx())
    assert p.storage_class == "rule"
    # Second call's tool result message carries the error
    tool_msgs = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "unknown tool" in tool_msgs[0]["content"]


@pytest.mark.asyncio
async def test_tool_loop_exhausts_rounds_and_raises():
    """A model that keeps calling tools forever should be capped."""
    forever_tool_response = {
        "content": "",
        "tool_calls": [
            {
                "id": "x",
                "function": {"name": "foo", "arguments": "{}"},
            }
        ],
    }
    client = _FakeChatClient([forever_tool_response] * 20)
    provider = LLMDispatcherProvider(client, tools=[], max_tool_rounds=2)
    with pytest.raises(RuntimeError, match="exhausted"):
        await provider.propose_async("test", ctx=_ctx())


@pytest.mark.asyncio
async def test_tool_loop_legacy_complete_client_still_works():
    """Backwards compat: a client without complete_chat is wrapped via
    its complete() method."""

    class _LegacyClient:
        def __init__(self):
            self.calls = []

        async def complete(self, *, system, user, max_tokens=800):
            self.calls.append((system, user))
            return _good_content()

    provider = LLMDispatcherProvider(_LegacyClient(), tools=[])
    p = await provider.propose_async("test", ctx=_ctx())
    assert p.storage_class == "rule"


def test_tool_specs_for_llm_flattens(stores):
    r, p, pr = stores
    tools = [SearchExistingGuidance(rules_store=r, policy_store=p, preferences_store=pr)]
    specs = tool_specs_for_llm(tools)
    assert len(specs) == 1
    assert specs[0]["function"]["name"] == "search_existing_guidance"


def test_safe_parse_tool_args_string():
    assert safe_parse_tool_args('{"x": 1}') == {"x": 1}


def test_safe_parse_tool_args_dict():
    assert safe_parse_tool_args({"x": 1}) == {"x": 1}


def test_safe_parse_tool_args_malformed_returns_empty():
    assert safe_parse_tool_args("not json") == {}
    assert safe_parse_tool_args(None) == {}
    assert safe_parse_tool_args(123) == {}


# ─── refinement commit path ──────────────────────────────────────


@pytest.fixture
def commit_stores():
    bundle = GuidanceStores(
        rules=RulesStore(path=None),
        preferences=PreferencesStore(path=None),
        policies=PolicyStore(path=None),
        provenance=ProvenanceStore(path=None),
    )
    yield bundle
    for s in (bundle.rules, bundle.preferences, bundle.policies, bundle.provenance):
        if s:
            s.close()


def test_commit_routes_refines_guidance_id_to_refine(commit_stores):
    """When scope.refines_guidance_id is set + matches an existing
    provenance row, the commit path updates in place rather than
    creating a fresh entry."""
    p_initial = PlacementProposal(
        storage_class="rule",
        name="Winston front yard",
        scope={"area": "front_yard"},
        lifecycle="persistent",
        fire_affordance="alert",
        severity="critical",
        intent_text="Winston in front yard - alert critical.",
        reasoning="initial placement",
        confidence=0.9,
    )
    gid = commit_guidance(
        p_initial,
        stores=commit_stores,
        origin="conversation",
        transcript_id="t0",
        user_utterance="initial",
    )

    # Now refine — same gid via scope.refines_guidance_id
    p_refined = PlacementProposal(
        storage_class="rule",
        name="Winston front yard",
        scope={"area": "front_yard", "refines_guidance_id": gid},
        lifecycle="persistent",
        fire_affordance="alert",
        severity="normal",  # dropped from critical
        intent_text="UPDATED — only at night",
        reasoning="refined scope",
        confidence=0.95,
    )
    gid2 = commit_guidance(
        p_refined,
        stores=commit_stores,
        origin="conversation",
        transcript_id="t1",
        user_utterance="only at night",
    )
    # Same id returned
    assert gid2 == gid
    # The rule was updated, not duplicated
    assert len(commit_stores.rules.all_rules()) == 1
    refreshed = commit_stores.rules.get(gid)
    assert "UPDATED" in refreshed.intent_text
    assert refreshed.severity_static == "normal"
    # Provenance carries the refinement
    prov = commit_stores.provenance.get_provenance(gid)
    assert "t1" in prov.refinement_transcript_ids
    # The refines_guidance_id was stripped from the stored scope
    assert "refines_guidance_id" not in p_refined.scope


def test_commit_with_unknown_refines_id_falls_through_to_fresh_create(commit_stores):
    """When the LLM cites a refines_guidance_id that doesn't exist,
    fall back to a fresh create instead of erroring — the dispatcher
    might have hallucinated the id."""
    p = PlacementProposal(
        storage_class="rule",
        name="Test",
        scope={"refines_guidance_id": "rule_ghost"},
        lifecycle="persistent",
        fire_affordance="alert",
        intent_text="test",
        reasoning="r",
        confidence=0.9,
    )
    gid = commit_guidance(p, stores=commit_stores, transcript_id="t")
    # Got a fresh id, not the ghost
    assert gid != "rule_ghost"
    assert commit_stores.rules.get(gid) is not None
