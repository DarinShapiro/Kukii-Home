"""kukiihome_memory — five memory layers backing the Kukii-Home runtime.

SQL (sessions, rules, identity, audit, spatial) + Vector DB (embeddings) +
object store (frames, clips, montages). Exposes the `memory.*` MCP contract
that the rest of the system consumes.

See: docs/architecture/11-memory-model.md, docs/architecture/12-recognition-and-identity.md
"""

from __future__ import annotations

__version__ = "0.1.0"

# The SQL/vector memory-store layer (models, store) depends on sqlalchemy +
# asyncpg + qdrant-client. The graph layer (kukiihome_memory.graph) depends
# only on the neo4j driver. Consumers that want ONLY the graph layer — e.g.
# the ha-agent add-on, which mirrors events/policies into Neo4j but has no
# use for the Postgres/Qdrant store — must be able to
# ``from kukiihome_memory.graph import ...`` WITHOUT the heavy SQL deps
# installed. Importing any submodule runs this package __init__ first, so
# the eager SQL-layer imports below are guarded: if sqlalchemy et al. aren't
# present, the SQL symbols simply aren't re-exported and the graph layer
# still imports cleanly. ``retention`` is pure-stdlib and always loads.
from kukiihome_memory.retention import (
    DataClass,
    RetentionPolicy,
    SoftDeleteGracePeriod,
)

__all__ = [
    "DataClass",
    "RetentionPolicy",
    "SoftDeleteGracePeriod",
    "__version__",
]

try:  # SQL layer — optional at import time (see comment above).
    from kukiihome_memory.models import (
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
    from kukiihome_memory.store import MemoryStore, MemoryStoreConfig
except ImportError:
    # sqlalchemy/asyncpg/qdrant not installed (graph-only consumer). The
    # graph layer remains fully importable; only the SQL symbols are absent.
    pass
else:
    __all__ += [
        "AuditLog",
        "Base",
        "CameraRecord",
        "CloudEgressAudit",
        "EpisodicSummary",
        "IdentityRecord",
        "KnownActor",
        "MemoryStore",
        "MemoryStoreConfig",
        "RuleRecord",
        "Session",
        "SessionSegment",
        "VisitLedger",
        "ZoneRecord",
    ]
