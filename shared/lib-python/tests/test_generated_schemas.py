"""Sanity tests for schema-generated pydantic models.

If these break after `./scripts/dev/regenerate-schemas.sh`, either the schema
changed shape (intentional, update the test) or the codegen is broken (fix it).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


def test_privacy_tier_enum() -> None:
    from sentihome_shared.generated.common.privacy_tier import PrivacyTier

    assert PrivacyTier.local_only.value == "local_only"
    assert PrivacyTier.cloud_eligible.value == "cloud_eligible"
    assert PrivacyTier.cloud_any.value == "cloud_any"


def test_severity_enum() -> None:
    from sentihome_shared.generated.common.severity import Severity

    assert {s.value for s in Severity} == {"info", "warning", "alert"}


def test_trigger_event_validates() -> None:
    from sentihome_shared.generated.events.trigger_event import (
        EventType,
        PrivacyTier,
        Source,
        TriggerEvent,
    )

    event = TriggerEvent(
        event_id="01HXY3ABCDEFGHJKMNPQRSTUVW",
        trace_id="a" * 32,
        source=Source.adapter_rtsp_direct,
        timestamp=datetime.now(UTC),
        camera_id="front_door",
        event_type=EventType.person,
        privacy_tier=PrivacyTier.cloud_eligible,
        retention_days=30,
    )
    assert event.event_id == "01HXY3ABCDEFGHJKMNPQRSTUVW"
    assert event.privacy_tier == PrivacyTier.cloud_eligible


def test_trigger_event_rejects_extras() -> None:
    from pydantic import ValidationError
    from sentihome_shared.generated.events.trigger_event import (
        PrivacyTier,
        Source,
        TriggerEvent,
    )

    with pytest.raises(ValidationError):
        TriggerEvent(
            event_id="x",
            source=Source.synthetic,
            timestamp=datetime.now(UTC),
            camera_id="c",
            privacy_tier=PrivacyTier.local_only,
            unexpected_field="boom",  # type: ignore[call-arg]
        )


def test_trigger_event_requires_event_id_non_empty() -> None:
    from pydantic import ValidationError
    from sentihome_shared.generated.events.trigger_event import (
        PrivacyTier,
        Source,
        TriggerEvent,
    )

    with pytest.raises(ValidationError):
        TriggerEvent(
            event_id="",  # min_length=1 violated
            source=Source.synthetic,
            timestamp=datetime.now(UTC),
            camera_id="c",
            privacy_tier=PrivacyTier.local_only,
        )
