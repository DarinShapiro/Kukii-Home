# services/

Long-running Python services that compose the SentiHome runtime. Each service is independently deployable, communicates over the event bus (NATS JetStream) or MCP, and follows the architectural boundaries laid out in [`docs/architecture/`](../docs/architecture/).

## Services

| Service | Architecture ref | Purpose |
|---------|-----------------|---------|
| [`core/`](core/) | §02, §06, §10, §15 | Triage worker, rule engine, action dispatcher — the orchestration brain |
| [`preprocessor/`](preprocessor/) | §03.5, §08 | Motion gating + frame markup + fast-detector enrichment for service-mode NVR adapters |
| [`detector/`](detector/) | §08 | Standalone fast-detector models (YOLO, face, re-ID, pose, OCR) — can be embedded in preprocessor or run separately |
| [`vlm-router/`](vlm-router/) | §04 | Multi-backend model router; local Ollama + cloud fallback with circuit breaker |
| [`memory/`](memory/) | §11, §12 | Memory MCP — vector DB, SQL, episodic, identity gallery, visit ledger |
| [`ha-agent/`](ha-agent/) | §07 | Bidirectional MCP server: read-side LLM-backed HA synthesis + write-side device commands |
| [`notify/`](notify/) | §15 | Notification dispatcher — push, voice/TTS, in-app, ask flows |

## Conventions

- **Language:** Python 3.12+ with `uv` for dependency management
- **Async:** `asyncio` throughout; long-running services are async by default
- **Communication:** NATS JetStream for events, MCP for tool calls, REST as escape hatch
- **Config:** Environment variables + YAML config files (no secrets in repo)
- **Logging:** Structured JSON logs (one event per line); trace IDs propagated
- **Testing:** `pytest` with `pytest-asyncio`; integration tests live in `../tests/integration/`
- **Packaging:** Each service is a Docker image; compose file in `../infrastructure/docker/`

## Adding a new service

1. Create the service folder with a README describing scope
2. Add to `infrastructure/docker/docker-compose.yml`
3. Define MCP contract in `shared/protos/` if it exposes tools
4. Add event schemas to `shared/schemas/` if it publishes/subscribes
5. Wire up integration tests in `tests/integration/`
6. Update this README's service table
