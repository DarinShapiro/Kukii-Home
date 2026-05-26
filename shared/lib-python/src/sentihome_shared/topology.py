"""Deployment topology — central per-household configuration.

Architecture: docs/architecture/02-high-level-architecture.md (deployment
topologies section), Epic 8.5 (#265).

Every SentiHome service reads its connection topology from one place: a
:class:`Topology` model populated by :func:`load_topology` from a layered
config (defaults → named profile → YAML → env-var overrides → CLI). Secrets
stay outside the YAML via ``${ENV_VAR}`` interpolation, and a bootstrap
:meth:`Topology.verify` async method pings every declared dependency and
returns a structured :class:`BootstrapReport`.

The three named profiles (``yellow_single_box``, ``yellow_plus_inference``,
``distributed``) collapse the boilerplate for the common household shapes;
users override individual fields without restating the rest.

Lookup order for the YAML path:
    1. Explicit ``path=`` argument to :func:`load_topology`
    2. ``SENTIHOME_CONFIG`` env var
    3. ``/data/options.json`` (HA Supervisor add-on convention)
    4. ``/etc/sentihome/sentihome.yaml``
    5. ``~/.sentihome/config.yaml``

Override precedence (later wins):
    defaults < profile < YAML < env-var (``SENTIHOME__SECTION__FIELD=...``) < CLI

Usage::

    from sentihome_shared.topology import load_topology

    topology = load_topology()                       # service startup
    bus = Bus(topology.bus.nats_url)
    store = MemoryStore(topology.memory)
    report = await topology.verify()
    if not report.ok:
        raise SystemExit(report.summary())
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Section models (each maps to one block of sentihome.yaml)
# ─────────────────────────────────────────────────────────────────────


class _StrictModel(BaseModel):
    """Forbid extra keys — catches YAML typos instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class DeploymentMeta(_StrictModel):
    profile: str = "distributed"
    household_id: str = "default"
    timezone: str = "UTC"


class BusConfig(_StrictModel):
    nats_url: str = "nats://localhost:4222"
    """NATS JetStream endpoint."""
    stream_prefix: str = ""
    """Optional subject prefix for multi-tenant deployments."""


class MemoryConfig(_StrictModel):
    postgres_url: str = "postgresql+asyncpg://sentihome:sentihome@localhost:5432/sentihome"
    qdrant_url: str = "http://localhost:6333"
    redis_url: str = "redis://localhost:6379/0"
    object_store: str = "file:///var/lib/sentihome/objects"
    """Frame/clip storage. ``file://`` or ``s3://`` or ``minio://``."""
    rules_top_k: int = 5
    episodic_top_k: int = 3


class PrivacyTierMax(StrEnum):
    local_only = "local_only"
    cloud_eligible = "cloud_eligible"
    cloud_any = "cloud_any"


class VLMBackendConfig(_StrictModel):
    name: str
    kind: Literal["ollama", "vllm", "openai_compatible"]
    base_url: str
    model: str
    api_key: str | None = None
    privacy_tier_max: PrivacyTierMax = PrivacyTierMax.local_only
    timeout_seconds: float = 30.0
    cost_per_1k_tokens_usd: float = 0.0
    typical_latency_ms: int = 2000

    @model_validator(mode="after")
    def _check_api_key_required(self) -> VLMBackendConfig:
        if self.kind == "openai_compatible" and not self.api_key:
            raise ValueError(f"backend {self.name!r}: openai_compatible kind requires api_key")
        return self


class VLMRouterConfig(_StrictModel):
    backends: list[VLMBackendConfig] = Field(default_factory=list)


class HAAgentConfig(_StrictModel):
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""
    """Long-lived access token. Empty means HA-agent will refuse to start."""
    websocket: bool = True


class NotifyConfig(_StrictModel):
    resident_to_push_service: dict[str, str] = Field(default_factory=dict)
    """resident_id → ``notify.mobile_app_<device_id>`` HA service."""
    tts_service: str = "tts.cloud_say"
    media_players: list[str] = Field(default_factory=list)


