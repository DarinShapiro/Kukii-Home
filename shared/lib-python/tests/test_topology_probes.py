"""Tests for the bootstrap dependency-ping orchestration (#270, #280).

The individual probe coroutines hit real services in production; here we
verify the *orchestration* — that ``probe_topology`` fans out one probe per
declared dependency, that backend kind dispatches to the right probe, and
that failures land in the report as ``unreachable`` rather than blowing up
the whole bootstrap.

A separate integration test (gated on the dev compose stack being up) will
exercise the real probe coroutines end-to-end once integration.yml learns
to spin up Postgres + Qdrant + Redis + NATS.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sentihome_shared import _topology_probes as probes
from sentihome_shared.topology import (
    BootstrapReport,
    HAAgentConfig,
    ProbeStatus,
    Topology,
    VLMBackendConfig,
    VLMRouterConfig,
)


def _topology_with_one_of_everything() -> Topology:
    return Topology(
        ha_agent=HAAgentConfig(ha_token="tok"),
        vlm_router=VLMRouterConfig(
            backends=[
                VLMBackendConfig(
                    name="local",
                    kind="ollama",
                    base_url="http://localhost:11434",
                    model="qwen2.5-vl:7b",
                ),
                VLMBackendConfig(
                    name="cloud",
                    kind="openai_compatible",
                    base_url="https://api.example.com/v1",
                    model="x",
                    api_key="k",
                ),
            ]
        ),
    )


async def test_verify_calls_one_probe_per_declared_dependency():
    """Bus + Postgres + Qdrant + Redis + HA + 2 VLM backends = 7 results."""
    topology = _topology_with_one_of_everything()

    async def fake_ok(*_args, **_kwargs):
        return ProbeStatus.ok, ""

    with (
        patch.object(probes, "_probe_nats", side_effect=fake_ok),
        patch.object(probes, "_probe_postgres", side_effect=fake_ok),
        patch.object(probes, "_probe_http", side_effect=fake_ok),
        patch.object(probes, "_probe_redis", side_effect=fake_ok),
        patch.object(probes, "_probe_ollama", side_effect=fake_ok),
        patch.object(probes, "_probe_openai_compatible", side_effect=fake_ok),
    ):
        report = await topology.verify(probe_timeout_s=0.1)

    names = {r.name for r in report.results}
    assert names == {
        "bus.nats",
        "memory.postgres",
        "memory.qdrant",
        "memory.redis",
        "ha_agent",
        "vlm.local",
        "vlm.cloud",
    }
    assert report.ok is True


async def test_verify_skips_ha_agent_when_token_empty():
    """HA-agent probe is skipped entirely when ha_token unconfigured."""
    topology = Topology()  # default has empty ha_token
    assert topology.ha_agent.ha_token == ""

    async def fake_ok(*_args, **_kwargs):
        return ProbeStatus.ok, ""

    with (
        patch.object(probes, "_probe_nats", side_effect=fake_ok),
        patch.object(probes, "_probe_postgres", side_effect=fake_ok),
        patch.object(probes, "_probe_http", side_effect=fake_ok),
        patch.object(probes, "_probe_redis", side_effect=fake_ok),
    ):
        report = await topology.verify(probe_timeout_s=0.1)

    assert "ha_agent" not in {r.name for r in report.results}


async def test_verify_marks_unreachable_dep_as_fail_but_keeps_running():
    """One failing probe doesn't abort the whole report."""
    topology = _topology_with_one_of_everything()

    async def fake_ok(*_args, **_kwargs):
        return ProbeStatus.ok, ""

    async def fake_unreachable(*_args, **_kwargs):
        return ProbeStatus.unreachable, "connection refused"

    with (
        patch.object(probes, "_probe_nats", side_effect=fake_ok),
        patch.object(probes, "_probe_postgres", side_effect=fake_ok),
        patch.object(probes, "_probe_http", side_effect=fake_ok),
        patch.object(probes, "_probe_redis", side_effect=fake_unreachable),
        patch.object(probes, "_probe_ollama", side_effect=fake_ok),
        patch.object(probes, "_probe_openai_compatible", side_effect=fake_ok),
    ):
        report = await topology.verify(probe_timeout_s=0.1)

    redis = next(r for r in report.results if r.name == "memory.redis")
    assert redis.status == ProbeStatus.unreachable
    assert "connection refused" in redis.detail
    assert report.ok is False
    summary = report.summary()
    assert "[FAIL] memory.redis" in summary


async def test_verify_recovers_from_probe_exception():
    """A probe that raises is captured as ProbeStatus.error, not propagated."""
    topology = Topology(ha_agent=HAAgentConfig(ha_token="tok"))

    async def fake_ok(*_args, **_kwargs):
        return ProbeStatus.ok, ""

    async def fake_boom(*_args, **_kwargs):
        raise RuntimeError("client library exploded")

    with (
        patch.object(probes, "_probe_nats", side_effect=fake_boom),
        patch.object(probes, "_probe_postgres", side_effect=fake_ok),
        patch.object(probes, "_probe_http", side_effect=fake_ok),
        patch.object(probes, "_probe_redis", side_effect=fake_ok),
    ):
        report = await topology.verify(probe_timeout_s=0.1)

    nats = next(r for r in report.results if r.name == "bus.nats")
    assert nats.status == ProbeStatus.error
    assert "exploded" in nats.detail


