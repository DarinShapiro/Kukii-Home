"""Activity page — Part IV (depth + filters).

Home shows the N most recent incidents (Part III). This page is the same
stream with depth: full chronological list, filter chips (passive vs action,
camera, kind), and "Load earlier" pagination. Shares the row schema with
home so each row reads the same wherever it appears.

The filter contract is **default-on** per Part III §17 — visibility builds
trust, the user toggles things *off* if they want a focused view; nothing
is hidden by default. Filters reset on page visit (not sticky across
sessions) — a fresh visit shouldn't surface a forgotten 3-day-old filter.
"""

from __future__ import annotations

from kukiihome_ha_agent.web_ui.home import (
    _alert_is_action,
    _alert_when_ts,
    _render_activity_row,
)
from kukiihome_ha_agent.web_ui.shell import _e

# Page-default for "show this many"; "Load earlier" pages add this many more.
DEFAULT_PAGE_SIZE = 30


def _kinds_present(alerts: list[dict]) -> list[str]:
    """Distinct detection kinds across the alert set, sorted for stable UI."""
    return sorted({(a.get("kind") or "").strip().lower() for a in alerts if a.get("kind")})


def _cameras_present(alerts: list[dict]) -> list[tuple[str, str]]:
    """``(camera_id, display_name)`` pairs for cameras seen in the alert set —
    used to populate the camera filter dropdown. Falls back to the slug as
    display name when no friendly name is available."""
    from kukiihome_ha_agent.web_ui.shell import camera_display_name

    seen: dict[str, str] = {}
    for a in alerts:
        slug = a.get("camera_id")
        if not slug or slug in seen:
            continue
        friendly = camera_display_name(a.get("camera_friendly_name") or a.get("camera_name"))
        seen[slug] = friendly or slug
    return sorted(seen.items(), key=lambda kv: kv[1].lower())


def _apply_filters(
    alerts: list[dict],
    *,
    show_passive: bool,
    show_actions: bool,
    cameras: set[str],
    kinds: set[str],
) -> list[dict]:
    """Pure filter pass. Empty ``cameras`` / ``kinds`` sets mean *no filter*
    on that axis (matches the default visit state)."""
    out: list[dict] = []
    for a in alerts:
        is_action = _alert_is_action(a)
        if is_action and not show_actions:
            continue
        if (not is_action) and not show_passive:
            continue
        if cameras and (a.get("camera_id") or "") not in cameras:
            continue
        if kinds and (a.get("kind") or "").strip().lower() not in kinds:
            continue
        out.append(a)
    return out


def _filter_form(
    *,
    show_passive: bool,
    show_actions: bool,
    cameras_present: list[tuple[str, str]],
    kinds_present: list[str],
    selected_cameras: set[str],
    selected_kinds: set[str],
) -> str:
    """The filter chip strip at the top of the stream. Form submits as GET so
    the URL reflects the current filter state (shareable, browser-back works)."""
    cam_opts = "".join(
        f"<option value='{_e(slug)}' "
        f"{'selected' if slug in selected_cameras else ''}>{_e(name)}</option>"
        for slug, name in cameras_present
    )
    kind_opts = "".join(
        f"<option value='{_e(k)}' "
        f"{'selected' if k in selected_kinds else ''}>{_e(k.capitalize())}</option>"
        for k in kinds_present
    )
    cam_select = (
        f"<select name='cam' multiple size='1'>{cam_opts}</select>" if cameras_present else ""
    )
    kind_select = (
        f"<select name='kind' multiple size='1'>{kind_opts}</select>" if kinds_present else ""
    )
    return (
        "<form class='filters' method='get'>"
        f"<label><input type='checkbox' name='passive' value='1' "
        f"{'checked' if show_passive else ''}> passive</label>"
        f"<label><input type='checkbox' name='actions' value='1' "
        f"{'checked' if show_actions else ''}> actions</label>"
        + (f"<label>Camera: {cam_select}</label>" if cam_select else "")
        + (f"<label>Kind: {kind_select}</label>" if kind_select else "")
        + "<button type='submit'>Apply</button>"
        + "<a class='clear' href='activity'>clear</a>"
        + "</form>"
    )