class AdapterConfig(_StrictModel):
    name: str
    """Unique identifier used in logs + metrics."""
    kind: Literal[
        "rtsp-direct",
        "ha-camera",
        "agent-dvr",
        "frigate",
        "blueiris",
        "synology",
        "qnap",
        "unifi",
    ]
    url: str | None = None
    username: str | None = None
    password: str | None = None
    mqtt_host: str | None = None
    streams: list[dict[str, Any]] = Field(default_factory=list)
    """For rtsp-direct: per-camera ``{id, rtsp_url, ...}`` dicts."""

    # ─── ha-camera fields ─────────────────────────────────────────
    camera_entity: str | None = None
    """For ha-camera: HA camera entity id, e.g. ``camera.pool_cam``."""
    motion_entities: list[str] = Field(default_factory=list)
    """For ha-camera: list of ``binary_sensor.*`` entities whose
    off→on transitions trigger a snapshot + alert. Typically the camera's
    onboard-AI motion / person / vehicle sensors."""
    snapshot_cooldown_seconds: float = 30.0
    """For ha-camera: minimum seconds between snapshots from this camera."""


class Topology(_StrictModel):
    deployment: DeploymentMeta = Field(default_factory=DeploymentMeta)
    bus: BusConfig = Field(default_factory=BusConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    vlm_router: VLMRouterConfig = Field(default_factory=VLMRouterConfig)
    ha_agent: HAAgentConfig = Field(default_factory=HAAgentConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    adapters: list[AdapterConfig] = Field(default_factory=list)
    auto_discover: bool = True
    """Zero-config camera onboarding (v0.3.11+). When True and
    ``adapters`` is empty, the ha-agent auto-discovers every HA camera
    entity, AI-picks the best stream + motion sensors per device, and
    wires up :class:`HACameraLoop` instances live. Per-device overrides
    (enable/disable, stream, motion, cooldown) live in
    ``/data/sentihome/adapter_overrides.json`` and are editable via the
    /ha_cameras Web UI card.

    Setting this False (or providing a non-empty ``adapters``) falls
    back to the legacy hand-written-YAML path."""

    async def verify(self, *, probe_timeout_s: float = 5.0) -> BootstrapReport:
        """Ping every declared dependency. Returns a :class:`BootstrapReport`."""
        from sentihome_shared._topology_probes import probe_topology

        return await probe_topology(self, timeout=probe_timeout_s)


# ─────────────────────────────────────────────────────────────────────
# Deployment profile presets (#267)
# ─────────────────────────────────────────────────────────────────────


PROFILES: dict[str, dict[str, Any]] = {
    # Everything runs on one HA Yellow box (compose-network hostnames).
    "yellow_single_box": {
        "deployment": {"profile": "yellow_single_box"},
        "bus": {"nats_url": "nats://nats:4222"},
        "memory": {
            "postgres_url": "postgresql+asyncpg://sentihome:sentihome@postgres:5432/sentihome",
            "qdrant_url": "http://qdrant:6333",
            "redis_url": "redis://redis:6379/0",
            "object_store": "file:///data/sentihome/objects",
        },
        "ha_agent": {"ha_url": "http://supervisor/core"},
    },
    # Yellow runs the brain + storage; a separate LAN box hosts the VLM.
    "yellow_plus_inference": {
        "deployment": {"profile": "yellow_plus_inference"},
        "bus": {"nats_url": "nats://nats:4222"},
        "memory": {
            "postgres_url": "postgresql+asyncpg://sentihome:sentihome@postgres:5432/sentihome",
            "qdrant_url": "http://qdrant:6333",
            "redis_url": "redis://redis:6379/0",
        },
        "vlm_router": {
            "backends": [
                {
                    "name": "lan-ollama",
                    "kind": "ollama",
                    "base_url": "http://inference.lan:11434",
                    "model": "qwen2.5-vl:7b",
                    "privacy_tier_max": "local_only",
                }
            ]
        },
        "ha_agent": {"ha_url": "http://supervisor/core"},
    },
    # Fully explicit; no host defaults. User must specify every URL.
    "distributed": {
        "deployment": {"profile": "distributed"},
    },
}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge — overlay wins; lists are *replaced*, not appended."""
    out = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def apply_profile(data: dict[str, Any]) -> dict[str, Any]:
    """If ``data.deployment.profile`` names a preset, layer it under ``data``."""
    profile_name = (data.get("deployment") or {}).get("profile")
    if not profile_name or profile_name not in PROFILES:
        return data
    preset = PROFILES[profile_name]
    return _deep_merge(preset, data)


# ─────────────────────────────────────────────────────────────────────
# ${ENV_VAR} interpolation (#266)
# ─────────────────────────────────────────────────────────────────────


_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def interpolate_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` and ``${VAR:-default}`` in strings.

    Unknown variables with no default become empty string and emit a warning;
    the model validator catches required-field-empty errors downstream.
    """
    if isinstance(value, str):

        def _sub(m: re.Match[str]) -> str:
            var, default = m.group(1), m.group(2)
            if var in os.environ:
                return os.environ[var]
            if default is not None:
                return default
            logger.warning("topology.unresolved_env_var", var=var)
            return ""

        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_env(v) for v in value]
    return value


# ─────────────────────────────────────────────────────────────────────
# Env-var overrides (#268)
# ─────────────────────────────────────────────────────────────────────


_ENV_OVERRIDE_PREFIX = "SENTIHOME__"


def _coerce_scalar(s: str) -> Any:
    """Coerce env-var string to int/float/bool/None when unambiguous."""
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.lower() in ("null", "none", ""):
        return None
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def _set_path(target: dict[str, Any], path: list[str], value: Any) -> None:
    """Set ``target[a][b]…[z] = value``, descending dicts; numeric segments
    descend into lists by index, extending with empty dicts if needed."""
    cur: Any = target
    for i, segment in enumerate(path[:-1]):
        nxt_is_int = path[i + 1].isdigit()
        if segment.isdigit():
            idx = int(segment)
            while len(cur) <= idx:
                cur.append({} if not nxt_is_int else [])
            if not isinstance(cur[idx], (dict, list)):
                cur[idx] = [] if nxt_is_int else {}
            cur = cur[idx]
        else:
            if segment not in cur or not isinstance(cur[segment], (dict, list)):
                cur[segment] = [] if nxt_is_int else {}
            cur = cur[segment]
    last = path[-1]
    if last.isdigit():
        idx = int(last)
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    else:
        cur[last] = value


def apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Layer ``SENTIHOME__A__B__C=value`` env vars onto ``data``.

    Double-underscore segments become a dotted path. Numeric segments index
    into lists. Values are coerced (true/false/null/int/float/string).
    """
    out = json.loads(json.dumps(data))  # deep copy that handles dicts+lists
    for key, raw in os.environ.items():
        if not key.startswith(_ENV_OVERRIDE_PREFIX):
            continue
        path = key[len(_ENV_OVERRIDE_PREFIX) :].lower().split("__")
        if not path or not path[0]:
            continue
        _set_path(out, path, _coerce_scalar(raw))
    return out


# ─────────────────────────────────────────────────────────────────────
# Loader (#266, #269)
# ─────────────────────────────────────────────────────────────────────


_DEFAULT_PATHS = (
    "/data/options.json",
    "/etc/sentihome/sentihome.yaml",
    str(Path.home() / ".sentihome" / "config.yaml"),
)


def _find_config_path() -> Path | None:
    """Return the first config file that exists in the lookup chain."""
    if env := os.environ.get("SENTIHOME_CONFIG"):
        p = Path(env)
        if p.exists():
            return p
        logger.warning("topology.config_env_path_missing", path=env)
        return None
    for candidate in _DEFAULT_PATHS:
        p = Path(candidate)
        if p.exists():
            return p
    return None


def _load_file(path: Path) -> dict[str, Any]:
    """Load YAML or JSON depending on suffix. .json gets the Supervisor mapping."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        raw = json.loads(text) or {}
        return _supervisor_options_to_topology(raw)
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping, got {type(raw).__name__}")
    return raw


def _supervisor_options_to_topology(opts: dict[str, Any]) -> dict[str, Any]:
    """Map HA Supervisor add-on options.json → topology dict.

    Auth selection rule:
      The Supervisor proxy (``http://supervisor/core``) only accepts the
      ``SUPERVISOR_TOKEN`` env var that Supervisor injects automatically.
      Long-lived access tokens from HA's user UI work ONLY against HA Core
      directly (e.g. ``http://homeassistant.local:8123``). Mixing them
      gives 401.

      So:
        ha_url == http://supervisor/core  →  always use SUPERVISOR_TOKEN
                                              (ignore user-provided ha_token;
                                              it can't work here anyway)
        ha_url anything else              →  use user-provided ha_token
                                              (long-lived access token)
                                              and fall back to SUPERVISOR_TOKEN
                                              only if empty

    This lets a user paste a long-lived token in the add-on Configuration
    tab IF they also override ha_url to a direct HA address.
    """
    out: dict[str, Any] = {"deployment": {}, "ha_agent": {}}
    if "profile" in opts:
        out["deployment"]["profile"] = opts["profile"]
    if "household_id" in opts:
        out["deployment"]["household_id"] = opts["household_id"]
    if "timezone" in opts:
        out["deployment"]["timezone"] = opts["timezone"]

    ha_url = opts.get("ha_url", "http://supervisor/core")
    out["ha_agent"]["ha_url"] = ha_url

    is_supervisor_proxy = ha_url.rstrip("/") == "http://supervisor/core"
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    user_token = (opts.get("ha_token") or "").strip()

    if is_supervisor_proxy and supervisor_token:
        # The proxy only accepts SUPERVISOR_TOKEN; anything else 401s.
        out["ha_agent"]["ha_token"] = supervisor_token
    elif user_token:
        out["ha_agent"]["ha_token"] = user_token
    elif supervisor_token:
        out["ha_agent"]["ha_token"] = supervisor_token

    for section in ("bus", "memory", "vlm_router", "notify"):
        if section in opts:
            out[section] = opts[section]
    if "adapters" in opts:
        out["adapters"] = opts["adapters"]
    return out


def load_topology(
    *,
    path: str | Path | None = None,
    apply_env: bool = True,
    cli_overrides: dict[str, Any] | None = None,
) -> Topology:
    """Load + validate a :class:`Topology` from the layered config chain.

    Args:
        path: Explicit config-file path. If ``None``, the standard lookup
            chain runs (``SENTIHOME_CONFIG`` env → /data/options.json →
            /etc/sentihome/sentihome.yaml → ~/.sentihome/config.yaml).
        apply_env: When True (default), env-var overrides
            (``SENTIHOME__SECTION__FIELD=...``) are applied after the YAML.
        cli_overrides: Optional pre-parsed dict layered on top of everything.
    """
    data: dict[str, Any] = {}

    resolved = Path(path) if path is not None else _find_config_path()
    if resolved is not None and resolved.exists():
        logger.info("topology.loading", path=str(resolved))
        data = _load_file(resolved)

    data = apply_profile(data)

    if apply_env:
        data = apply_env_overrides(data)

    if cli_overrides:
        data = _deep_merge(data, cli_overrides)

    data = interpolate_env(data)
    return Topology.model_validate(data)


# ─────────────────────────────────────────────────────────────────────
# Bootstrap dependency-ping (#270) — public types live here, the actual
# probing implementations live in _topology_probes to keep this module
# import-cheap when only the model is needed.
# ─────────────────────────────────────────────────────────────────────


class ProbeStatus(StrEnum):
    ok = "ok"
    unreachable = "unreachable"
    skipped = "skipped"
    error = "error"


@dataclass
class ProbeResult:
    name: str
    status: ProbeStatus
    latency_ms: float
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == ProbeStatus.ok


@dataclass
class BootstrapReport:
    results: list[ProbeResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.status in (ProbeStatus.ok, ProbeStatus.skipped) for r in self.results)

    def summary(self) -> str:
        """One-line-per-hop human-readable summary."""
        lines = ["SentiHome topology bootstrap:"]
        for r in self.results:
            mark = {"ok": "OK", "unreachable": "FAIL", "skipped": "SKIP", "error": "ERR"}[
                r.status.value
            ]
            lines.append(f"  [{mark}] {r.name} ({r.latency_ms:.0f} ms) {r.detail}")
        return "\n".join(lines)

    def add(
        self, *, name: str, status: ProbeStatus, started_monotonic: float, detail: str = ""
    ) -> None:
        elapsed_ms = (time.monotonic() - started_monotonic) * 1000.0
        self.results.append(
            ProbeResult(name=name, status=status, latency_ms=elapsed_ms, detail=detail)
        )
