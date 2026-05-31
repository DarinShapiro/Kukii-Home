# infrastructure/

Deployment and runtime infrastructure: Docker Compose stacks, NATS JetStream configuration, database migrations, and bootstrap scripts.

## Layout

| Folder                             | Purpose                                                                                                                                                                                            |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`docker/`](docker/)               | Docker Compose files for local dev and production deployment. Each service has its own Dockerfile in the service folder; this directory composes them.                                             |
| [`nats/`](nats/)                   | NATS JetStream stream + consumer definitions (see §03). Declared as YAML; applied via `nats` CLI.                                                                                                  |
| [`db-migrations/`](db-migrations/) | SQL migrations for Kukii-Home's relational stores (sessions, rules, episodic metadata, audit log). Run with Alembic. Vector DB schemas live with the memory service since they're library-specific. |

## Deployment topologies

Architecture: [§02 High-Level Architecture](../docs/architecture/02-high-level-architecture.md), [§18 Hardware Sizing](../docs/architecture/18-hardware-sizing.md)

- **Single-box dev:** all services on one machine via `docker-compose -f docker/dev.yml`
- **Single-box production:** same, but with production configs and external volumes
- **Split-host production:** compose file per host (compute, NVR/preprocessing, NAS)
- **HA add-on (future):** Kukii-Home packaged as a HA add-on for installation through HA's Supervisor

## Conventions

- **Local dev:** `docker-compose -f docker/dev.yml up` brings up the full stack including NATS, postgres, vector DB, and all services in dev mode
- **Production:** images are tagged by git SHA; deployments are immutable
- **Config:** environment-specific overrides in `docker/overrides/` (e.g., `production.yml`, `staging.yml`)
- **Secrets:** never in repo; use Docker secrets, environment variables, or external secret stores
- **Persistence:** volumes for vector DB, SQL, object store, NATS JetStream — defined in compose files

## Storage layout

```
~/kukiihome-data/
├── postgres/         SQL (sessions, rules, episodic metadata)
├── qdrant/           Vector DB (embeddings, rule vectors)
├── objects/          Frame crops, clips, montages
├── nats/             JetStream durable streams
└── logs/             Structured logs (also forwarded to external collectors)
```

## Bootstrap

```bash
# First-time setup
./scripts/setup/install.sh
docker-compose -f infrastructure/docker/dev.yml up -d
./scripts/setup/migrate.sh
```

See [`../scripts/setup/`](../scripts/setup/) for the underlying scripts.
