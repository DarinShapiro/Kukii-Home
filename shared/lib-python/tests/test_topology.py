"""Tests for sentihome_shared.topology (Epic 8.5)."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
import yaml
from pydantic import ValidationError
from sentihome_shared.topology import (
    PROFILES,
    BootstrapReport,
    PrivacyTierMax,
    ProbeResult,
    ProbeStatus,
    Topology,
    apply_env_overrides,
    apply_profile,
    interpolate_env,
    load_topology,
)

# ─────────────────────────────────────────────────────────────────────
# Pydantic model defaults + validation
# ─────────────────────────────────────────────────────────────────────


def test_topology_defaults_validate():
    t = Topology()
    assert t.deployment.profile == "distributed"
    assert t.bus.nats_url.startswith("nats://")
    assert t.memory.postgres_url.startswith("postgresql+asyncpg://")
    assert t.vlm_router.backends == []
    assert t.adapters == []


def test_topology_rejects_unknown_top_level_field():
    with pytest.raises(ValidationError):
        Topology.model_validate({"deplyment": {"profile": "distributed"}})  # typo


def test_vlm_backend_openai_requires_api_key():
    with pytest.raises(ValidationError) as exc:
        Topology.model_validate(
            {
                "vlm_router": {
                    "backends": [
                        {
                            "name": "cloud",
                            "kind": "openai_compatible",
                            "base_url": "https://api.example.com/v1",
                            "model": "x",
                        }
                    ]
                }
            }
        )
    assert "api_key" in str(exc.value)


def test_vlm_backend_ollama_does_not_require_api_key():
    t = Topology.model_validate(
        {
            "vlm_router": {
                "backends": [
                    {
                        "name": "local",
                        "kind": "ollama",
                        "base_url": "http://localhost:11434",
                        "model": "qwen2.5-vl:7b",
                    }
                ]
            }
        }
    )
    assert t.vlm_router.backends[0].privacy_tier_max == PrivacyTierMax.local_only


# ─────────────────────────────────────────────────────────────────────
# Profile presets (#267)
# ─────────────────────────────────────────────────────────────────────


def test_profiles_registry_has_three_presets():
    assert set(PROFILES) == {"yellow_single_box", "yellow_plus_inference", "distributed"}


def test_apply_profile_layers_under_user_yaml():
    user = {"deployment": {"profile": "yellow_single_box"}, "memory": {"rules_top_k": 99}}
    merged = apply_profile(user)
    # Profile defaults filled in
    assert merged["bus"]["nats_url"] == "nats://nats:4222"
    # User override survives
    assert merged["memory"]["rules_top_k"] == 99
    # Profile-supplied memory defaults still present
    assert merged["memory"]["qdrant_url"] == "http://qdrant:6333"


def test_apply_profile_unknown_profile_is_noop():
    data = {"deployment": {"profile": "nonexistent_profile"}}
    assert apply_profile(data) == data


def test_yellow_plus_inference_profile_declares_lan_backend():
    merged = apply_profile({"deployment": {"profile": "yellow_plus_inference"}})
    backends = merged["vlm_router"]["backends"]
    assert len(backends) == 1
    assert backends[0]["kind"] == "ollama"
    assert "inference.lan" in backends[0]["base_url"]


# ─────────────────────────────────────────────────────────────────────
# ${ENV_VAR} interpolation (#266)
# ─────────────────────────────────────────────────────────────────────


def test_interpolate_env_expands_known_var(monkeypatch):
    monkeypatch.setenv("MY_PASSWORD", "s3cret")
    assert interpolate_env("user:${MY_PASSWORD}@host") == "user:s3cret@host"


def test_interpolate_env_uses_default_when_missing(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert interpolate_env("${MISSING_VAR:-fallback}") == "fallback"


def test_interpolate_env_unknown_var_becomes_empty(monkeypatch):
    monkeypatch.delenv("DEFINITELY_NOT_SET_XYZ", raising=False)
    assert interpolate_env("${DEFINITELY_NOT_SET_XYZ}") == ""


def test_interpolate_env_recurses_into_dicts_and_lists(monkeypatch):
    monkeypatch.setenv("X", "expanded")
    data = {"k": "${X}", "list": ["${X}", {"nested": "${X}"}]}
    assert interpolate_env(data) == {
        "k": "expanded",
        "list": ["expanded", {"nested": "expanded"}],
    }


# ─────────────────────────────────────────────────────────────────────
# Env-var overrides (#268)
# ─────────────────────────────────────────────────────────────────────


def test_env_overrides_set_nested_field(monkeypatch):
    monkeypatch.setenv("SENTIHOME__BUS__NATS_URL", "nats://override:4222")
    out = apply_env_overrides({"bus": {"nats_url": "nats://default:4222"}})
    assert out["bus"]["nats_url"] == "nats://override:4222"


def test_env_overrides_index_into_list(monkeypatch):
    monkeypatch.setenv("SENTIHOME__VLM_ROUTER__BACKENDS__0__BASE_URL", "http://lan:11434")
    monkeypatch.setenv("SENTIHOME__VLM_ROUTER__BACKENDS__0__MODEL", "qwen2.5-vl:32b")
    out = apply_env_overrides(
        {
            "vlm_router": {
                "backends": [{"name": "x", "kind": "ollama", "base_url": "old", "model": "old"}]
            }
        }
    )
    assert out["vlm_router"]["backends"][0]["base_url"] == "http://lan:11434"
    assert out["vlm_router"]["backends"][0]["model"] == "qwen2.5-vl:32b"


def test_env_overrides_coerce_scalars(monkeypatch):
    monkeypatch.setenv("SENTIHOME__MEMORY__RULES_TOP_K", "42")
    monkeypatch.setenv("SENTIHOME__HA_AGENT__WEBSOCKET", "false")
    out = apply_env_overrides({})
    assert out["memory"]["rules_top_k"] == 42
    assert out["ha_agent"]["websocket"] is False


# ─────────────────────────────────────────────────────────────────────
# load_topology end-to-end (#266, #267, #268, #269)
# ─────────────────────────────────────────────────────────────────────


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "sentihome.yaml"
    p.write_text(dedent(body), encoding="utf-8")
    return p


def test_load_topology_from_explicit_path(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        deployment:
          profile: distributed
          household_id: testhouse
        bus:
          nats_url: nats://test:4222
        """,
    )
    t = load_topology(path=path, apply_env=False)
    assert t.deployment.household_id == "testhouse"
    assert t.bus.nats_url == "nats://test:4222"


