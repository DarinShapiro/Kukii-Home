"""MCP (Model Context Protocol) server/client helpers.

Thin wrappers for declaring MCP tools with consistent schema validation,
auth scope checking, and timeout enforcement. Full implementation lands as
each MCP server is built (services/ha-agent, services/memory, etc.).

For now, this module exposes the protocol-shape types that other modules
can import without pulling in a specific MCP implementation.
"""

from __future__ import annotations

from typing import Any, Protocol


class MCPTool(Protocol):
    """Protocol that every MCP tool definition satisfies."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_scopes: list[str]


class MCPError(Exception):
    """Base class for MCP-level errors (policy gate, timeout, schema)."""

    def __init__(self, code: str, message: str, *, suggest: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.suggest = suggest

    def to_dict(self) -> dict[str, str]:
        out = {"error": self.code, "message": str(self)}
        if self.suggest is not None:
            out["suggest"] = self.suggest
        return out


class PolicyGateError(MCPError):
    """Raised when a tool call is blocked by the autonomous action policy (§15)."""

    def __init__(self, action: str, reason: str, suggest: str = "ask") -> None:
        super().__init__("policy_gate", reason, suggest=suggest)
        self.action = action
