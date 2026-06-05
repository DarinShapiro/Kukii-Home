"""Rules runtime — scope filtering, shortcut matching, prompt-section build.

The store (``rules_store.py``) is the source of truth. This module is the
hot-path layer triage talks to:

  - Keeps an in-memory cache of active rules so we don't hit SQLite on every
    incident. Refreshes lazily when the store's dirty bit is set.
  - Filters by scope ``(camera, area, ts)`` so rules silently ignore
    incidents outside their declared window.
  - **Deterministically matches shortcut rules** — identity-only patterns
    that don't need the VLM ("Bob seen → critical"). The match runs from
    whatever subjects are already in the alert's evidence.
  - **Builds the NL-rules section of the VLM prompt** when a real VLM lands.
    For now this returns a structured list the triage layer can stash on
    the alert; the live VLM call wires it in once the real reasoner exists.
  - **Parses ``matched_rules`` from a VLM response** — the inverse, when the
    VLM evaluates NL rules and returns per-rule verdicts.

Today only the shortcut path is wired end-to-end. The NL path's
helpers are exported so they can be unit-tested before the VLM lights up;
the live wire-in is one method call in :class:`TriageGate`.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from .rules_store import Rule, RuleMatch, RulesStore, Severity

logger = structlog.get_logger(__name__)


# Match-confidence threshold below which a VLM-claimed match is ignored.
# Global default; per-rule overrides land in iteration 2.
DEFAULT_MATCH_THRESHOLD = 0.6


# ─── Scope evaluator ────────────────────────────────────────────────


_DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _in_time_window(window: dict[str, Any], when: datetime) -> bool:
    """Inclusive day-of-week match + ``start <= clock < end`` (24h) check.

    Day list missing or empty → any day matches. Bad start/end → window
    silently fails to match (so a malformed time window can't accidentally
    *broaden* a rule's scope)."""
    days = [d.lower() for d in window.get("days", []) or []]
    if days and _DAY_NAMES[when.weekday()] not in days:
        return False
    try:
        start = window.get("start", "00:00")
        end = window.get("end", "23:59")
        sh, sm = (int(x) for x in start.split(":", 1))
        eh, em = (int(x) for x in end.split(":", 1))
    except (ValueError, AttributeError):
        return False
    cur = when.hour * 60 + when.minute
    return (sh * 60 + sm) <= cur < (eh * 60 + em)


def rule_in_scope(
    rule: Rule, *, camera_id: str | None, area_id: str | None, ts: float | None
) -> bool:
    """True iff this rule's scope gate allows ``(camera, area, ts)``.

    Empty list on any axis = *any* (no gate). All axes AND-combined.
    """
    if rule.scope.cameras and camera_id not in rule.scope.cameras:
        return False
    if rule.scope.areas and area_id not in rule.scope.areas:
        return False
    if rule.scope.time_windows:
        when = datetime.fromtimestamp(ts) if ts else datetime.now()
        if not any(_in_time_window(w, when) for w in rule.scope.time_windows):
            return False
    return True


# ─── Subject extraction (alert → list of (kind, actor_id) pairs) ────


def subjects_in_alert(alert: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Best-effort extraction of recognized subjects from an alert.

    Reads in order:
      1. ``identified_actors`` (preprocessor-resolved, with actor_id)
      2. ``resolved_actors`` / ``actor_id`` legacy hints
      3. fallback to the alert's classification kind (no actor_id)

    Shortcut rules match on actor_id when present; if the subject is
    classified but unresolved, we still surface the kind so a shortcut rule
    keyed on the kind itself (`shortcut_subject="unknown_person"`) can fire.
    """
    pairs: list[tuple[str, str | None]] = []
    for a in alert.get("identified_actors") or []:
        if isinstance(a, dict):
            pairs.append((str(a.get("kind") or "person"), a.get("actor_id")))
    for a in alert.get("resolved_actors") or []:
        if isinstance(a, dict):
            pairs.append((str(a.get("kind") or "person"), a.get("actor_id")))
    if not pairs:
        kind = (alert.get("sensor_classification") or alert.get("kind") or "").lower()
        if kind:
            pairs.append((kind, None))
    return pairs


# ─── Shortcut matcher ───────────────────────────────────────────────


@dataclass
class ShortcutOutcome:
    """One shortcut-rule firing — the dispatcher reads this list to know
    which alerts to emit and at what severity."""

    rule: Rule
    matched_subject_id: str | None
    severity: Severity


def evaluate_shortcuts(
    rules: list[Rule],
    *,
    alert: dict[str, Any],
    camera_id: str | None,
    area_id: str | None,
    ts: float | None,
) -> list[ShortcutOutcome]:
    """Run shortcut rules deterministically. Returns one outcome per match.

    A shortcut rule matches when:
      - its scope allows ``(camera, area, ts)`` (see :func:`rule_in_scope`)
      - ``shortcut_subject`` equals an alert subject's ``actor_id``
        (preferred) OR equals the subject's kind (fallback for type-keyed
        shortcuts like ``unknown_person``).
    """
    subjects = subjects_in_alert(alert)
    out: list[ShortcutOutcome] = []
    for r in rules:
        if r.mode != "shortcut" or not r.shortcut_subject:
            continue
        if not rule_in_scope(r, camera_id=camera_id, area_id=area_id, ts=ts):
            continue
        for kind, actor_id in subjects:
            if r.shortcut_subject == actor_id or r.shortcut_subject == kind:
                out.append(
                    ShortcutOutcome(
                        rule=r,
                        matched_subject_id=actor_id,
                        severity=r.severity_static or "normal",
                    )
                )
                break  # one match per rule per alert; multi-subject is one fire
    return out


# ─── NL prompt section + response parsing (VLM-bound) ───────────────


def nl_rules_in_scope(
    rules: list[Rule],
    *,
    camera_id: str | None,
    area_id: str | None,
    ts: float | None,
) -> list[Rule]:
    """Subset of NL rules whose scope allows this incident — the input list
    for prompt-section building."""
    return [
        r
        for r in rules
        if r.mode == "nl" and rule_in_scope(r, camera_id=camera_id, area_id=area_id, ts=ts)
    ]


def build_nl_prompt_section(rules: list[Rule]) -> str:
    """Render the *Named user intents* block of the VLM prompt.

    Empty input → empty string (don't pollute the prompt with an empty
    section). Shape is documented in Task 9 §"Triage / VLM prompt
    integration" — one paragraph per rule, instructing the VLM to judge
    match + reason about severity."""
    if not rules:
        return ""
    parts = [
        "Named user intents — for each rule below, judge match (yes/no, "
        "confidence) and reason about severity (critical/normal/low) "
        "given the scene + time-of-day + context:"
    ]
    for r in rules:
        parts.append(f'  [rule:{r.id}] "{r.name}"\n    Intent: {r.intent_text.strip()}')
    return "\n".join(parts)


def parse_matched_rules(
    rules: list[Rule],
    matched_rules_payload: list[dict[str, Any]] | None,
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> list[RuleMatch]:
    """Take ``VLMResponse.matched_rules`` and produce :class:`RuleMatch`
    audit rows. Only entries with ``matched: true`` AND ``confidence >=
    threshold`` count as a real match; everything else is recorded as a
    non-match so the per-rule audit log shows the full evaluation, not
    just the firings."""
    if not matched_rules_payload:
        return []
    rules_by_id = {r.id: r for r in rules}
    out: list[RuleMatch] = []
    now = _time.time()
    for entry in matched_rules_payload:
        rid = entry.get("rule_id")
        if not rid or rid not in rules_by_id:
            continue
        conf = float(entry.get("confidence") or 0.0)
        claimed = bool(entry.get("matched"))
        real_match = claimed and conf >= threshold
        sev = entry.get("severity") if real_match else None
        out.append(
            RuleMatch(
                rule_id=rid,
                incident_id="",  # caller fills incident_id
                matched_at=now,
                severity=sev,
                confidence=conf,
                reasoning=entry.get("reasoning"),
                matched=real_match,
            )
        )
    return out


# ─── Runtime cache ──────────────────────────────────────────────────


class RulesRuntime:
    """In-memory cache over :class:`RulesStore` for the triage hot path.

    Refreshes lazily on :meth:`active_rules` whenever the store's dirty bit
    is set. Cache scope is the *active* rule set (enabled + non-retired) —
    retired rules don't need cache plumbing.
    """

    def __init__(self, store: RulesStore) -> None:
        self.store = store
        self._cache: list[Rule] = []
        self._loaded = False

    def _refresh(self) -> None:
        self._cache = self.store.active_rules()
        self._loaded = True

    def active_rules(self) -> list[Rule]:
        """Cached active rule list. Reloads if the store has been mutated."""
        if not self._loaded or self.store.take_dirty():
            self._refresh()
        return self._cache

    def shortcuts_for(
        self,
        *,
        alert: dict[str, Any],
        camera_id: str | None,
        area_id: str | None,
        ts: float | None,
    ) -> list[ShortcutOutcome]:
        return evaluate_shortcuts(
            self.active_rules(),
            alert=alert,
            camera_id=camera_id,
            area_id=area_id,
            ts=ts,
        )

    def nl_rules_for(
        self,
        *,
        camera_id: str | None,
        area_id: str | None,
        ts: float | None,
    ) -> list[Rule]:
        """In-scope NL rules — fed to VLM prompt assembly."""
        return nl_rules_in_scope(
            self.active_rules(),
            camera_id=camera_id,
            area_id=area_id,
            ts=ts,
        )
