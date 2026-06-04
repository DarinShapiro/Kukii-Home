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


# NOTE: render_policies_page() removed in Iter 2.D — /policies is now a live
# page from kukiihome_ha_agent.web_ui.policies. See planning/web-ui-design.md
# Part VII + PolicyStore in policy_store.py.


# NOTE: render_cameras_page() removed in Iter 2.B — /cameras is now a live
# page served from kukiihome_ha_agent.web_ui.cameras.render_cameras_list +
# render_camera_detail. See planning/web-ui-design.md Part II.


# NOTE: render_diagnostics_page() removed in Iter 2.E — /diagnostics is
# now a live page from kukiihome_ha_agent.web_ui.diagnostics. See planning/
# web-ui-design.md Part VIII.
