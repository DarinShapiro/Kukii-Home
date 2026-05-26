# 02.5 — Deployment Topologies & Bootstrap Configuration

**Purpose:** How SentiHome is deployed across one or more machines, and how every service learns where its dependencies live at bootstrap.
**Status:** stable (Epic 8.5 / #265)
**Related:** §02 high-level architecture, §07 ha-agent, `infrastructure/docker/sentihome.example.yaml`

---

## Why this exists

Every SentiHome service needs to know where its dependencies are: NATS, Postgres, Qdrant, Redis, the VLM backend(s), Home Assistant, and per-resident notification routing. Households deploy in materially different shapes — one HA Yellow doing everything, Yellow plus a separate inference NUC, multiple servers across the LAN, or HA add-on under Supervisor. Hard-coding `localhost` defaults or sprinkling env-var reads through each service breaks the moment a household differs from the developer's laptop.

The fix is a **single per-household topology config** that every service reads at startup. One file declares the entire topology; one loader applies layered overrides; one bootstrap step pings every declared hop and fails loud if anything is unreachable.

---

## The three shipped profiles

A profile is a named dict-merge layer that populates sensible defaults for one deployment shape. Pick one, then override individual fields as needed.

### `yellow_single_box`

Everything on one HA Yellow (CM4 + 8 GB + NVMe + optional Coral). NATS / Postgres / Qdrant / Redis / SentiHome services all run under the HA Supervisor's Docker network. The VLM lives off-box; this profile assumes you've configured a cloud fallback (or a small CPU-only model on Yellow for prototyping).

```
┌─── HA Yellow ────────────────────────────────────────────────┐
│  HA Core · Mosquitto · Frigate (Coral)                        │
│  NATS · Postgres · Qdrant · Redis                             │
│  services/{core, memory, ha-agent, notify, preprocessor,      │
│            vlm-router}                                        │
└──────────────────────────────────────────────────────────────┘
                       │ optional fallback
                       ▼
              Cloud VLM (Anthropic/OpenAI)
```

### `yellow_plus_inference` (recommended for production)

Yellow is the always-on brain + bus + storage; a separate LAN box (NUC + discrete GPU, Mac Studio, etc.) hosts Ollama or vLLM. The inference box can be powered down; the cloud fallback kicks in via the router's circuit breaker.

```
┌─── HA Yellow ────────────────────┐    ┌─── Inference box ────┐
│  HA Core · Frigate               │    │  Ollama (qwen2.5-vl) │
│  NATS · Postgres · Qdrant · Redis│◀──▶│  or vLLM             │
│  SentiHome services + vlm-router │    └──────────────────────┘
└──────────────────────────────────┘
                  │
                  ▼  cloud_eligible events only
         Cloud VLM (optional fallback)
```

### `distributed`

No host defaults. Every URL is declared explicitly. Use this when you're running services across multiple LAN hosts, on Kubernetes, or in any non-standard shape.

---

## Override precedence

Later layers win:

```
defaults (in the pydantic model)
  └─ named profile (from PROFILES[name])
       └─ YAML file (sentihome.yaml or /data/options.json)
            └─ ${ENV_VAR} interpolation inside string values
                 └─ SENTIHOME__SECTION__FIELD env-var overrides
                      └─ cli_overrides dict (optional)
```

### YAML lookup chain

`load_topology()` searches in order and uses the first existing file:

1. Explicit `path=` argument
2. `SENTIHOME_CONFIG` env var
3. `/data/options.json` (HA add-on Supervisor convention)
4. `/etc/sentihome/sentihome.yaml` (system-wide)
5. `~/.sentihome/config.yaml` (per-user dev)

### `${ENV_VAR}` interpolation

Any string field accepts `${VAR}` or `${VAR:-default}` syntax — secrets like API keys and database passwords stay out of the file and in the environment (or HA's secrets store):

```yaml
memory:
  postgres_url: postgresql+asyncpg://sentihome:${POSTGRES_PASSWORD}@postgres:5432/sentihome
ha_agent:
  ha_token: ${HA_TOKEN:-${SUPERVISOR_TOKEN:-}}
```

Unknown variables with no default expand to empty string and emit a warning; downstream pydantic validation catches required-but-empty fields.

### Env-var overrides

`SENTIHOME__SECTION__SUBSECTION__FIELD=value`. Double underscore = dotted path. Numeric segments index into lists. Values are coerced: `true`/`false` → bool, `null` → None, numeric → int/float, else string.

```bash
SENTIHOME__BUS__NATS_URL=nats://nats.lan:4222
SENTIHOME__VLM_ROUTER__BACKENDS__0__BASE_URL=http://10.0.0.5:11434
SENTIHOME__VLM_ROUTER__BACKENDS__0__MODEL=qwen2.5-vl:32b
SENTIHOME__MEMORY__RULES_TOP_K=10
```

This is the supported way to tweak production deployments without re-rolling the YAML file.

---

## Installing as a Supervisor add-on

The repo is itself an HA add-on repository. To install SentiHome on a
Home Assistant OS / Supervised instance:

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories**
2. Paste `https://github.com/DarinShapiro/SentiHome` and **Add**
3. Refresh — the **SentiHome** tile appears
4. Click **Install**, then open the **Configuration** tab
5. Pick a profile + fill in nested sections (see
   [`infrastructure/docker/sentihome.example.yaml`](../../infrastructure/docker/sentihome.example.yaml))
6. Click **Start**

Supervisor writes the form to `/data/options.json`; the add-on's
`cont-init.d/10-bootstrap.sh` exports `SENTIHOME_CONFIG=/data/options.json`
so the topology loader picks it up automatically.

## HA add-on Supervisor mapping

When SentiHome runs as a Home Assistant add-on, Supervisor provides:

- `/data/options.json` — flat JSON populated by the add-on's options UI
- `SUPERVISOR_TOKEN` — auth token for the local `http://supervisor/core` proxy

The loader detects `/data/options.json` and converts it to a topology dict via `_supervisor_options_to_topology`. The add-on's `config.yaml` (the Supervisor manifest) declares the same fields the topology model expects, flattened one level:

| options.json key                                    | Topology field                                        |
| --------------------------------------------------- | ----------------------------------------------------- |
| `profile`                                           | `deployment.profile`                                  |
| `household_id`                                      | `deployment.household_id`                             |
| `timezone`                                          | `deployment.timezone`                                 |
| `ha_url`                                            | `ha_agent.ha_url` (default `http://supervisor/core`)  |
| `ha_token`                                          | `ha_agent.ha_token` (defaults to `$SUPERVISOR_TOKEN`) |
| `bus`, `memory`, `vlm_router`, `notify`, `adapters` | pass-through                                          |

For nested sections the user pastes structured YAML through the add-on options UI (Supervisor accepts arbitrary nested fields if the add-on schema declares them as `match(.+)`). For the simplest path, leave the nested sections at their profile defaults and override individual fields with env vars in the add-on's "Network" tab.

---

## Bootstrap dependency-ping

After loading a topology, every service calls `await topology.verify()` before opening its main consumer loops. The probe runs concurrently across all declared dependencies and returns a `BootstrapReport`:

```python
from sentihome_shared.topology import load_topology

topology = load_topology()
report = await topology.verify(probe_timeout_s=5.0)
if not report.ok:
    raise SystemExit(report.summary())
```

A failing report prints one line per hop:

```
SentiHome topology bootstrap:
  [OK]   bus.nats         (12 ms)
  [OK]   memory.postgres  (8 ms)
  [OK]   memory.qdrant    (15 ms) HTTP 200
  [FAIL] memory.redis     (5000 ms) Timeout connecting to redis://redis:6379/0
  [OK]   ha_agent         (42 ms) HTTP 200
  [OK]   vlm.lan-ollama   (130 ms) HTTP 200
```

This catches misconfiguration before it manifests as a cryptic NPE deep in the first real request.

Probes are best-effort: if the optional client library for a hop isn't installed in this service's environment, the probe returns `skipped` rather than failing. Each service declares the client libraries it actually needs as its own dependency.

---

## Consuming the topology in services

Every service follows the same shape:

```python
from sentihome_shared.topology import load_topology
from sentihome_shared.bus import Bus
from sentihome_memory import MemoryStore, MemoryStoreConfig

async def main():
    topology = load_topology()

    report = await topology.verify()
    if not report.ok:
        raise SystemExit(report.summary())

    bus = Bus(topology.bus.nats_url)
    store = MemoryStore(MemoryStoreConfig.from_topology(topology.memory))
    # ... start consumer loops ...
```

Each service config dataclass (`MemoryStoreConfig`, `BackendConfig`, `HAAgentSettings`, etc.) has a `from_topology()` classmethod that converts the topology slice into its existing shape. The legacy constructors are preserved so unit tests can still construct config objects directly without going through the YAML loader.

---

## Where this lives in the code

| Module                                                       | Role                                                                                          |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `shared/lib-python/src/sentihome_shared/topology.py`         | Pydantic model tree, profile presets, YAML loader, env-override layer, `${VAR}` interpolation |
| `shared/lib-python/src/sentihome_shared/_topology_probes.py` | Bootstrap dependency-ping implementations                                                     |
| `infrastructure/docker/sentihome.example.yaml`               | Annotated starter config                                                                      |
| `services/core/src/sentihome_core/adapter_registry.py`       | `bootstrap_from_topology(adapters)`                                                           |
| `services/memory/src/sentihome_memory/store.py`              | `MemoryStoreConfig.from_topology(memory)`                                                     |
| `services/vlm-router/src/sentihome_vlm_router/router.py`     | `build_backends_from_topology(topology)`                                                      |
| `services/notify/src/sentihome_notify/dispatcher.py`         | `NotifyWorker.from_topology(topology, ha_caller=...)`                                         |
| `services/ha-agent/src/sentihome_ha_agent/config.py`         | `HAAgentSettings.from_topology(topology)`                                                     |
