# shared/schemas/

JSON Schema definitions for all cross-service messages and configuration objects. Source of truth for the event bus contracts (§05) and any cross-language data structures.

## Layout

```
schemas/
├── events/         # Bus event payloads (per §05)
│   ├── trigger-event.schema.json
│   ├── enriched-event.schema.json
│   ├── vlm-request.schema.json
│   ├── vlm-response.schema.json
│   └── ...
├── messages/       # Tool call inputs/outputs (MCP)
├── config/         # Per-service config models
└── common/         # Shared types (privacy tiers, severity, etc.)
```

## Conventions

- **Draft:** JSON Schema 2020-12
- **`$id`:** every schema has a `$id` URI with a version segment, e.g.
  `https://sentihome.io/schemas/v1/events/trigger-event.schema.json`
- **`$schema`:** always set to `https://json-schema.org/draft/2020-12/schema`
- **Naming:** kebab-case filenames ending in `.schema.json`
- **Cross-refs:** use `$ref` with relative paths (`../common/privacy-tier.schema.json`)

## Code generation

Run `./scripts/dev/regenerate-schemas.sh` to regenerate language bindings:

- **Python:** `shared/lib-python/src/sentihome_shared/generated/` (via `datamodel-code-generator` → pydantic models)
- **TypeScript:** `shared/lib-typescript/src/generated/` (via `json-schema-to-typescript` → TS interfaces)

CI verifies generated artifacts are in sync with the schemas; drift fails the build.

## Adding a schema

1. Drop the `.schema.json` file in the appropriate subfolder
2. Run `./scripts/dev/regenerate-schemas.sh`
3. Commit both the schema and the regenerated artifacts
4. Reference from event/message payloads via `$ref`
