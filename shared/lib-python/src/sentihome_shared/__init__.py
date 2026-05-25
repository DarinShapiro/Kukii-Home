"""sentihome_shared — shared utilities for all SentiHome services.

Reusable primitives that every service needs:

- :mod:`sentihome_shared.bus`     — async NATS JetStream wrappers
- :mod:`sentihome_shared.logging` — structured JSON logging with trace IDs
- :mod:`sentihome_shared.tracing` — OpenTelemetry-flavored trace context
- :mod:`sentihome_shared.mcp`     — MCP server/client helpers
- :mod:`sentihome_shared.config`  — environment + YAML config loader

Generated schema types live under :mod:`sentihome_shared.generated`
(populated by `scripts/dev/regenerate-schemas.sh`).
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
