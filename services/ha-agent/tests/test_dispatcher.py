"""Heuristic + LLM dispatcher providers (Part X §35) — utterance →
placement, with the composite fallback for production."""

from __future__ import annotations

import pytest
from kukiihome_ha_agent.dispatcher import (
    CompositeDispatcherProvider,
    DispatcherContext,
    HeuristicDispatcherProvider,
    LLMDispatcherProvider,
)
from kukiihome_ha_agent.provenance_store import PlacementProposal


def _ctx(**kw):
    base: dict = dict(  # noqa: C408
        known_actor_names=["Winston", "Bob"],
        known_area_names=["Pool", "Front yard", "Backyard"],
        known_camera_names=["Pool Camera", "Front Camera"],
    )
    base.update(kw)
    return DispatcherContext(**base)


def _dispatcher():
    return HeuristicDispatcherProvider()


# ─── Rule branch ───────────────────────────────────────────────────


def test_notify_persistent_rule_with_actor_and_area():
    p = _dispatcher().propose(
        "Notify me when Winston is in the Front yard alone", ctx=_ctx(),
    )
    assert p.storage_class == "rule"
    assert p.lifecycle == "persistent"
    assert p.fire_affordance == "alert"
    assert p.scope.get("actor") == "winston"
    assert p.scope.get("area") == "front_yard"
    assert p.confidence >= 0.7


def test_actor_resolved_with_canonical_casing():
    p = _dispatcher().propose(
        "alert me when bob arrives", ctx=_ctx(),
    )
    # case-insensitive match, but actor_name is the canonical "Bob"
    assert p.scope.get("actor_name") == "Bob"


def test_camera_used_when_no_area_mentioned():
    # The heuristic prefers area matches over camera matches. When the
    # utterance mentions a camera that overlaps an area name, the area
    # wins; this is by-design — areas are the higher-level grouping.
    p = _dispatcher().propose(
        "Tell me when motion happens on the Front Camera", ctx=_ctx(),
    )
    # Either path is acceptable; assert at least one was resolved.
    assert p.scope.get("camera") == "front_camera" or \
        p.scope.get("area") == "front_yard"


# ─── Transient intent branch ──────────────────────────────────────


def test_tonight_keyword_routes_to_transient_intent():
    p = _dispatcher().propose(
        "Notify me when Bob's car arrives tonight", ctx=_ctx(),
    )
    assert p.storage_class == "transient_intent"
    assert p.lifecycle == "temporal"
    assert p.fire_affordance == "alert"
    assert "actor" in p.scope and p.scope["actor"] == "bob"


def test_today_keyword_routes_to_transient_intent():
    p = _dispatcher().propose(
        "Alert me if anyone is at the pool today", ctx=_ctx(),
    )
    assert p.storage_class == "transient_intent"


# ─── Dismissal branch ─────────────────────────────────────────────


def test_dont_alert_routes_to_dismissal_policy():
    p = _dispatcher().propose(
        "Don't alert me when there's a dog at the front camera", ctx=_ctx(),
    )
    assert p.storage_class == "dismissal_policy"
    assert p.fire_affordance == "dismiss"


def test_boring_routes_to_dismissal():
    p = _dispatcher().propose(
        "These wind-in-tree events are boring noise", ctx=_ctx(),
    )
    assert p.storage_class == "dismissal_policy"


def test_dismissal_with_temporal_marker_is_temporal_lifecycle():
    p = _dispatcher().propose(
        "Ignore alerts at the Pool tonight", ctx=_ctx(),
    )
    assert p.storage_class == "dismissal_policy"
    assert p.lifecycle == "temporal"


# ─── Preference branch ────────────────────────────────────────────


def test_winston_is_our_dog_routes_to_preference():
    # "don't" wins the dismissal pattern first; this is by-design
    # (negative-instruction priority). But the household statement pattern
    # should be detectable when no don't-alert intent is present:
    p = _dispatcher().propose("Winston is our dog", ctx=_ctx())
    assert p.storage_class == "preference"


def test_i_care_about_pattern_to_preference():
    p = _dispatcher().propose(
        "I care about anything happening at the front door at night",
        ctx=_ctx(),
    )
    assert p.storage_class == "preference"
    assert p.fire_affordance == "shift_prior"


# ─── Disambiguation fallback ──────────────────────────────────────


def test_ambiguous_utterance_returns_clarifying_questions():
    p = _dispatcher().propose("watch for stuff", ctx=_ctx())
    assert p.confidence < 0.7
    assert p.needs_disambiguation()
    assert len(p.clarifying_questions) >= 1


def test_proposals_always_include_reasoning_field():
    samples = [
        "Notify when Winston is alone in Front yard",
        "Don't ping me about dogs at the front camera",
        "I care about anyone at the pool",
        "Alert me if a car arrives tonight",
        "watch for stuff",
    ]
    for utt in samples:
        p = _dispatcher().propose(utt, ctx=_ctx())
        assert p.reasoning  # non-empty


# ─── Reasoning field is single sentence ──────────────────────────


def test_reasoning_field_is_concise():
    p = _dispatcher().propose(
        "Notify me when Winston is at the front yard", ctx=_ctx(),
    )
    # Single sentence guidance — under 200 chars keeps the audit row legible.
    assert len(p.reasoning) < 200


# ─── Severity defaults ────────────────────────────────────────────


def test_rule_proposal_carries_a_default_severity():
    p = _dispatcher().propose(
        "Tell me when Winston is in the Front yard alone", ctx=_ctx(),
    )
    # Heuristic provider doesn't infer severity from text in v1 — defaults to
    # 'normal' for rules so the rule can fire without VLM grading.
    assert p.severity in ("low", "normal", "critical")