def test_load_topology_layered_profile_then_user_then_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "tok_from_env")
    monkeypatch.setenv("SENTIHOME__MEMORY__RULES_TOP_K", "77")
    path = _write_yaml(
        tmp_path,
        """
        deployment:
          profile: yellow_single_box
        ha_agent:
          ha_token: ${MY_TOKEN}
        memory:
          rules_top_k: 5
        """,
    )
    t = load_topology(path=path)
    # Profile default (yellow_single_box overrides postgres host)
    assert "postgres" in t.memory.postgres_url
    # ${VAR} expansion
    assert t.ha_agent.ha_token == "tok_from_env"
    # Env override beats YAML
    assert t.memory.rules_top_k == 77


def test_supervisor_options_prefer_supervisor_token_over_long_lived_token(tmp_path, monkeypatch):
    """v0.1.12 regression: a user-pasted long-lived token AGAINST the
    Supervisor proxy URL gives 401. The mapper must use SUPERVISOR_TOKEN
    when ha_url is the proxy, regardless of what's in options."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor_xyz")
    opts = {
        "profile": "yellow_single_box",
        "ha_token": "user_pasted_long_lived_token_that_wont_work_here",
    }
    p = tmp_path / "options.json"
    p.write_text(json.dumps(opts), encoding="utf-8")
    t = load_topology(path=p, apply_env=False)
    assert t.ha_agent.ha_url == "http://supervisor/core"
    assert t.ha_agent.ha_token == "supervisor_xyz"  # NOT the user's token


def test_supervisor_options_use_user_token_when_url_is_direct(tmp_path, monkeypatch):
    """If the user points ha_url at HA Core directly, their long-lived
    token IS valid and should be used in preference to SUPERVISOR_TOKEN."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor_xyz")
    opts = {
        "profile": "distributed",
        "ha_url": "http://homeassistant.local:8123",
        "ha_token": "user_pasted_long_lived_token",
    }
    p = tmp_path / "options.json"
    p.write_text(json.dumps(opts), encoding="utf-8")
    t = load_topology(path=p, apply_env=False)
    assert t.ha_agent.ha_url == "http://homeassistant.local:8123"
    assert t.ha_agent.ha_token == "user_pasted_long_lived_token"


def test_load_topology_supervisor_options_json(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor_xyz")
    opts = {
        "profile": "yellow_single_box",
        "household_id": "addon_house",
        "bus": {"nats_url": "nats://nats:4222"},
    }
    p = tmp_path / "options.json"
    p.write_text(json.dumps(opts), encoding="utf-8")
    t = load_topology(path=p)
    assert t.deployment.household_id == "addon_house"
    assert t.ha_agent.ha_token == "supervisor_xyz"  # injected from env
    assert t.ha_agent.ha_url == "http://supervisor/core"


def test_load_topology_unknown_field_rejected(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        bus:
          nats_url: nats://x:4222
          unknown_field: oops
        """,
    )
    with pytest.raises(ValidationError):
        load_topology(path=path, apply_env=False)


# ─────────────────────────────────────────────────────────────────────
# Bootstrap report types (#270 — full probe is exercised in integration)
# ─────────────────────────────────────────────────────────────────────


def test_bootstrap_report_ok_when_all_ok_or_skipped():
    r = BootstrapReport(
        results=[
            ProbeResult(name="a", status=ProbeStatus.ok, latency_ms=10),
            ProbeResult(name="b", status=ProbeStatus.skipped, latency_ms=0),
        ]
    )
    assert r.ok is True


def test_bootstrap_report_summary_renders_each_hop():
    r = BootstrapReport(
        results=[
            ProbeResult(name="bus.nats", status=ProbeStatus.ok, latency_ms=12.3, detail=""),
            ProbeResult(
                name="memory.redis",
                status=ProbeStatus.unreachable,
                latency_ms=5000,
                detail="Timeout",
            ),
        ]
    )
    text = r.summary()
    assert "[OK] bus.nats" in text
    assert "[FAIL] memory.redis" in text
    assert "Timeout" in text
    assert r.ok is False


# ─────────────────────────────────────────────────────────────────────
# Sanity: example YAML in the repo loads cleanly
# ─────────────────────────────────────────────────────────────────────


def test_shipped_example_yaml_loads(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("CAM_USER", "u")
    monkeypatch.setenv("CAM_PASS", "p")
    monkeypatch.setenv("HA_TOKEN", "tok")
    example = (
        Path(__file__).resolve().parents[3] / "infrastructure" / "docker" / "sentihome.example.yaml"
    )
    assert example.exists(), f"missing {example}"
    # Round-trip YAML to validate structure without env-override side effects.
    raw = yaml.safe_load(example.read_text(encoding="utf-8"))
    assert "deployment" in raw and "vlm_router" in raw
    t = load_topology(path=example, apply_env=False)
    assert t.deployment.household_id  # populated from example
    assert len(t.vlm_router.backends) >= 1
