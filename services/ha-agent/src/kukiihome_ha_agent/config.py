"""HA-agent connection settings sourced from the central topology.

Epic 9 (the real HA-agent implementation) will use this to construct its
WebSocket/REST client. Kept tiny + import-cheap so the topology refactor
(#275) doesn't drag the not-yet-implemented client surface in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class HAAgentSettings:
    """Resolved HA connection settings."""

    ha_url: str
    ha_token: str
    websocket: bool = True

    @classmethod
    def from_topology(cls, topology: Any) -> HAAgentSettings:
        """Build from :class:`kukiihome_shared.topology.HAAgentConfig`."""
        cfg = topology.ha_agent
        if not cfg.ha_token:
            raise ValueError(
                "ha_agent.ha_token is empty — set it in kukiihome.yaml or via "
                "KUKIIHOME__HA_AGENT__HA_TOKEN env var (Supervisor add-on "
                "deployments inject SUPERVISOR_TOKEN automatically)."
            )
        return cls(
            ha_url=cfg.ha_url,
            ha_token=cfg.ha_token,
            websocket=cfg.websocket,
        )
