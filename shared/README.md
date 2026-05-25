# shared/

Cross-language shared artifacts: schemas, MCP protocol definitions, and lightweight shared libraries for Python and TypeScript.

## Layout

| Folder | Purpose |
|--------|---------|
| [`schemas/`](schemas/) | JSON Schema definitions for events, messages, configuration objects (see §05). Source of truth for cross-service contracts. |
| [`protos/`](protos/) | MCP tool definitions for each MCP server (ha-agent, nvr-adapter, memory, notify). Used to generate client stubs in Python and TypeScript. |
| [`lib-python/`](lib-python/) | Shared Python utilities — event bus clients, MCP helpers, logging, tracing, config loading. Distributed as an internal package. |
| [`lib-typescript/`](lib-typescript/) | Shared TypeScript utilities — API client (generated from schemas), event types, formatting helpers. Distributed as an npm package. |

## Why a `shared/` directory in a monorepo?

Two reasons:

1. **Cross-cutting concerns** (event schemas, MCP contracts) need a single canonical location. Drift between services is the #1 cause of subtle integration bugs.
2. **Generated client code** (Python and TypeScript clients from OpenAPI/MCP schemas) needs a stable home that all services can import.

## Conventions

- **Schemas:** JSON Schema draft-2020-12, organized by domain (`schemas/events/`, `schemas/config/`, etc.)
- **Versioning:** every schema has a `$id` with a version segment; breaking changes bump the major version
- **Generation:** Python types via `datamodel-code-generator`; TypeScript via `json-schema-to-typescript`
- **CI:** schemas are validated and clients are regenerated on every PR; drift between source and generated artifacts fails the build
- **No business logic:** `shared/` is for contracts and primitives; if something has interesting behavior it belongs in a service or an adapter

## Adding a new schema

1. Add the JSON Schema to the appropriate `schemas/<domain>/` folder
2. Reference it from any event/message that uses it
3. Run the codegen script (`scripts/dev/regenerate-schemas.sh`)
4. Update consumers in services that use it
