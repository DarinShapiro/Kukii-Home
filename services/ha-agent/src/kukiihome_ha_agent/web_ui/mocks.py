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


# NOTE: render_areas_page() removed in Iter 2.C — /areas is now a live
# page served from kukiihome_ha_agent.web_ui.areas. See planning/
# web-ui-design.md Part V + AreaStore in area_store.py.


# NOTE: render_intent_page() removed in Task 9 — /intent is now a live page
# served from kukiihome_ha_agent.web_ui.intent.render_intent_page (with
# real rules from RulesStore). See planning/web-ui-iteration-1.md Task 9.


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


# NOTE: render_cameras_page() removed in Iter 2.B — /cameras is now a live
# page served from kukiihome_ha_agent.web_ui.cameras.render_cameras_list +
# render_camera_detail. See planning/web-ui-design.md Part II.


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
