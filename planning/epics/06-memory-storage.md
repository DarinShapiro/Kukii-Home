# Epic 06: Memory & Storage

**Architecture refs:** §11, §12, §13
**Components:** services/memory, infrastructure/db-migrations
**Priority:** P0
**Blocked by:** Epic 01, 02

## Description

The memory MCP service backing all five memory layers (working, session, episodic, identity, semantic) plus the spatial model (cameras, areas, zones). Hybrid SQL + Vector DB + object store. Owns lifecycle management (TTL, retention, soft-delete + grace).

## Definition of done

- Postgres + Qdrant (or equivalent vector DB) running in docker-compose
- Schema migrations work via Alembic
- All `memory.*` MCP tools per §07 implemented
- Five memory layers have clean lifecycles
- Identity gallery supports multi-modal candidates (face, body, behavioral)
- Visit ledgers per subject working
- Spatial model: cameras, areas, zones, adjacency graph

## Issues

1. **chore(infra): postgres in docker-compose with persistent volume** — schema bootstrap on first run. (labels: `epic:memory`, `component:infrastructure`, `priority:p0`)
2. **chore(infra): qdrant vector DB in docker-compose** — persistent volume, collection bootstrap. (labels: `epic:memory`, `component:infrastructure`, `priority:p0`)
3. **chore(infra): Alembic migration tooling** — initial migration, migration scripts in `infrastructure/db-migrations/`. (labels: `epic:memory`, `component:infrastructure`, `priority:p0`)
4. **feat(memory): SQL schema for sessions** — open/append/close, multi-camera segments. (labels: `epic:memory`, `component:memory`, `priority:p0`)
5. **feat(memory): SQL schema for rules** — full rule record per §10, with lifecycle counters. (labels: `epic:memory`, `component:memory`, `priority:p0`)
6. **feat(memory): SQL schema for KnownActors + identity records** — multi-modal candidates, confidence histories. (labels: `epic:memory`, `component:memory`, `priority:p0`)
7. **feat(memory): SQL schema for VisitLedgers + episodic metadata** — per-subject visit history, episodic summaries. (labels: `epic:memory`, `component:memory`, `priority:p0`)
8. **feat(memory): SQL schema for audit log + cloud egress** — per §16 governance. (labels: `epic:memory`, `component:memory`, `priority:p0`)
9. **feat(memory): vector DB collections** — face embeddings, body re-ID, rule embeddings, episodic embeddings. (labels: `epic:memory`, `component:memory`, `priority:p0`)
10. **feat(memory): object store for clips + frames + montages** — local filesystem backend; pluggable for S3-compatible. (labels: `epic:memory`, `component:memory`, `priority:p0`)
11. **feat(memory): `memory.open_session` / `append_segment` / `close_session`** — session lifecycle MCP tools. (labels: `epic:memory`, `component:memory`, `priority:p0`)
12. **feat(memory): `memory.retrieve_rules` hybrid retrieval** — SQL filter + ANN rank, top-K budgeting. (labels: `epic:memory`, `component:memory`, `priority:p0`)
13. **feat(memory): `memory.get_active_contexts` + `get_active_intents`** — SituationalContext and TransientIntent retrieval. (labels: `epic:memory`, `component:memory`, `priority:p0`)
14. **feat(memory): `memory.resolve_identity`** — top-N identity candidates with confidence + access profiles. (labels: `epic:memory`, `component:memory`, `priority:p0`)
15. **feat(memory): `memory.recall_episodic`** — similar past sessions, summarized. (labels: `epic:memory`, `component:memory`, `priority:p0`)
16. **feat(memory): `memory.write_episodic`** — close session and write summary + embedding. (labels: `epic:memory`, `component:memory`, `priority:p0`)
17. **feat(memory): `memory.update_visit_ledger`** — per-subject visit history append. (labels: `epic:memory`, `component:memory`, `priority:p1`)
18. **feat(memory): TTL + retention enforcement per data class** — nightly cleanup job (§16). (labels: `epic:memory`, `component:memory`, `priority:p1`)
19. **feat(memory): soft-delete + grace period mechanism** — for right-to-forget. (labels: `epic:memory`, `component:memory`, `priority:p1`)
20. **feat(memory): SQL schema for cameras/areas/zones** — full spatial model per §13. (labels: `epic:memory`, `component:memory`, `priority:p1`)
21. **feat(memory): adjacency graph computation** — derived from area model, used for spatial plausibility. (labels: `epic:memory`, `component:memory`, `priority:p1`)
22. **test: memory MCP integration tests** — round-trip sessions, identity resolution, episodic recall. (labels: `epic:memory`, `component:memory`, `priority:p1`)