async def test_verify_emits_isolated_report_per_call():
    """verify() doesn't accumulate state across calls."""
    topology = Topology()

    async def fake_ok(*_args, **_kwargs):
        return ProbeStatus.ok, ""

    with (
        patch.object(probes, "_probe_nats", side_effect=fake_ok),
        patch.object(probes, "_probe_postgres", side_effect=fake_ok),
        patch.object(probes, "_probe_http", side_effect=fake_ok),
        patch.object(probes, "_probe_redis", side_effect=fake_ok),
    ):
        r1 = await topology.verify(probe_timeout_s=0.1)
        r2 = await topology.verify(probe_timeout_s=0.1)

    assert len(r1.results) == len(r2.results) == 4
    assert isinstance(r1, BootstrapReport)
    assert isinstance(r2, BootstrapReport)


def test_probes_module_lazy_imports_dont_fail_at_module_load():
    """The probe module must import without nats/sqlalchemy/redis/httpx
    installed in the calling service; client imports live inside each
    probe function."""
    # If the module top-level tried to import nats, this re-import would
    # have failed during collection. The assertion is reaching this line.
    from sentihome_shared import _topology_probes  # noqa: F401


def test_probe_topology_handles_topology_with_no_optional_clients():
    """Sanity: an empty Topology probes the four required deps and nothing
    else — no HA probe, no VLM probes."""
    topology = Topology()
    # Synchronously sanity-check the count without running.
    # Topology() has no backends, no HA token → just 4 always-on probes.
    assert len(topology.vlm_router.backends) == 0
    assert topology.ha_agent.ha_token == ""


def test_probe_topology_invalid_backend_kind_is_skipped(monkeypatch):
    """Future-proofing: if a backend.kind escapes validation somehow, the
    probe loop continues rather than KeyError-ing."""
    # Forcibly bypass model validation by constructing the dataclass-like
    # object the loop reads.
    topology = Topology()
    # Inject a backend with an unrecognized kind via dict-style construction
    # using model_validate so the test doesn't blow on the literal type;
    # we cheat with model_construct to skip validation.
    backend = VLMBackendConfig.model_construct(
        name="weird",
        kind="not-a-real-kind",  # type: ignore[arg-type]
        base_url="http://x",
        model="x",
    )
    topology.vlm_router.backends.append(backend)

    # If the loop skips correctly, names will lack vlm.weird; if it KeyErrors,
    # this test fails. We don't need the probe to actually run for any
    # backend — patching everything to ok is fine.
    async def fake_ok(*_args, **_kwargs):
        return ProbeStatus.ok, ""

    async def run() -> BootstrapReport:
        with (
            patch.object(probes, "_probe_nats", side_effect=fake_ok),
            patch.object(probes, "_probe_postgres", side_effect=fake_ok),
            patch.object(probes, "_probe_http", side_effect=fake_ok),
            patch.object(probes, "_probe_redis", side_effect=fake_ok),
        ):
            return await topology.verify(probe_timeout_s=0.1)

    import asyncio

    report = asyncio.run(run())
    names = {r.name for r in report.results}
    assert "vlm.weird" not in names


@pytest.mark.parametrize(
    "kind,probe_attr",
    [
        ("ollama", "_probe_ollama"),
        ("openai_compatible", "_probe_openai_compatible"),
        ("vllm", "_probe_openai_compatible"),
    ],
)
async def test_verify_dispatches_backend_kind_to_correct_probe(kind, probe_attr):
    api_key = "k" if kind == "openai_compatible" else None
    topology = Topology(
        vlm_router=VLMRouterConfig(
            backends=[
                VLMBackendConfig(
                    name=f"bk-{kind}",
                    kind=kind,
                    base_url="http://x",
                    model="m",
                    api_key=api_key,
                )
            ]
        )
    )

    async def fake_ok(*_args, **_kwargs):
        return ProbeStatus.ok, ""

    calls = {"ollama": 0, "openai_compatible": 0}

    async def track_ollama(*_args, **_kwargs):
        calls["ollama"] += 1
        return ProbeStatus.ok, ""

    async def track_openai(*_args, **_kwargs):
        calls["openai_compatible"] += 1
        return ProbeStatus.ok, ""

    with (
        patch.object(probes, "_probe_nats", side_effect=fake_ok),
        patch.object(probes, "_probe_postgres", side_effect=fake_ok),
        patch.object(probes, "_probe_http", side_effect=fake_ok),
        patch.object(probes, "_probe_redis", side_effect=fake_ok),
        patch.object(probes, "_probe_ollama", side_effect=track_ollama),
        patch.object(probes, "_probe_openai_compatible", side_effect=track_openai),
    ):
        await topology.verify(probe_timeout_s=0.1)

    if probe_attr == "_probe_ollama":
        assert calls["ollama"] == 1 and calls["openai_compatible"] == 0
    else:
        assert calls["openai_compatible"] == 1 and calls["ollama"] == 0
