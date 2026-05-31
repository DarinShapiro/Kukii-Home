"""Retention policy + soft-delete grace period enforcement.

Maps §16 data classes (A-D) to TTLs. The nightly retention job uses these to:
1. Soft-delete records past their TTL
2. Hard-delete records past the soft-delete grace period
3. Track cloud egress retention separately

Tested via pure functions; the actual sweep job lives in the store module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class DataClass(StrEnum):
    """§16 data classes — drive retention policy."""

    A_RESIDENT_BIOMETRIC = "A_resident_biometric"
    """Resident faces, voice, identity embeddings. Indefinite, local-only."""

    B_INTERIOR = "B_interior"
    """Interior frames, visitor patterns. 14 days default."""

    C_EXTERIOR = "C_exterior"
    """Exterior frames, unknown visitor faces, plates. 30 days default."""

    D_DETECTION = "D_detection"
    """Detection JSON, aggregates. 90 days default; indefinite cloud."""


@dataclass(frozen=True)
class RetentionPolicy:
    """Per data-class retention configuration."""

    data_class: DataClass
    local_ttl_days: int | None
    """None = indefinite."""
    cloud_ttl_days: int | None
    soft_delete_grace_days: int = 7

    @classmethod
    def default_for(cls, data_class: DataClass) -> RetentionPolicy:
        """Return the §16 default policy for a data class."""
        defaults = {
            DataClass.A_RESIDENT_BIOMETRIC: cls(
                data_class=data_class, local_ttl_days=None, cloud_ttl_days=None
            ),
            DataClass.B_INTERIOR: cls(
                data_class=data_class, local_ttl_days=14, cloud_ttl_days=None
            ),
            DataClass.C_EXTERIOR: cls(data_class=data_class, local_ttl_days=30, cloud_ttl_days=90),
            DataClass.D_DETECTION: cls(
                data_class=data_class, local_ttl_days=90, cloud_ttl_days=None
            ),
        }
        return defaults[data_class]

    def is_expired(self, created_at: datetime, *, now: datetime | None = None) -> bool:
        if self.local_ttl_days is None:
            return False
        cutoff = (now or datetime.utcnow()) - timedelta(days=self.local_ttl_days)
        return created_at < cutoff


@dataclass(frozen=True)
class SoftDeleteGracePeriod:
    """Right-to-forget soft-delete grace window (§16)."""

    days: int = 7

    def is_past_grace(self, deleted_at: datetime, *, now: datetime | None = None) -> bool:
        """True if the soft-deleted record can be hard-erased."""
        cutoff = (now or datetime.utcnow()) - timedelta(days=self.days)
        return deleted_at < cutoff
