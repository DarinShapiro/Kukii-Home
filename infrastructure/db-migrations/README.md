# Database migrations

Alembic-managed schema migrations for Kukii-Home's relational store (Postgres).

## Layout

```
db-migrations/
├── alembic.ini           Alembic config (DB URL via env var override)
├── env.py                Async-aware migration runner
├── script.py.mako        Template for new revisions
└── versions/             Migration revisions (created by alembic revision)
```

## Common commands

```bash
# Generate a new migration after editing services/memory/src/kukiihome_memory/models.py
uv run alembic -c infrastructure/db-migrations/alembic.ini \
    revision --autogenerate -m "describe the change"

# Apply all pending migrations
uv run alembic -c infrastructure/db-migrations/alembic.ini upgrade head

# Roll back one migration
uv run alembic -c infrastructure/db-migrations/alembic.ini downgrade -1

# View current revision
uv run alembic -c infrastructure/db-migrations/alembic.ini current

# View history
uv run alembic -c infrastructure/db-migrations/alembic.ini history
```

## Environment overrides

`DATABASE_URL` env var overrides the URL in `alembic.ini`:

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db \
    uv run alembic -c infrastructure/db-migrations/alembic.ini upgrade head
```

## Convention

- One migration per logical change (don't batch unrelated schema changes)
- Migration messages: imperative present tense ("add visitor_consent column")
- Always test downgrade locally before committing
- Migrations are idempotent where possible (use `if_exists=True` for drops, etc.)
