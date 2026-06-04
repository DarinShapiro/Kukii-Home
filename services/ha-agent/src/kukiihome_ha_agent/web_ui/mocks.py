"""Credible skeletons for the tabs that don't have their full ratified
build yet. Each renders a *Coming soon* card explaining what'll go there and
linking out to the relevant ratified design section in
``planning/web-ui-design.md``.

The point is that the nav works end-to-end and the user can click through to
see what's coming — empty pages would feel broken. Each mock here is a few
lines; a real page replaces it module-by-module.
"""

from __future__ import annotations

from kukiihome_ha_agent.web_ui.shell import _e


def _coming_soon(
    title: str, blurb: str, sketch: str | None = None, *, ref: str | None = None
) -> str:
    """One section: title + short description + optional ASCII sketch."""
    sketch_html = f"<div class='sketch'>{_e(sketch)}</div>" if sketch else ""
    ref_html = (
        f"<div class='sub' style='margin-top:14px'>{_e(ref)}</div>"
        if ref else ""
    )
    return (
        "<div class='coming-soon'>"
        f"<h3>{_e(title)}</h3>"
        f"<div class='sub'>{_e(blurb)}</div>"
        f"{sketch_html}{ref_html}"
        "</div>"
    )


def render_activity_page() -> str:
    return (
        "<h1>Activity</h1>"
        "<div class='sub'>The home page already shows the N most recent "
        "incidents. This is where depth + filters live: search, focused "
        "views by camera / person / area, day-picker, export.</div>"
        + _coming_soon(
            "Coming soon",
            "Search across all incidents; refocused views; day-picker; "
            "trace deep-links.",
            ref="planning/web-ui-design.md — Part IV (Activity depth & filters).",
        )
    )


def render_areas_page() -> str:
    return (
        "<h1>Areas</h1>"
        "<div class='sub'>Areas are conceptual zones (Pool, Driveway, Front "
        "porch, Backyard) that group cameras and carry AttentionMode + "
        "normal-hours + role posture. The reasoner uses these to shape its "
        "judgment per area.</div>"
        + _coming_soon(
            "Coming soon",
            "Create + rename areas, assign cameras, set AttentionMode + "
            "normal-hours, view per-area activity.",
            ref="planning/web-ui-design.md — Part V (Areas).",
        )
    )


def render_intent_page() -> str:
    return (
        "<h1>Intent</h1>"
        "<div class='sub'>How you've told the system what you care about. "
        "Preferences shape the reasoner's baseline; Rules attach named, "
        "scoped intents with explicit actions on top. Both are user-written "
        "guidance the VLM reads.</div>"
        + _coming_soon(
            "Preferences",
            "Vigilance dial; 'what I care about' free-text; per-actor "
            "relationship; per-area posture; quiet hours.",
            ref="planning/web-ui-design.md — Part VI (Intent · Preferences).",
        )
        + _coming_soon(
            "Rules",
            "Named scoped intents in natural language that the VLM evaluates "
            "against the situation; structured shortcut for trivial identity "
            "matches; fires HA events for downstream automations.",
            sketch=(
                "WHEN  Front Yard\n"
                "ALERT IF\n"
                "  Winston seems to have gotten outside\n"
                "  without someone watching him.\n"
                "THEN  alert + fire kukiihome.winston_unsupervised"
            ),
            ref="planning/web-ui-design.md — Part VI (Intent · Rules).",
        )
    )


def render_policies_page() -> str:
    return (
        "<h1>Policies</h1>"
        "<div class='sub'>The throttles and overrides the agent has built up "
        "over time. Dismissal policies suppress patterns the system has "
        "learned to ignore; transient intents are temporary watches. Every "
        "policy is viewable, revocable, and shows the incidents it has acted "
        "on.</div>"
        + _coming_soon(
            "Coming soon",
            "List of active dismissals + transient intents; per-policy "
            "rationale + sanity-check countdown; revoke / narrow-scope; "
            "reverse-link from passive activity rows.",
            ref="planning/web-ui-design.md — Part VII (Policies).",
        )
    )


def render_cameras_page() -> str:
    return (
        "<h1>Cameras</h1>"
        "<div class='sub'>Per-camera detail page (Part II, ratified): "
        "identity & role, detection capability matrix with delegate "
        "affordances, privacy posture, tuning, health, active policies. The "
        "page is intentionally <em>not</em> a mini-NVR — that's Agent DVR's "
        "job.</div>"
        + _coming_soon(
            "Coming soon",
            "Per-camera detail with the capability matrix (NATIVE / "
            "AUGMENTED / SUBSTITUTED / DELEGATED / MISSING). Each row "
            "carries the external-dependency triple: link out, re-scan, "
            "drift surfaces to home.",
            ref="planning/web-ui-design.md — Part II (Per-camera detail).",
        )
    )


def render_diagnostics_page(legacy_status_path: str = "/") -> str:
    """Diagnostics — placeholder note + link out to the existing status page
    (which lives at ``/`` for now). Eventually this page hosts the raw
    detection stream + audit edge browser + the dev loop dashboard."""
    return (
        "<h1>Diagnostics</h1>"
        "<div class='sub'>Raw observations + audit + dev loop. Home shows "
        "the <em>reasoned</em> incident stream; this is the <em>observed</em> "
        "stream (per-frame detections, per-track embeddings, per-VLM-call "
        "raw payloads) + the dev loop dashboard.</div>"
        + _coming_soon(
            "Coming soon",
            "Raw detection stream, audit-edge browser (CITED / INFLUENCED / "
            "YIELDED), dev loop dashboard (replay alert, cross-backend diff, "
            "unresolved-feedback queue), trust metrics per camera + per VLM.",
            ref="planning/web-ui-design.md — Part VIII (Diagnostics + dev loop).",
        )
        + (
            "<div class='notice'>For now, the legacy status page (topology, "
            f"capabilities, cameras, logs) lives at <a href='{_e(legacy_status_path)}'>"
            f"{_e(legacy_status_path)}</a>.</div>"
        )
    )
