"""Drift detection — suggest re-classification when guidance entries
stop earning their placement (Part X §39 backstop #3).

The misclassification backstop the dispatcher relies on. Even with
preview-before-save + reversible re-typing, the LLM will occasionally
place wrong. This sweep catches it after the fact by looking at how
each entry actually behaved:

  - **Rule with 0 fires in 30 days** → suggest *"this rule hasn't
    fired; maybe a Preference?"*. The rule was authored to fire
    explicitly, but the system never matched it — likely the user
    meant it as a soft prior shift, not a hard fire target.

  - **DismissalPolicy with 0 hits in 30 days** → suggest *"this
    suppression hasn't applied; maybe revoke?"*. The pattern the user
    wanted suppressed never recurred — keeping the policy live just
    adds noise to the audit chain.

  - **Stale fire_once TransientIntents** (created >7 days ago and
    never fired) → suggest *"convert to a Rule?"*. The user expected
    the event to happen and it didn't — they may have meant a
    persistent watch instead of a one-shot.

For v1 the sweep runs inline on each /memory page render — cheap,
operates over hundreds of entries at most. A nightly background
worker lands as a follow-up if the cost grows.

Pure functions: hand them the entry sets, they return suggestions.
No store dependencies — easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Threshold windows (in seconds).
RULE_DRIFT_SECONDS = 30 * 86400.0  # 30 days
DISMISSAL_DRIFT_SECONDS = 30 * 86400.0  # 30 days
TI_STALE_SECONDS = 7 * 86400.0  # 7 days


@dataclass
class DriftSuggestion:
    """One suggestion surfaced on /memory. The route handler renders
    these as a banner at the top of the affected context group."""

    guidance_id: str
    kind: str  # 'rule' | 'dismissal' | 'transient_intent'
    name: str  # the entry's display name
    summary: str  # one-sentence rationale shown to the user
    recommended_action: str  # 'convert_to_preference' | 'revoke' | 'convert_to_rule'


# ─── Detectors ────────────────────────────────────────────────────


def detect_stale_rules(
    rules: list[Any],
    *,
    now_ts: float,
) -> list[DriftSuggestion]:
    """Rules created more than RULE_DRIFT_SECONDS ago that have never
    matched (or whose last match was more than the window ago)."""
    out: list[DriftSuggestion] = []
    cutoff = now_ts - RULE_DRIFT_SECONDS
    for r in rules:
        created_at = float(getattr(r, "created_at", 0.0) or 0.0)
        if created_at == 0.0 or created_at > cutoff:
            continue  # too young to assess
        last = getattr(r, "last_matched_at", None)
        if last is None or float(last) < cutoff:
            out.append(
                DriftSuggestion(
                    guidance_id=r.id,
                    kind="rule",
                    name=getattr(r, "name", r.id),
                    summary=(
                        "This rule hasn't fired in 30+ days — it may have "
                        "been meant as a Preference, not an alert."
                    ),
                    recommended_action="convert_to_preference",
                )
            )
    return out


def detect_stale_dismissals(
    policies: list[Any],
    *,
    now_ts: float,
) -> list[DriftSuggestion]:
    """DismissalPolicies created more than DISMISSAL_DRIFT_SECONDS ago
    that have never applied (or last applied past the window)."""
    out: list[DriftSuggestion] = []
    cutoff = now_ts - DISMISSAL_DRIFT_SECONDS
    for p in policies:
        if getattr(p, "kind", "") != "dismissal":
            continue
        created_at = float(getattr(p, "created_at", 0.0) or 0.0)
        if created_at == 0.0 or created_at > cutoff:
            continue
        last = getattr(p, "last_applied_at", None)
        if last is None or float(last) < cutoff:
            out.append(
                DriftSuggestion(
                    guidance_id=p.id,
                    kind="dismissal",
                    name=getattr(p, "name", p.id),
                    summary=(
                        "This dismissal hasn't applied in 30+ days — the "
                        "pattern hasn't recurred. Consider revoking."
                    ),
                    recommended_action="revoke",
                )
            )
    return out


def detect_stale_transient_intents(
    policies: list[Any],
    *,
    now_ts: float,
) -> list[DriftSuggestion]:
    """fire_once TransientIntents created more than TI_STALE_SECONDS ago
    that have never fired — the watched event didn't happen, suggest
    converting to a persistent Rule."""
    out: list[DriftSuggestion] = []
    cutoff = now_ts - TI_STALE_SECONDS
    for p in policies:
        if getattr(p, "kind", "") != "transient_intent":
            continue
        descriptor = getattr(p, "descriptor", None) or {}
        if not descriptor.get("fire_once"):
            continue
        created_at = float(getattr(p, "created_at", 0.0) or 0.0)
        if created_at == 0.0 or created_at > cutoff:
            continue
        if getattr(p, "apply_count", 0) > 0:
            continue
        out.append(
            DriftSuggestion(
                guidance_id=p.id,
                kind="transient_intent",
                name=getattr(p, "name", p.id),
                summary=(
                    "This one-shot watch was set 7+ days ago and never "
                    "fired. Consider converting to a persistent Rule."
                ),
                recommended_action="convert_to_rule",
            )
        )
    return out


def detect_all_drift(
    *,
    rules: list[Any],
    policies: list[Any],
    now_ts: float,
) -> list[DriftSuggestion]:
    """Run all three detectors. Returns a single flat list — the route
    handler groups them by recommended_action for display."""
    return (
        detect_stale_rules(rules, now_ts=now_ts)
        + detect_stale_dismissals(policies, now_ts=now_ts)
        + detect_stale_transient_intents(policies, now_ts=now_ts)
    )
