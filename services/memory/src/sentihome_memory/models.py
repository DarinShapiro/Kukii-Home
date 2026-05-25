"""SQLAlchemy ORM models for the five memory layers.

Maps cleanly to the architecture sections:

- :class:`Session`, :class:`SessionSegment` — §11 session memory
- :class:`RuleRecord` — §10 rule registry
- :class:`KnownActor`, :class:`IdentityRecord` — §12 identity gallery
- :class:`VisitLedger`, :class:`EpisodicSummary` — §11 episodic memory
- :class:`AuditLog`, :class:`CloudEgressAudit` — §16 audit
- :class:`CameraRecord`, :class:`AreaRecord`, :class:`ZoneRecord` — §13 spatial

All tables include ``created_at`` / ``updated_at`` for time-series queries and
``deleted_at`` for soft-delete (§16 right-to-forget).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Common base for all ORM models."""


# ─────────────────────────────────────────────────────────────────────
# Common mixins (composed via inheritance below)
# ─────────────────────────────────────────────────────────────────────


class _Timestamps:
    """Adds created_at + updated_at; subclass via mixin pattern."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.utcnow(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
        nullable=False,
    )


class _SoftDelete:
    """Adds deleted_at for soft-delete + retention enforcement (§16)."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


# ─────────────────────────────────────────────────────────────────────
# Session memory (§11)
# ─────────────────────────────────────────────────────────────────────


class Session(Base, _Timestamps, _SoftDelete):
    """A multi-camera session tracking a subject through one or more areas."""

    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject_descriptor: Mapped[str | None] = mapped_column(Text)
    """Free-form description of the subject (e.g. 'unknown adult, dark jacket')."""

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    """open | closed | abandoned"""

    journey_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    privacy_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    """local_only | cloud_eligible | cloud_any (mirrors event schema)."""

    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)

    segments: Mapped[list[SessionSegment]] = relationship(
        "SessionSegment", back_populates="session", cascade="all, delete-orphan"
    )


class SessionSegment(Base, _Timestamps):
    """One camera's segment within a session."""

    __tablename__ = "session_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), index=True
    )
    camera_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detection_count: Mapped[int] = mapped_column(Integer, default=0)
    clip_ref: Mapped[str | None] = mapped_column(Text)
    """URI in the object store, or null if DVR is down (§19 F3 degradation)."""

    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)

    session: Mapped[Session] = relationship("Session", back_populates="segments")


# ─────────────────────────────────────────────────────────────────────
# Rule registry (§10)
# ─────────────────────────────────────────────────────────────────────


class RuleRecord(Base, _Timestamps, _SoftDelete):
    """A user- or agent-authored rule. See §10 for the schema."""

    __tablename__ = "rules"

    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    """Original natural-language rule text."""

    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    """zone | camera | area | journey | composite | global"""

    scope_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    """The specific area_id / camera_id / zone_id, or null for global scope."""

    temporal: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    """active_hours, active_days, exclusions, ttl"""

    conditions: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    """subject_type, subject_known, location, context_required, etc."""

    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    """info | warning | alert"""

    actions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=list)
    """notify, speak, ask, light_scene, open_session, escalate, ..."""

    confidence_required: Mapped[float] = mapped_column(Float, default=0.5)
    deeper_assessment_if_low: Mapped[bool] = mapped_column(Boolean, default=False)

    created_by: Mapped[str] = mapped_column(String(16), default="user")
    """user | agent | system"""

    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_fired: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismiss_count: Mapped[int] = mapped_column(Integer, default=0)
    dismiss_count_24h: Mapped[int] = mapped_column(Integer, default=0)
    suppress_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edit_count: Mapped[int] = mapped_column(Integer, default=0)

    embedding_id: Mapped[str | None] = mapped_column(String(64))
    """Foreign key into the vector DB (rules collection)."""


# ─────────────────────────────────────────────────────────────────────
# Identity gallery (§12)
# ─────────────────────────────────────────────────────────────────────


class KnownActor(Base, _Timestamps, _SoftDelete):
    """A known person (resident or visitor) in the identity gallery."""

    __tablename__ = "known_actors"

    actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), default="person")
    """person | pet | vehicle"""

    is_resident: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    privacy_level: Mapped[str] = mapped_column(String(16), default="medium")
    """very_high | high | medium | low — controls per-resident consent (§16)."""

    enrollment_status: Mapped[str] = mapped_column(String(16), default="candidate")
    """candidate | promoted | confirmed | deprecated"""

    primary_face_embedding_id: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)

    identity_records: Mapped[list[IdentityRecord]] = relationship(
        "IdentityRecord", back_populates="actor", cascade="all, delete-orphan"
    )


