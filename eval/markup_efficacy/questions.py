"""The question battery the harness runs against every (fixture, variant) pair.

Each question has:
* A short, stable ``id`` so cache keys stay stable across re-runs.
* The actual ``prompt`` sent to the VLM, with placeholders for the
  per-fixture context (known actors, etc.).
* A ``category`` so the report can roll up by reasoning type.
* A ``ground_truth_key`` naming the field on the fixture's ground-truth
  YAML that the scorer should compare the VLM's answer against.

The five categories chosen for v0:
* identity      — can the VLM correctly identify a known actor in the scene?
* anomaly       — can the VLM flag an unknown entity alongside known ones?
* counting      — can the VLM count generic objects (vehicle test for the
                  "is YOLO redundant?" hypothesis)?
* behavior      — can the VLM describe what's happening?
* alert_tier    — given the scene, does the VLM agree with the
                  end-to-end alert-tier ground truth?

This list is small on purpose. The point of v0 is signal on whether
pixel-burned markup helps — not a comprehensive eval. Each question
either adds new signal or drops out.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    prompt: str
    ground_truth_key: str


QUESTION_BATTERY: tuple[Question, ...] = (
    Question(
        id="identity_known_present",
        category="identity",
        prompt=(
            "Looking at this camera frame, is any of these specific people "
            "or animals visible: {known_actor_names}? "
            "Respond with a single line: 'YES, <name>' if you see one of "
            "the named actors, 'NO' if none of them are present, or "
            "'UNCERTAIN' if the image is ambiguous."
        ),
        ground_truth_key="identity_present",
    ),
    Question(
        id="anomaly_unknown_alongside_known",
        category="anomaly",
        prompt=(
            "Looking at this camera frame, is there any person, animal, or "
            "vehicle visible that is NOT one of these known entities: "
            "{known_actor_names}? "
            "Respond with a single line: 'YES, <brief description>' or 'NO'."
        ),
        ground_truth_key="anomaly_present",
    ),
    Question(
        id="counting_vehicles",
        category="counting",
        prompt=(
            "Looking at this camera frame, how many vehicles (cars, "
            "trucks, motorcycles) are visible? Respond with a single "
            "integer, nothing else."
        ),
        ground_truth_key="vehicle_count",
    ),
    Question(
        id="behavior_summary",
        category="behavior",
        prompt=(
            "Looking at this camera frame, describe in one short sentence "
            "what is happening in the scene. Focus on observable activity, "
            "not speculation."
        ),
        ground_truth_key="behavior_summary",
    ),
    Question(
        id="alert_tier",
        category="alert_tier",
        prompt=(
            "Looking at this camera frame (from camera {camera_id}), and "
            "knowing the household's known actors are {known_actor_names}, "
            "would this scene warrant an alert? Respond with one of: "
            "'TIER_0' (no alert — routine), "
            "'TIER_1' (notify, low urgency), "
            "'TIER_2' (high urgency — possible security concern). "
            "Just the tier label, nothing else."
        ),
        ground_truth_key="alert_tier",
    ),
)


def render_prompt(q: Question, *, camera_id: str, known_actor_names: list[str]) -> str:
    """Fill in the per-fixture placeholders in the question prompt."""
    actors_str = ", ".join(known_actor_names) if known_actor_names else "(none)"
    return q.prompt.format(camera_id=camera_id, known_actor_names=actors_str)
