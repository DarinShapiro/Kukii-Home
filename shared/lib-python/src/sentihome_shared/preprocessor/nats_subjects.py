"""Canonical NATS subject names for preprocessor traffic.

The preprocessor's primary surface is REQUEST/RESPONSE (REST), not
broadcast. NATS is only used for one-to-many CONFIG STATE — the
memory service broadcasts KnownActor changes and the preprocessor
subscribes so its identity cache stays fresh.

These strings are the single source of truth — both sides import the
constants; no one hardcodes the topic anywhere.

Subject naming follows the rest of the bus conventions
(see ``sentihome_shared.bus``):
``sentihome.<domain>.<noun>.<verb>``.
"""

from __future__ import annotations

# ─── Inbound to preprocessor (memory → preprocessor) ────────────────

SUBJECT_ACTOR_ENROLLED = "sentihome.memory.actor.enrolled"
"""Published by memory service when a new KnownActor is enrolled.
Carries the full embedding so the preprocessor can match without
an extra round-trip."""

SUBJECT_ACTOR_UPDATED = "sentihome.memory.actor.updated"
"""Published when an existing KnownActor's profile changes —
embedding refresh, name change, access_profile update."""

SUBJECT_ACTOR_DEACTIVATED = "sentihome.memory.actor.deactivated"
"""Published when a KnownActor is removed from the active roster.
The preprocessor drops them from its cache; future detections of
that face/pet/plate fall through to 'unknown'."""

# Convenience tuple for "subscribe to everything that touches the
# actor cache."
ALL_ACTOR_SUBJECTS = (
    SUBJECT_ACTOR_ENROLLED,
    SUBJECT_ACTOR_UPDATED,
    SUBJECT_ACTOR_DEACTIVATED,
)


# ─── Camera config (ha-agent → preprocessor) ────────────────────────

SUBJECT_CAMERA_CONFIGURED = "sentihome.ha.camera.configured"
"""Published by ha-agent when a camera becomes ready for capture —
newly discovered, stream URL refreshed, user (re)enabled.
Preprocessor starts (or restarts) a CameraCaptureTask for it."""

SUBJECT_CAMERA_REMOVED = "sentihome.ha.camera.removed"
"""Published by ha-agent when a camera is no longer eligible —
user-disabled in the override UI, deleted from HA, etc.
Preprocessor stops the corresponding CameraCaptureTask."""

ALL_CAMERA_SUBJECTS = (SUBJECT_CAMERA_CONFIGURED, SUBJECT_CAMERA_REMOVED)
