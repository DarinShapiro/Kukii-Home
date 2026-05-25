# services/vlm-router/

Multi-backend VLM router. Routes VLM calls across local (Ollama, vLLM, TGI) and cloud backends based on capability, privacy tier, cost, health, and affinity. Includes circuit breaker for failing backends.

**Architecture:** [§04](../../docs/architecture/04-model-router-and-inference.md)

## Responsibilities

- Maintain registry of available VLM backends
- Route per-call based on routing policy
- Enforce privacy tier constraints (local-only data never goes to cloud)
- Circuit breaker for repeatedly-failing backends
- Cost + latency tracking per backend
- Fall back to next backend on failure

## Status

Skeleton. Implementation tracked in [`planning/epics/05-vlm-router.md`](../../planning/epics/).
