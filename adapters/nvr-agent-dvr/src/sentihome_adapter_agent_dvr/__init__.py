"""sentihome-adapter-agent-dvr — Agent DVR (iSpy Connect) adapter.

Service mode in v1: SentiHome's preprocessor consumes RTSP from Agent DVR.
Native mode (in-process plugin) planned for v2+ (see §03.5).

API reference: https://ispysoftware.github.io/Agent_API/
"""

from __future__ import annotations

__version__ = "0.1.0"

from sentihome_adapter_agent_dvr.adapter import AgentDVRAdapter
from sentihome_adapter_agent_dvr.client import AgentDVRClient, AgentDVRClientError, AgentDVRConfig
from sentihome_adapter_agent_dvr.webhook import AgentDVRWebhookReceiver

__all__ = [
    "AgentDVRAdapter",
    "AgentDVRClient",
    "AgentDVRClientError",
    "AgentDVRConfig",
    "AgentDVRWebhookReceiver",
    "__version__",
]
