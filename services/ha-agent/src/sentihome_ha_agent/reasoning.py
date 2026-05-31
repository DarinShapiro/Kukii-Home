"""Reasoning layer — decide whether an event warrants an alert.

This is the seam where the VLM lives. Today no inference box / VLM
backend is running, so :class:`StubReasoner` stands in: it makes a
deterministic decision from whatever evidence we DO have — the
preprocessor's :class:`FrameWindow` when an inference box is configured,
otherwise HA's own AI-sensor classification (person / vehicle / animal /
motion). When a real backend lands, :class:`VlmRouterReasoner` (calling
``sentihome_vlm_router.Router.invoke``) drops in behind the same
:class:`Reasoner` protocol with no change to the triage gate.

Both reasoners return the project's canonical :class:`VLMResponse`
(``sentihome_shared.generated.events.vlm_response``) — the same
structured contract the real VLM emits (§09). The triage gate reads
``criticality`` to decide whether to notify:

  - ``alert``   → notify (and, later, escalate)
  - ``warning`` → notify in-app
  - ``info``    → silent timeline entry, no notification

So "boring" events (known person, animal, rippling-water motion) become
``info`` and never reach the phone, while unknown people become
``alert``. Every event is still recorded — only the *notification* is
gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import structlog
from sentihome_shared.generated.events.vlm_response import (
    Criticality,
    IdentifiedActor,
    VLMResponse,
)
from sentihome_shared.preprocessor import FrameWindow

logger = structlog.get_logger(__name__)

# Criticalities that warrant a notification. info → silent timeline entry.
NOTIFY_CRITICALITIES: frozenset[Criticality] = frozenset({Criticality.alert, Criticality.warning})

# Identity confidence at/above which a preprocessor match counts as a
# *known* actor (so a recognized resident is "boring" → info). Below
# this we treat the person as unknown.
KNOWN_ACTOR_MIN_CONFIDENCE = 0.6


class Reasoner(Protocol):
    """Anything that can turn an event + evidence into a VLM decision.

    The triage gate depends only on this protocol, so swapping the stub
    for the real VLM-router-backed reasoner is a one-line wiring change.
    """

    async def reason(self, alert: dict, evidence: FrameWindow | None) -> VLMResponse:
        """Return a structured decision for ``alert`` given ``evidence``.

        ``evidence`` is the preprocessor's frame window when an inference
        box is configured and reachable, else ``None``.
        """
        ...


@dataclass(frozen=True)
class ReasoningPolicy:
    """Tunable thresholds for :class:`StubReasoner`'s heuristic.

    These are the knobs the real VLM would internalize; exposing them
    here lets the household tune behavior before the VLM exists, and
    documents exactly what "warrants an alert" means today.
    """

    vehicles_warrant_alert: bool = True
    """An unrecognized vehicle → warning (notify). Off → info (silent)."""

    animals_warrant_alert: bool = False
    """An animal with no person → alert. Off (default) → info: pets and
    wildlife are the canonical "boring" case."""

    alert_on_unclassified_motion: bool = False
    """Generic motion with NO person/vehicle/animal signal (e.g. rippling
    water, blowing foliage, lighting changes). Off (default) → info:
    this is the flood we want to suppress. Turn on per-camera only when
    a camera's view genuinely has no better signal and you'd rather be
    noisy than miss something."""


@dataclass
class _Subjects:
    """What the evidence says is in frame, collapsed to a decision-ready
    summary."""

    unknown_persons: int = 0
    known_persons: list[str] = field(default_factory=list)
    vehicles: int = 0
    animals: int = 0
    has_any_classification: bool = False
    """True when we had at least a coarse class (person/vehicle/animal).
    False means 'something moved but we don't know what' — the
    unclassified-motion case."""

    source: str = "ha_sensor"
    """Where the subjects came from: 'preprocessor' (rich evidence) or
    'ha_sensor' (HA's AI classification only)."""

    best_confidence: float = 0.5


def _subjects_from_evidence(alert: dict, evidence: FrameWindow | None) -> _Subjects:
    """Collapse preprocessor evidence (preferred) or HA's sensor
    classification (fallback) into a :class:`_Subjects` summary."""
    # ── Rich path: preprocessor frame window ──────────────────────────
    if evidence is not None and (evidence.identified_entities or evidence.detections):
        s = _Subjects(source="preprocessor", has_any_classification=True)
        confs: list[float] = []

        # Identified entities carry both a class AND an identity. An
        # IdentifiedEntity always has an actor; "known" vs "unknown" is
        # whether the identity confidence clears the threshold.
        identified_persons = 0
        identified_vehicles = 0
        for e in evidence.identified_entities:
            confs.append(e.detection_confidence)
            if e.kind == "person":
                identified_persons += 1
                if e.actor_name and e.identity_confidence >= KNOWN_ACTOR_MIN_CONFIDENCE:
                    s.known_persons.append(e.actor_name)
                else:
                    s.unknown_persons += 1
            elif e.kind == "vehicle":
                identified_vehicles += 1
                s.vehicles += 1
            elif e.kind in ("dog", "cat"):
                s.animals += 1

        # Raw detections are ALL boxes — including the ones that became
        # identified entities. Count raw classes, then subtract what was
        # already identified so we don't double-count a recognized
        # subject's own box. Any surplus raw person/vehicle is an
        # additional UNKNOWN subject (e.g. a stranger standing next to a
        # recognized resident).
        raw_persons = 0
        raw_vehicles = 0
        for d in evidence.detections:
            confs.append(d.confidence)
            kind = d.kind.lower()
            if kind == "person":
                raw_persons += 1
            elif kind in ("car", "truck", "vehicle"):
                raw_vehicles += 1
            elif kind in ("dog", "cat", "bird", "animal"):
                s.animals += 1
        s.unknown_persons += max(raw_persons - identified_persons, 0)
        s.vehicles += max(raw_vehicles - identified_vehicles, 0)

        if confs:
            s.best_confidence = max(confs)
        return s

    # ── Fallback path: HA's own AI sensor classification ──────────────
    kind = (alert.get("sensor_classification") or "").strip().lower()
    s = _Subjects(source="ha_sensor", best_confidence=float(alert.get("confidence") or 0.5))
    if kind == "person":
        s.unknown_persons = 1
        s.has_any_classification = True
    elif kind == "vehicle":
        s.vehicles = 1
        s.has_any_classification = True
    elif kind in ("animal", "dog", "cat", "pet"):
        s.animals = 1
        s.has_any_classification = True
    # "motion", "", "test", or anything else → unclassified: leave all
    # counts at zero and has_any_classification False.
    return s


@dataclass
class StubReasoner:
    """Deterministic stand-in for the VLM.

    Decides ``criticality`` from coarse subject classes. Faithful enough
    to be useful today (unknown person → alert, ripple/animal → silent)
    and shaped exactly like the VLM's output so the real one drops in.
    """

    policy: ReasoningPolicy = field(default_factory=ReasoningPolicy)
    backend_name: str = "stub_heuristic"

    async def reason(self, alert: dict, evidence: FrameWindow | None) -> VLMResponse:
        s = _subjects_from_evidence(alert, evidence)
        camera = alert.get("camera_name") or alert.get("camera_id") or "a camera"
        criticality, explanation = self._decide(s, camera)

        actors = [
            IdentifiedActor(actor_id=None, name=name, confidence=None) for name in s.known_persons
        ]
        # recognition_status mirrors the architecture's tag: how grounded
        # this decision is. Stored on the alert by the gate for the UI.
        recognition_status = "preprocessor" if s.source == "preprocessor" else "ha_sensor_only"
        logger.info(
            "reasoner.decided",
            event_id=alert.get("alert_id"),
            criticality=criticality.value,
            source=s.source,
            unknown_persons=s.unknown_persons,
            known_persons=len(s.known_persons),
            vehicles=s.vehicles,
            animals=s.animals,
            recognition_status=recognition_status,
        )
        return VLMResponse(
            request_id=str(alert.get("alert_id") or "unknown"),
            event_id=alert.get("alert_id"),
            criticality=criticality,
            confidence=round(min(max(s.best_confidence, 0.0), 1.0), 3),
            explanation=explanation,
            identified_actors=actors or None,
            backend=self.backend_name,
        )

    def _decide(self, s: _Subjects, camera: str) -> tuple[Criticality, str]:
        # Order matters: an unknown person dominates everything else.
        if s.unknown_persons > 0:
            who = "person" if s.unknown_persons == 1 else f"{s.unknown_persons} people"
            return Criticality.alert, f"Unknown {who} at {camera}."
        if s.known_persons:
            names = ", ".join(dict.fromkeys(s.known_persons))
            return Criticality.info, f"Known person ({names}) at {camera} — no alert."
        if s.vehicles > 0:
            if self.policy.vehicles_warrant_alert:
                return Criticality.warning, f"Vehicle at {camera}."
            return Criticality.info, f"Vehicle at {camera} — dismissed by policy."
        if s.animals > 0:
            if self.policy.animals_warrant_alert:
                return Criticality.alert, f"Animal at {camera}."
            return Criticality.info, f"Animal at {camera} — no person; dismissed."
        # Nothing classified — generic motion.
        if self.policy.alert_on_unclassified_motion:
            return (
                Criticality.warning,
                f"Unclassified motion at {camera} (no person/vehicle detected).",
            )
        return (
            Criticality.info,
            f"Unclassified motion at {camera} — no person or vehicle detected; "
            "dismissed (e.g. wind, water, lighting).",
        )


def should_notify(response: VLMResponse) -> bool:
    """Whether a VLM decision warrants a notification."""
    return response.criticality in NOTIFY_CRITICALITIES