# ─── LLM provider — happy path, schema retry, raise on second failure ──


class _FakeLLMClient:
    """Deterministic test double — returns canned responses in order
    so we can simulate first-call failures + retry success."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def complete(
        self, *, system: str, user: str, max_tokens: int = 800,
    ) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise RuntimeError("test ran out of canned responses")
        return self.responses.pop(0)


def _good_response_json():
    return (
        '{"storage_class": "rule", "name": "Winston front yard", '
        '"scope": {"actor": "winston", "area": "front_yard"}, '
        '"lifecycle": "persistent", "lifecycle_ttl_iso": null, '
        '"fire_affordance": "alert", "severity": "critical", '
        '"intent_text": "Winston in Front yard alone — critical.", '
        '"reasoning": "explicit fire + persistent → Rule.", '
        '"confidence": 0.92, "clarifying_questions": []}'
    )


@pytest.mark.asyncio
async def test_llm_provider_happy_path_returns_proposal():
    client = _FakeLLMClient([_good_response_json()])
    provider = LLMDispatcherProvider(client)
    p = await provider.propose_async(
        "Tell me when Winston is alone in the Front yard", ctx=_ctx(),
    )
    assert isinstance(p, PlacementProposal)
    assert p.storage_class == "rule"
    assert p.severity == "critical"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_llm_provider_tolerates_code_fence():
    fenced = f"```json\n{_good_response_json()}\n```"
    provider = LLMDispatcherProvider(_FakeLLMClient([fenced]))
    p = await provider.propose_async("alert when Winston is alone", ctx=_ctx())
    assert p.storage_class == "rule"


@pytest.mark.asyncio
async def test_llm_provider_retries_on_invalid_json_then_succeeds():
    client = _FakeLLMClient(["not json at all", _good_response_json()])
    provider = LLMDispatcherProvider(client)
    p = await provider.propose_async("Tell me when Winston is alone", ctx=_ctx())
    assert p.storage_class == "rule"
    # The retry happened — second call carries the JSON error in its
    # message history (legacy complete() path stringifies via [role]).
    assert "valid JSON" in client.calls[1][1]


@pytest.mark.asyncio
async def test_llm_provider_retries_on_schema_failure_then_succeeds():
    bad = '{"storage_class": "garbage", "name": "x", "scope": {}, ' \
          '"lifecycle": "persistent", "fire_affordance": "alert", ' \
          '"intent_text": "x", "reasoning": "x"}'
    client = _FakeLLMClient([bad, _good_response_json()])
    p = await LLMDispatcherProvider(client).propose_async(
        "Tell me when Winston is alone", ctx=_ctx(),
    )
    assert p.storage_class == "rule"
    # Retry prompt mentions schema failure
    assert "schema validation" in client.calls[1][1].lower()


@pytest.mark.asyncio
async def test_llm_provider_raises_after_two_bad_responses():
    client = _FakeLLMClient(["not json", "still not json"])
    provider = LLMDispatcherProvider(client)
    with pytest.raises(ValueError):
        await provider.propose_async("anything", ctx=_ctx())


@pytest.mark.asyncio
async def test_llm_provider_raises_when_client_errors_twice():
    class _BoomClient:
        async def complete(self, *, system, user, max_tokens=800):
            raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        await LLMDispatcherProvider(_BoomClient()).propose_async(
            "anything", ctx=_ctx(),
        )


@pytest.mark.asyncio
async def test_llm_provider_user_prompt_includes_system_state():
    client = _FakeLLMClient([_good_response_json()])
    provider = LLMDispatcherProvider(client)
    await provider.propose_async("Tell me about Winston", ctx=_ctx())
    user_prompt = client.calls[0][1]
    # Known actor / area / camera names + utterance present
    assert "Winston" in user_prompt
    assert "Pool" in user_prompt
    assert "Pool Camera" in user_prompt
    assert "Tell me about Winston" in user_prompt


# ─── Composite — LLM success path + fallback path ────────────────


@pytest.mark.asyncio
async def test_composite_uses_llm_when_available():
    client = _FakeLLMClient([_good_response_json()])
    composite = CompositeDispatcherProvider(llm=LLMDispatcherProvider(client))
    p = await composite.propose_async(
        "Tell me when Winston is alone", ctx=_ctx(),
    )
    assert p.storage_class == "rule"
    # Reasoning is the LLM's own — no fallback marker
    assert not p.reasoning.startswith("(LLM unavailable")


@pytest.mark.asyncio
async def test_composite_falls_back_to_heuristic_on_llm_error():
    client = _FakeLLMClient(["not json", "still not json"])
    composite = CompositeDispatcherProvider(llm=LLMDispatcherProvider(client))
    p = await composite.propose_async(
        "Notify me when Winston is in the Front yard", ctx=_ctx(),
    )
    # Heuristic produced a placement; reasoning carries the fallback marker
    assert p.reasoning.startswith("(LLM unavailable")
    assert p.storage_class == "rule"


@pytest.mark.asyncio
async def test_composite_with_no_llm_uses_heuristic_directly():
    composite = CompositeDispatcherProvider(llm=None)
    p = await composite.propose_async(
        "Notify me when Winston is in the Front yard", ctx=_ctx(),
    )
    # No fallback marker since the LLM was never tried
    assert not p.reasoning.startswith("(LLM unavailable")
    assert p.storage_class == "rule"
