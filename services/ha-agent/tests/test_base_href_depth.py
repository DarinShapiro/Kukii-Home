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


# ─── Drawer persistence across nav (Part X §34 follow-up) ───────


def test_nav_links_carry_drawer_when_drawer_is_open():
    """When the drawer is currently open (drawer_html non-empty), every
    primary nav link should append ?drawer=1 so clicking Home / Areas /
    Cameras / etc. keeps the conversation panel open across navigation."""
    html_out = render_shell(
        "home",
        "x",
        drawer_html="<aside class='drawer'>x</aside>",
    )
    # Every nav target should carry ?drawer=1
    for path, _label in NAV_ITEMS:
        assert f"href='{path}?drawer=1'" in html_out


def test_nav_links_no_drawer_query_when_drawer_closed():
    """When the drawer is closed (drawer_html empty), the nav links
    themselves are plain — no ?drawer=1 cluttering the URL. The ✨
    trigger separately always emits drawer=1 (its job is to open
    the drawer); we just isolate to the <nav>…</nav> region."""
    html_out = render_shell("home", "x", drawer_html="")
    nav_block = html_out.split("<nav>")[1].split("</nav>")[0]
    for path, _label in NAV_ITEMS:
        assert f"href='{path}'" in nav_block
        assert "?drawer=1" not in nav_block


def test_drawer_persistence_round_trip_via_browser_resolution():
    """Walk the click: on /memory?drawer=1, clicking the 'Cameras' nav
    link should land at /cameras?drawer=1 (preserving the drawer
    state). RFC 3986 §5.3 resolution via _resolve helper."""
    base = base_href_for_path("/memory")
    resolved = _resolve(base, "cameras?drawer=1", "/memory")
    assert resolved == "/cameras?drawer=1"


def test_drawer_persistence_works_from_depth_2_too():
    """Walk: on /cameras/pool?drawer=1, clicking Home → /home?drawer=1."""
    base = base_href_for_path("/cameras/pool")
    resolved = _resolve(base, "home?drawer=1", "/cameras/pool")
    assert resolved == "/home?drawer=1"


# ─── Persistent header drawer trigger (always-available ✨) ──────


def test_shell_renders_drawer_trigger_in_header():
    """Per the design's "available from any page" promise, the ✨
    trigger sits in the header on every render."""
    html = render_shell("home", "x")
    assert "drawer-toggle" in html
    assert "✨" in html


def test_drawer_trigger_present_on_pages_without_drawer_html():
    """The trigger is independent of whether the drawer is currently
    open — it should always be there as a way to OPEN it."""
    html = render_shell("areas", "x", drawer_html="")
    assert "drawer-toggle" in html


def test_drawer_trigger_present_when_drawer_already_open():
    """Doesn't disappear when drawer_html is non-empty either —
    keeps the click target visible for users who close + reopen."""
    html = render_shell("memory", "x", drawer_html="<aside class='drawer'>x</aside>")
    assert "drawer-toggle" in html
    assert "<aside class='drawer'>" in html


def test_drawer_trigger_stays_on_current_page():
    """Page-context preserved (Part X §34): opening the drawer from
    /cameras/pool should land on /cameras/pool?drawer=1, not jump to
    /memory. The shell computes the href from request_path."""
    html = render_shell("cameras", "x", request_path="/cameras/pool")
    assert "href='cameras/pool?drawer=1'" in html


def test_drawer_trigger_on_top_level_page():
    html = render_shell("home", "x", request_path="/home")
    assert "href='home?drawer=1'" in html


def test_drawer_trigger_falls_back_to_memory_without_request_path():
    """Legacy callers that don't pass request_path get the safe
    'memory?drawer=1' fallback so the trigger is never broken."""
    html = render_shell("home", "x")
    assert "href='memory?drawer=1'" in html


def test_drawer_trigger_resolves_to_current_page_with_drawer_query():
    """Critical: the trigger href + <base href> at every depth must
    resolve to the SAME page the user was on (with drawer=1 added).
    RFC 3986 §5.3 compliant."""
    # Depth-1
    base1 = base_href_for_path("/home")
    assert _resolve(base1, "home?drawer=1", "/home") == "/home?drawer=1"
    # Depth-2
    base2 = base_href_for_path("/cameras/pool")
    assert _resolve(base2, "cameras/pool?drawer=1", "/cameras/pool") == "/cameras/pool?drawer=1"
    # Depth-3
    base3 = base_href_for_path("/areas/pool/edit")
    assert (
        _resolve(base3, "areas/pool/edit?drawer=1", "/areas/pool/edit")
        == "/areas/pool/edit?drawer=1"
    )