# ─── Page assembly ─────────────────────────────────────────────────


def render_activity_page(
    *,
    alerts_all: list[dict],
    now_ts: float,
    show_passive: bool,
    show_actions: bool,
    cameras: set[str],
    kinds: set[str],
    page_size: int = DEFAULT_PAGE_SIZE,
    page: int = 0,
) -> str:
    """Render the full activity stream. ``page`` is zero-indexed; each step
    shows ``page_size`` rows from the chronological tail."""
    sorted_all = sorted(alerts_all, key=_alert_when_ts, reverse=True)
    filtered = _apply_filters(
        sorted_all,
        show_passive=show_passive,
        show_actions=show_actions,
        cameras=cameras,
        kinds=kinds,
    )

    total = len(filtered)
    end = (page + 1) * page_size
    visible = filtered[:end]

    rows_html = (
        "".join(_render_activity_row(a, now_ts=now_ts) for a in visible)
        if visible
        else "<div class='empty'>No activity matches the current filters.</div>"
    )

    n_action = sum(1 for a in filtered if _alert_is_action(a))
    n_passive = total - n_action
    counts_line = (
        f"<div class='trust-line'>Showing {len(visible)} of {total} · "
        f"{n_action} action{'s' if n_action != 1 else ''} · "
        f"{n_passive} passive — system is reasoning.</div>"
    )

    # Pagination: build a "?page=N+1" link preserving current filters.
    more_link = ""
    if end < total:
        # Round-trip current filter state through query params
        params = []
        if show_passive:
            params.append("passive=1")
        if show_actions:
            params.append("actions=1")
        for c in cameras:
            params.append(f"cam={_e(c)}")
        for k in kinds:
            params.append(f"kind={_e(k)}")
        params.append(f"page={page + 1}")
        qs = "&".join(params)
        more_link = f"<div class='trust-line'><a href='activity?{qs}'>↓ Load earlier</a></div>"

    return (
        "<h1>Activity</h1>"
        "<div class='sub'>Every reasoned incident the cameras produced — "
        "actions on top of passives in one stream, filter as needed.</div>"
        + _filter_form(
            show_passive=show_passive,
            show_actions=show_actions,
            cameras_present=_cameras_present(sorted_all),
            kinds_present=_kinds_present(sorted_all),
            selected_cameras=cameras,
            selected_kinds=kinds,
        )
        + rows_html
        + counts_line
        + more_link
    )


# ─── Query-string parsing (route-handler helper) ───────────────────


def parse_filters(query: dict) -> dict:
    """Build the kwargs render_activity_page expects from a request's query
    dict (aiohttp's ``request.rel_url.query`` or similar). Both filter
    checkboxes default ON when there are no query params at all (fresh
    visit), but when the user has explicitly submitted a filter form
    (any of the four query keys is present), only ticked boxes count as on."""
    has_any_explicit = any(k in query for k in ("passive", "actions", "cam", "kind", "page"))
    if has_any_explicit:
        show_passive = "1" in _multi(query, "passive")
        show_actions = "1" in _multi(query, "actions")
    else:
        show_passive = True
        show_actions = True
    cameras = set(_multi(query, "cam")) - {""}
    kinds = set(_multi(query, "kind")) - {""}
    try:
        page = int(query.get("page", 0))
    except (TypeError, ValueError):
        page = 0
    page = max(0, page)
    return {
        "show_passive": show_passive,
        "show_actions": show_actions,
        "cameras": cameras,
        "kinds": kinds,
        "page": page,
    }


def _multi(query: dict, key: str) -> list[str]:
    """aiohttp's MultiDict supports getall(); plain dict only has get(). Handle
    both so this helper is testable without spinning up aiohttp."""
    if hasattr(query, "getall"):
        return list(query.getall(key, []))
    val = query.get(key)
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]
