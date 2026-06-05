"""Depth-aware <base href> in render_shell (user-review fixups #5-8).

Before this fixup, the shell emitted a fixed ``<base href='./'>`` which
broke at depth >= 2: nav links on /cameras/{id}, /areas/new, etc.
would resolve to relative paths under the current page instead of the
application root.
"""

from __future__ import annotations

from kukiihome_ha_agent.web_ui.shell import (
    NAV_ITEMS,
    base_href_for_path,
    render_shell,
)

# ─── base_href_for_path ──────────────────────────────────────────


def test_base_href_root_path_uses_dot_slash():
    """Depth-0 (root) and depth-1 (top-level page) → './' is correct
    since the browser drops the trailing component when computing the
    current directory."""
    assert base_href_for_path("/") == "./"
    assert base_href_for_path("/home") == "./"
    assert base_href_for_path("/areas") == "./"
    assert base_href_for_path("/system") == "./"


def test_base_href_depth_2_uses_dotdot_slash():
    """One level deeper → one ../"""
    assert base_href_for_path("/areas/new") == "../"
    assert base_href_for_path("/cameras/pool") == "../"
    assert base_href_for_path("/identities/bob") == "../"
    assert base_href_for_path("/alert/evt_42") == "../"


def test_base_href_depth_3_chains_dotdot():
    assert base_href_for_path("/areas/pool/edit") == "../../"
    assert base_href_for_path("/cameras/pool/whitelist/perception/new") == "../../../../"


def test_base_href_empty_path_falls_back_to_root():
    assert base_href_for_path("") == "./"
    assert base_href_for_path(None) == "./"


def test_base_href_strips_trailing_slash():
    # /areas/ and /areas should resolve identically — both are depth 1
    assert base_href_for_path("/areas/") == "./"
    assert base_href_for_path("/cameras/pool/") == "../"


# ─── render_shell threads it through ─────────────────────────────


def test_render_shell_uses_root_base_for_top_level_page():
    html = render_shell("home", "x", request_path="/home")
    assert "<base href='./'>" in html


def test_render_shell_uses_dotdot_base_for_depth2_page():
    html = render_shell("cameras", "x", request_path="/cameras/pool")
    assert "<base href='../'>" in html


def test_render_shell_uses_dotdotdotdot_base_for_depth3_page():
    html = render_shell("areas", "x", request_path="/areas/pool/edit")
    assert "<base href='../../'>" in html


def test_render_shell_default_request_path_works_for_legacy_callers():
    # Legacy callers that don't pass request_path get the './' base
    # — matches the pre-fix behavior for depth-1 pages.
    html = render_shell("home", "x")
    assert "<base href='./'>" in html


# ─── User-review fixup #9: Activity dropped from nav ─────────────


def test_activity_no_longer_in_primary_nav():
    """Per fixup #9: Activity is reachable via 'See all' on Home; the
    standalone nav slot was redundant."""
    paths = {path for path, _label in NAV_ITEMS}
    assert "activity" not in paths


def test_memory_still_in_primary_nav():
    paths = {path for path, _label in NAV_ITEMS}
    assert "memory" in paths


def test_nav_order_includes_home_first():
    assert NAV_ITEMS[0] == ("home", "Home")


# ─── Depth-aware nav link resolution against a fake browser ─────


def _resolve(base: str, link: str, current_path: str) -> str:
    """Mirror browser URL resolution: per RFC 3986 §5.3, the base
    element's href is resolved against the document URL as-is (no
    forced trailing slash), and the result becomes the new base URL
    against which all in-page links resolve."""
    from urllib.parse import urljoin

    base_abs = urljoin(current_path, base)
    return urljoin(base_abs, link)


def test_resolved_camera_nav_link_from_camera_detail():
    """The bug: on /cameras/pool, clicking nav 'system' (href='system')
    was resolving to /cameras/system. With the fix, it resolves to
    /system."""
    base = base_href_for_path("/cameras/pool")
    resolved = _resolve(base, "system", "/cameras/pool")
    assert resolved == "/system"


def test_resolved_area_save_from_new_area_form():
    """The bug: on /areas/new, form action='areas' was POSTing to
    /areas/areas (which 405'd). With the fix, it POSTs to /areas."""
    base = base_href_for_path("/areas/new")
    resolved = _resolve(base, "areas", "/areas/new")
    assert resolved == "/areas"


def test_resolved_alert_nav_link_from_alert_detail():
    """Fixup #3 prerequisite: nav on the alert page needs to resolve
    correctly from depth 2 too."""
    base = base_href_for_path("/alert/evt_42")
    resolved = _resolve(base, "memory", "/alert/evt_42")
    assert resolved == "/memory"


def test_resolved_top_level_links_from_root():
    """Pre-existing behavior on root pages stays correct after the fix."""
    base = base_href_for_path("/home")
    resolved = _resolve(base, "system", "/home")
    assert resolved == "/system"
