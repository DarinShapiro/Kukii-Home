"""kukiihome.ha-agent — bidirectional HA bridge.

Architecture: docs/architecture/07-tool-layer-mcp.md.
Epic 09 (#134).
"""

from kukiihome_ha_agent.area_resolver import AreaRegistry, AreaResources
from kukiihome_ha_agent.client import (
    HAClient,
    HAClientError,
    HAClientSettings,
    HAState,
)
from kukiihome_ha_agent.config import HAAgentSettings
from kukiihome_ha_agent.http_api import AlertLog, HAAgentAPI, make_ha_caller
from kukiihome_ha_agent.mcp_tools import (
    CAPABILITY_DOMAINS,
    CapabilitySummary,
    ChangedEntity,
    HACameraDiscovery,
    HACameraEntity,
    HATools,
)

__version__ = "0.1.0"

__all__ = [
    "CAPABILITY_DOMAINS",
    "AlertLog",
    "AreaRegistry",
    "AreaResources",
    "CapabilitySummary",
    "ChangedEntity",
    "HAAgentAPI",
    "HAAgentSettings",
    "HACameraDiscovery",
    "HACameraEntity",
    "HAClient",
    "HAClientError",
    "HAClientSettings",
    "HAState",
    "HATools",
    "make_ha_caller",
]
