# services/memory/

Memory MCP server. Five memory layers: working, session, episodic, identity, semantic. Backed by SQL (sessions, rules, audit) + Vector DB (embeddings, episodic summaries) + object store (frames, montages).

**Architecture:** [§11](../../docs/architecture/11-memory-model.md), [§12](../../docs/architecture/12-recognition-and-identity.md), [§12.5](../../docs/architecture/12.5-dynamic-identity-refinement.md)

## Responsibilities

- Sessions: open, append segment, close
- Rule registry: hybrid retrieval (SQL filter + ANN rank)
- Identity gallery: face/body embeddings, multi-modal candidates
- Visit ledgers per subject
- Episodic summaries with similarity recall
- SituationalContexts and TransientIntents lifecycle

## Status

Skeleton. Implementation tracked in [`planning/epics/06-memory-storage.md`](../../planning/epics/).
