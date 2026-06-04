"""Shared layout for the v2 Web UI.

Every page is wrapped in the same shell — top nav, content area, footer —
so pages can focus on their own content and the navigation feels consistent.
Relative URLs everywhere (``<base href='./'>``) so the same HTML renders
identically under HA Ingress (``/api/hassio_ingress/<token>/...``) and direct
port-8765 access — the same trick the per-alert page and Review page use.

All v2 pages are at top-level paths (``/home``, ``/activity``, ...). Detail
views use query strings (e.g. ``/camera?id=pool``) rather than deeper paths so
the relative-URL trick keeps working without ``..`` games.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

# Nav order matches the ratified TOC. Each entry: (path, label).
NAV_ITEMS: list[tuple[str, str]] = [
    ("home",        "Home"),
    ("activity",    "Activity"),
    ("areas",       "Areas"),
    ("intent",      "Intent"),
    ("policies",    "Policies"),
    ("review",      "Identities"),
    ("cameras",     "Cameras"),
    ("diagnostics", "Diagnostics"),
]


# Shared dark-themed CSS. Matches the existing /review page so the visual
# language is consistent. Lives in the shell so every page picks it up.
_STYLE = """
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  background:#0f1115;color:#e6e6e6;min-height:100vh}
a{color:#8ab4f8;text-decoration:none}
a:hover{text-decoration:underline}

/* ─── header + nav ─────────────────────────────────────────────── */
header{display:flex;align-items:center;gap:14px;padding:10px 18px;
  background:#161a21;border-bottom:1px solid #262c36;
  position:sticky;top:0;z-index:10}
header .brand{font-weight:600;font-size:15px;color:#e6e6e6;white-space:nowrap}
header nav{display:flex;gap:2px;flex-wrap:wrap;flex:1}
header nav a{color:#9aa7b8;font-size:13px;padding:6px 10px;border-radius:6px;
  text-decoration:none;white-space:nowrap}
header nav a:hover{background:#1c2129;color:#e6e6e6}
header nav a.active{background:#2a3140;color:#e6e6e6}
header .version{color:#5b6675;font-size:12px}

/* ─── content frame ────────────────────────────────────────────── */
main{padding:18px;max-width:1100px;margin:0 auto}
h1{font-size:22px;margin:0 0 6px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:#9aa7b8;
  margin:22px 0 10px;font-weight:600}
.sub{color:#9aa7b8;font-size:13px}

/* ─── shared bits ──────────────────────────────────────────────── */
.empty{color:#7e8a9a;padding:20px 0;font-size:14px}
.notice{background:#13171e;border:1px solid #262c36;border-radius:10px;
  padding:14px 16px;margin:12px 0;color:#cfd6df;font-size:14px}
.tag{display:inline-block;background:#222936;border-radius:6px;padding:1px 7px;
  font-size:11px;color:#9fc1ff;margin-right:5px}
.muted{color:#7e8a9a}
.chip{display:inline-block;background:#161a21;border:1px solid #262c36;
  border-radius:20px;padding:5px 12px;font-size:13px;margin:0 4px 4px 0}
.row{display:flex;align-items:center;gap:10px;padding:10px 0;
  border-bottom:1px solid #1c2129}
.row:last-child{border-bottom:0}
.flash{background:#16361f;border:1px solid #2e7d46;color:#b6f0c6;
  padding:10px 14px;border-radius:8px;margin-bottom:14px}

/* ─── home: status line, attention, activity, system stripe ────── */
.status-line{font-size:16px;margin:8px 0 18px;color:#cfd6df}
.attention-row{display:flex;align-items:flex-start;gap:10px;padding:12px 14px;
  background:#161a21;border:1px solid #262c36;border-radius:10px;margin:6px 0}
.attention-row .glyph{font-size:16px;line-height:20px;flex-shrink:0}
.attention-row .body{flex:1;font-size:14px;color:#e6e6e6}
.attention-row .body .meta{color:#9aa7b8;font-size:12px;margin-top:2px}
.attention-row .actions{display:flex;gap:6px;flex-shrink:0}
.attention-row .actions a,
.attention-row .actions button{background:#2e6ad1;color:#fff;border:0;
  border-radius:6px;padding:5px 10px;font-size:13px;cursor:pointer;
  text-decoration:none}
.attention-row .actions .secondary{background:transparent;border:1px solid #2a3140;
  color:#9aa7b8}

.activity-row{display:flex;align-items:center;gap:12px;padding:10px 4px;
  border-bottom:1px solid #1c2129}
.activity-row.passive{opacity:.6;padding:6px 4px}
.activity-row.passive .when{font-size:12px}
.activity-row.passive .what{font-size:13px}
.activity-row .when{color:#9aa7b8;font-size:12px;width:90px;flex-shrink:0;
  text-align:right}
.activity-row .what{flex:1;color:#e6e6e6;font-size:14px}
.activity-row .what .where{color:#9aa7b8;font-size:13px}
.activity-row .chip-out{font-size:12px;padding:2px 8px;border-radius:10px;
  background:#1c2129;color:#9fc1ff}
.activity-row .chip-out.action{background:#1e3a4f;color:#a8d4ff}
.activity-row a.trace{color:#5b6675;font-size:11px;text-decoration:none;
  padding-left:10px}
.activity-row a.trace:hover{color:#9aa7b8}
.trust-line{color:#7e8a9a;font-size:12px;text-align:right;padding:8px 0;
  font-style:italic}

.system-stripe{margin-top:30px;padding:12px 14px;background:#13171e;
  border:1px solid #1c2129;border-radius:8px;color:#9aa7b8;font-size:13px}
.system-stripe summary{cursor:pointer;list-style:none;outline:none}
.system-stripe summary::-webkit-details-marker{display:none}
.system-stripe .lines{margin-top:8px;padding-left:8px;border-left:2px solid #262c36}
.system-stripe .lines div{padding:3px 0}

/* ─── mock-page treatment ──────────────────────────────────────── */
.coming-soon{background:#13171e;border:1px dashed #262c36;border-radius:12px;
  padding:36px 24px;text-align:center;color:#9aa7b8;margin-top:24px}
.coming-soon h3{margin:0 0 6px;font-size:16px;color:#cfd6df}
.coming-soon .sketch{margin:16px 0 0;color:#7e8a9a;font-size:13px;
  font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre;
  text-align:left;display:inline-block}
"""


def _e(s: Any) -> str:
    """HTML-escape any value (forces str first so ints/floats are safe)."""
    return html.escape(str(s), quote=True)


def render_shell(active: str, content_html: str, *, version: str = "",
                 flash: str | None = None) -> str:
    """Wrap ``content_html`` in the shared shell. ``active`` is the path of
    the current page (one of :data:`NAV_ITEMS` paths) so its nav link is
    highlighted. ``flash`` is an optional one-line notice rendered above the
    content (success / error messages from a POST-redirect)."""
    nav = "".join(
        f"<a class='{'active' if path == active else ''}' href='{path}'>{_e(label)}</a>"
        for path, label in NAV_ITEMS
    )
    flash_html = f"<div class='flash'>{_e(flash)}</div>" if flash else ""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        # base ./ → relative URLs resolve against the current dir (top-level),
        # identically under HA Ingress + direct port. Same trick as /review.
        "<base href='./'>"
        f"<title>Kukii-Home</title><style>{_STYLE}</style></head><body>"
        "<header>"
        "<span class='brand'>Kukii-Home</span>"
        f"<nav>{nav}</nav>"
        f"<span class='version'>{_e(version)}</span>"
        "</header>"
        f"<main>{flash_html}{content_html}</main>"
        "</body></html>"
    )


# ─── relative-time formatting (used by Home + Activity) ──────────────


def relative_time(ts: float, *, now: float | None = None) -> str:
    """Friendly relative timestamp: ``Just now``, ``5m ago``, ``An hour ago``,
    ``3h ago``, ``Yesterday``, ``Tuesday``, ``Mar 12``. Mirrors the spec in
    Part III §23 (no day boundaries — graduates by magnitude)."""
    if now is None:
        now = datetime.now(UTC).timestamp()
    delta = max(0.0, now - ts)
    if delta < 60:
        return "Just now"
    if delta < 3600:
        m = int(delta // 60)
        return f"{m}m ago"
    if delta < 7200:
        return "An hour ago"
    if delta < 86400:
        h = int(delta // 3600)
        return f"{h}h ago"
    if delta < 172800:
        return "Yesterday"
    if delta < 604800:
        try:
            return datetime.fromtimestamp(ts, UTC).astimezone().strftime("%A")
        except (ValueError, OSError):
            return "Earlier"
    try:
        return datetime.fromtimestamp(ts, UTC).astimezone().strftime("%b %d")
    except (ValueError, OSError):
        return "Older"