class IdentityRecord(Base, _Timestamps):
    """One identity-resolution record: a detection-time decision with candidates + evidence."""

    __tablename__ = "identity_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("known_actors.actor_id", ondelete="SET NULL"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="SET NULL"), index=True
    )
    camera_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    """face | reid | gait | plate | behavioral | composite"""

    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    """Sources, sub-confidences, candidates considered."""

    user_confirmed: Mapped[bool | None] = mapped_column(Boolean)

    actor: Mapped[KnownActor | None] = relationship("KnownActor", back_populates="identity_records")


# ─────────────────────────────────────────────────────────────────────
# Episodic memory (§11)
# ─────────────────────────────────────────────────────────────────────


class VisitLedger(Base, _Timestamps, _SoftDelete):
    """Per-actor per-area visit history."""

    __tablename__ = "visit_ledgers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("known_actors.actor_id", ondelete="SET NULL"), index=True
    )
    area_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    visited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    session_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)


class EpisodicSummary(Base, _Timestamps, _SoftDelete):
    """Summarized record of one closed session, suitable for similarity recall."""

    __tablename__ = "episodic_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), index=True, unique=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_id: Mapped[str | None] = mapped_column(String(64))
    """FK into vector DB (episodic collection)."""

    privacy_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)


# ─────────────────────────────────────────────────────────────────────
# Audit logs (§16)
# ─────────────────────────────────────────────────────────────────────


class AuditLog(Base, _Timestamps):
    """Append-only audit log for rule fires, dismissals, identity decisions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    """rule_fire | rule_dismiss | identity_decision | ..."""

    actor: Mapped[str | None] = mapped_column(String(64))
    """User or system actor that caused the event."""

    subject_id: Mapped[str | None] = mapped_column(String(64), index=True)
    """e.g. rule_id, session_id, actor_id depending on event_type."""

    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(32), index=True)


class CloudEgressAudit(Base, _Timestamps):
    """Records every byte that left the home (§16 cloud egress audit log)."""

    __tablename__ = "cloud_egress_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    data_type: Mapped[str] = mapped_column(String(64), nullable=False)
    """scene_json | frame_crops | episodic_summary | detection | metadata"""

    privacy_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    destination: Mapped[str] = mapped_column(String(128), nullable=False)
    scrubbed: Mapped[bool] = mapped_column(Boolean, default=False)
    scrub_details: Mapped[str | None] = mapped_column(Text)
    initiated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    user_who_approved: Mapped[str | None] = mapped_column(String(64))
    data_retention_days: Mapped[int | None] = mapped_column(Integer)


# ─────────────────────────────────────────────────────────────────────
# Spatial model (§13)
# ─────────────────────────────────────────────────────────────────────


class CameraRecord(Base, _Timestamps, _SoftDelete):
    """Camera entity in the spatial model."""

    __tablename__ = "cameras"

    camera_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="fixed")
    """fixed | ptz | doorbell | mobile"""

    location: Mapped[str | None] = mapped_column(String(64))
    area_id: Mapped[str | None] = mapped_column(String(64), index=True)
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    streams: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    """{ main: rtsp_url, substream: rtsp_url, mobile: ... }"""

    intrinsics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    """Focal length, principal point, distortion model (§14)."""
    extrinsics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    """Rotation + translation from site frame (§14)."""

    health: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)


class ZoneRecord(Base, _Timestamps, _SoftDelete):
    """Image-space or world-space zone within an area (§13)."""

    __tablename__ = "zones"

    zone_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    area_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128))
    zone_type: Mapped[str] = mapped_column(String(16), default="image_space")
    """image_space | world_space | height_aware"""

    polygon: Mapped[list[list[float]] | None] = mapped_column(JSON)
    """For image_space zones, a list of (x, y) image-pixel vertices."""

    world_volume: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    """For world/height-aware zones, ground-plane polygon + height bounds."""

    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)


__all__ = [
    "AuditLog",
    "Base",
    "CameraRecord",
    "CloudEgressAudit",
    "EpisodicSummary",
    "IdentityRecord",
    "KnownActor",
    "RuleRecord",
    "Session",
    "SessionSegment",
    "VisitLedger",
    "ZoneRecord",
]
