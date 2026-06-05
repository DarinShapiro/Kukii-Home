"""/identities — Review + Enrolled (Part IX §29).

Replaces the standalone Review page. People, pets, and vehicles are
all *identities*; one surface manages all three with two states per
record:

  - **Review** — unresolved tracks awaiting label (the Build #292
    surface). Unchanged behavior; the existing review_page renderer
    feeds this tab.
  - **Enrolled** — labeled actors with full lifecycle management.
    Click through to per-identity detail.

The detail page surfaces what we have today (kind / display_name /
modalities / appearances) plus a "Linked guidance" card pulling
matching rules + policies from /memory.

Operations are constrained to what the preprocessor supports in v1:
merge (existing) is fully wired. *Stop recognizing* renders as a
disabled affordance with a "coming next iteration" tooltip — it
requires a `DELETE /identity/subjects/{id}/embeddings` endpoint that
lands separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kukiihome_ha_agent.web_ui.memory import GuidanceEntry
from kukiihome_ha_agent.web_ui.shell import _e

# ─── View models ────────────────────────────────────────────────────


@dataclass
class IdentitySubject:
    """Flattened view of a preprocessor IdentitySubject row. The route
    handler builds this from the /identity/subjects payload."""

    subject_id: str
    kind: str  # 'person' | 'pet' | 'vehicle' (when added)
    display_name: str
    species: str | None = None
    owner_id: str | None = None
    modalities: list[str] = field(default_factory=list)
    appearances: int = 0


@dataclass
class IdentityDetailViewModel:
    subject: IdentitySubject
    linked_guidance: list[GuidanceEntry] = field(default_factory=list)


# ─── Helpers ────────────────────────────────────────────────────────


_KIND_ICON = {"person": "👤", "pet": "🐾", "vehicle": "🚗"}


def _kind_icon(kind: str) -> str:
    return _KIND_ICON.get(kind, "·")


def _modality_chips(modalities: list[str]) -> str:
    return (
        "".join(f"<span class='chip cap-src ok'>{_e(m)}</span> " for m in modalities)
        or "<span class='muted'>no enrolled templates</span>"
    )


# ─── List page ─────────────────────────────────────────────────────


def render_identities_list(
    subjects: list[IdentitySubject],
    *,
    unresolved_count: int = 0,
    review_url: str = "review",
    tab: str = "enrolled",
) -> str:
    """Top-level list page with Review + Enrolled tabs. ``tab`` is the
    active selection — defaults to Enrolled (the new surface)."""
    review_tab = (
        f"<a class='{'active' if tab == 'review' else ''}' "
        f"href='{_e(review_url)}'>"
        f"Review <span class='muted'>({unresolved_count})</span></a>"
    )
    enrolled_tab = (
        f"<a class='{'active' if tab == 'enrolled' else ''}' "
        "href='identities'>"
        f"Enrolled <span class='muted'>({len(subjects)})</span></a>"
    )

    if not subjects:
        review_cta = (
            (
                f"<div style='margin-top:14px'>"
                f"<a class='btn primary' href='{_e(review_url)}'>"
                f"Review {unresolved_count} unresolved track"
                f"{'s' if unresolved_count != 1 else ''} →</a></div>"
            )
            if unresolved_count > 0
            else ""
        )
        body = (
            "<div class='empty'>No identities enrolled yet. Label an "
            "unresolved track on the Review tab — once you give a track "
            "a name, the subject lands here with its templates and "
            "lifecycle controls.</div>" + review_cta
        )
    else:
        # Sort: people first, then pets, then vehicles; within each by name.
        order = {"person": 0, "pet": 1, "vehicle": 2}
        ordered = sorted(
            subjects,
            key=lambda s: (order.get(s.kind, 99), s.display_name.lower()),
        )
        body = "<div class='cameras-grid'>" + "".join(_subject_tile(s) for s in ordered) + "</div>"

    return (
        "<h1>Identities</h1>"
        "<div class='sub'>People, pets, and vehicles — Review brings "
        "unlabeled tracks into focus; Enrolled is your lifecycle "
        "surface for everyone the system already recognizes.</div>"
        f"<div class='memory-cut'>{review_tab}{enrolled_tab}</div>" + body
    )


def _subject_tile(s: IdentitySubject) -> str:
    sub_label = s.species or s.kind
    appearances = (
        f"{s.appearances} appearance{'s' if s.appearances != 1 else ''}"
        if s.appearances
        else "<span class='muted'>not seen yet</span>"
    )
    return (
        f"<a class='camera-tile' href='identities/{_e(s.subject_id)}'>"
        "<div class='cam-head'>"
        f"<b>{_kind_icon(s.kind)} {_e(s.display_name)}</b>"
        f"<span class='chip cap-src muted'>{_e(sub_label)}</span>"
        "</div>"
        "<div class='cam-meta muted'>" + " ".join(_e(m) for m in s.modalities) + "</div>"
        f"<div class='cam-meta'>{appearances}</div>"
        "</a>"
    )


# ─── Detail page ───────────────────────────────────────────────────


def render_identity_detail(vm: IdentityDetailViewModel) -> str:
    """Per-identity lifecycle surface (Part IX §29)."""
    s = vm.subject
    sub_label = s.species or s.kind

    # At a glance
    at_a_glance = (
        "<section class='card'>"
        "<div class='card-head'>"
        f"<h2>{_kind_icon(s.kind)} {_e(s.display_name)}</h2>"
        f"<span class='chip cap-src muted'>{_e(sub_label)}</span>"
        f"<span class='muted'>{s.appearances} appearance"
        f"{'s' if s.appearances != 1 else ''}</span>"
        "</div>"
        "</section>"
    )

    # Templates / modalities
    templates = (
        "<section class='card'>"
        "<h2>Enrolled templates</h2>"
        "<div class='sub'>The modalities this subject is recognized "
        "by. Each modality is its own embedding pipeline (face / body "
        "/ pet / gait — and vehicle / plate when the pipeline lands).</div>"
        f"<div class='cam-row'>{_modality_chips(s.modalities)}</div>"
        "</section>"
    )

    # Access profile placeholder — wired when commit_guidance handles
    # the 'access_profile' storage class (it currently NotImplemented).
    access_profile = (
        "<section class='card'>"
        "<h2>Access profile</h2>"
        "<div class='sub'>Areas, hours, expected pattern — the guidance "
        "fields the VLM reads when scoring this actor's appearance.</div>"
        "<div class='empty'>Editing the access profile lands when "
        "<code>access_profile</code> commit support is wired in the "
        "dispatcher (Part X §35 deferred).</div>"
        "</section>"
    )

    # Linked guidance
    if vm.linked_guidance:
        linked_html = "".join(
            f"<div class='rule-row'>"
            f"<a href='{_e(e.detail_url)}'><b>{_e(e.name)}</b></a> "
            f"<span class='chip cap-src muted'>{_e(e.storage_class)}</span>"
            f"<div class='rule-meta muted'>{_e(e.scope_summary)}</div>"
            f"</div>"
            for e in vm.linked_guidance
        )
    else:
        linked_html = (
            "<div class='empty'>No rules or policies reference this "
            "identity yet. Open the drawer from /memory to author one.</div>"
        )
    linked = f"<section class='card'><h2>Linked guidance</h2>{linked_html}</section>"

    # Operations
    ops = (
        "<section class='card'>"
        "<h2>Operations</h2>"
        "<div class='sub'>Lifecycle controls. Per the identity-never-"
        "lost-only-corrected principle (Part IX §27), the subject "
        "record persists; <i>Stop recognizing</i> deletes embeddings "
        "but preserves identity + history.</div>"
        "<div class='form-actions' style='justify-content:flex-start'>"
        "<button class='btn' disabled title='Coming when the preprocessor "
        "exposes DELETE /identity/subjects/{id}/embeddings'>"
        "Stop recognizing</button>"
        f"<a class='btn' href='identities?merge_from={_e(s.subject_id)}'>"
        "Correct / merge</a>"
        "</div>"
        "</section>"
    )

    return (
        "<a class='back-link' href='identities'>← All identities</a>"
        + at_a_glance
        + templates
        + access_profile
        + linked
        + ops
    )


# ─── Linked-guidance filter ─────────────────────────────────────────


def filter_guidance_for_subject(
    entries: list[GuidanceEntry],
    *,
    subject: IdentitySubject,
) -> list[GuidanceEntry]:
    """Return guidance entries that reference this subject by id or name.
    Case-insensitive — guidance entries may carry either form."""
    needle_id = subject.subject_id.lower()
    needle_name = subject.display_name.lower()
    out: list[GuidanceEntry] = []
    for e in entries:
        actor = (e.scope_fields.get("actor") or "").lower()
        actor_name = (e.scope_fields.get("actor_name") or "").lower()
        if actor == needle_id or actor == needle_name:
            out.append(e)
            continue
        if actor_name == needle_name:
            out.append(e)
            continue
        # Fall back to substring in intent_text / scope_summary
        if needle_name and needle_name in (e.scope_summary or "").lower():
            out.append(e)
    return out


def build_identity_subjects(api_payload: dict | list | None) -> list[IdentitySubject]:
    """Flatten the preprocessor's /identity/subjects payload into our
    view model. Tolerant of both ``{"subjects": [...]}`` and raw list
    shapes."""
    if api_payload is None:
        return []
    if isinstance(api_payload, dict):
        records = api_payload.get("subjects") or []
    else:
        records = list(api_payload)
    out: list[IdentitySubject] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        out.append(
            IdentitySubject(
                subject_id=str(r.get("subject_id") or ""),
                kind=str(r.get("kind") or "person"),
                display_name=str(r.get("display_name") or r.get("subject_id") or ""),
                species=r.get("species"),
                owner_id=r.get("owner_id"),
                modalities=list(r.get("modalities") or []),
                appearances=int(r.get("appearances") or 0),
            )
        )
    return out


def _get_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    """Both dict-like and dataclass-like access pattern — the call sites
    sometimes hand us plain dicts from a JSON parse + sometimes
    dataclasses from a typed client. Tolerate both."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
