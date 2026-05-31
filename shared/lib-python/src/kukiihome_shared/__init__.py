"""kukiihome_shared — shared utilities for all Kukii-Home services.

Reusable primitives that every service needs:

- :mod:`kukiihome_shared.bus`     — async NATS JetStream wrappers
- :mod:`kukiihome_shared.logging` — structured JSON logging with trace IDs
- :mod:`kukiihome_shared.tracing` — OpenTelemetry-flavored trace context
- :mod:`kukiihome_shared.mcp`     — MCP server/client helpers
- :mod:`kukiihome_shared.config`  — environment + YAML config loader

Generated schema types live under :mod:`kukiihome_shared.generated`
(populated by `scripts/dev/regenerate-schemas.sh`).
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
