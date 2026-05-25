"""sentihome_memory — five memory layers backing the SentiHome runtime.

SQL (sessions, rules, identity, audit, spatial) + Vector DB (embeddings) +
object store (frames, clips, montages). Exposes the `memory.*` MCP contract
that the rest of the system consumes.

See: docs/architecture/11-memory-model.md, docs/architecture/12-recognition-and-identity.md
"""

from __future__ import annotations

__version__ = "0.1.0"

from sentihome_memory.models import (
    AuditLog,
    Base,
    CameraRecord,
    CloudEgressAudit,
    EpisodicSummary,
    IdentityRecord,
    KnownActor,
    RuleRecord,
    Session,
    SessionSegment,
    VisitLedger,
    ZoneRecord,
)
from sentihome_memory.retention import (
    DataClass,
    RetentionPolicy,
    SoftDeleteGracePeriod,
)
from sentihome_memory.store import MemoryStore, MemoryStoreConfig

__all__ = [
    "AuditLog",
    "Base",
    "CameraRecord",
    "CloudEgressAudit",
    "DataClass",
    "EpisodicSummary",
    "IdentityRecord",
    "KnownActor",
    "MemoryStore",
    "MemoryStoreConfig",
    "RetentionPolicy",
    "RuleRecord",
    "Session",
    "SessionSegment",
    "SoftDeleteGracePeriod",
    "VisitLedger",
    "ZoneRecord",
    "__version__",
]
