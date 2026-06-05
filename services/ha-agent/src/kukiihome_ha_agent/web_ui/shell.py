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
    ("home", "Home"),
    # User-review fixup: Activity dropped from the primary nav since it's
    # reachable via the "See all activity" link on the Home page and the
    # standalone tab was redundant. The /activity route + page itself
    # still exist; only the nav slot was removed.
    # Iter 3 (Parts IX+X): Intent + Policies collapse into Memory. The
    # /intent and /policies URLs 301-redirect for backward-compat with
    # bookmarks + HA Lovelace card links.
    ("memory", "Memory"),
    ("areas", "Areas"),
    # Iter 3 / Part IX §29: /identities is now the unified Review+Enrolled
    # surface; the existing /review URL is preserved as a tab within it.
    ("identities", "Identities"),
    ("cameras", "Cameras"),
    # Iter 3 / Part IX §30: storage + privacy. Diagnostics stays separate;
    # this surface is "what's it holding + privacy ops".
    ("system", "System"),
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
header .drawer-toggle{display:inline-flex;align-items:center;justify-content:center;
  width:30px;height:30px;border-radius:6px;background:#1f2632;
  color:#cfd6df;text-decoration:none;font-size:14px;
  transition:background 100ms ease}
header .drawer-toggle:hover{background:#2a3344;color:#e5edf7}

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

/* ─── thumbnails — aspect-ratio handling (Task 5) ───────────────── */
/* Standard activity-row thumbnail container: 16:9 wide-default with cover-
 * crop. Pool-cam (top-down 4K) gets cropped heavily here — that's accepted
 * for a thumbnail; the clip player below honors the source aspect. */
.thumb{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;
  background:#0a0c10;border-radius:6px}
.thumb.portrait{aspect-ratio:3/4}  /* opt-in for top-down cams where the
                                      subject lives in the vertical axis */

/* Clip / event-detail video player: max-width constrained, native aspect
 * preserved (no distortion). Used by the (future) per-event trace and
 * the existing track-detail GIF — same container, different media. */
.clip-player{display:block;width:100%;max-width:480px;height:auto;
  margin:0 auto 14px;border-radius:10px;background:#0a0c10;
  border:1px solid #262c36}

/* ─── activity filter strip (Task 7 / Part IV) ──────────────────── */
form.filters{display:flex;gap:14px;flex-wrap:wrap;align-items:center;
  padding:10px 12px;background:#13171e;border:1px solid #1c2129;
  border-radius:10px;margin:6px 0 14px;font-size:13px;color:#cfd6df;
  position:sticky;top:54px;z-index:5}
form.filters label{display:flex;align-items:center;gap:6px;color:#cfd6df}
form.filters select{background:#0c0f14;border:1px solid #2a3140;
  color:#e6e6e6;border-radius:6px;padding:4px 8px;font-size:13px;
  min-width:120px}
form.filters button{background:#2e6ad1;border:0;color:#fff;border-radius:6px;
  padding:5px 12px;font-size:13px;cursor:pointer}
form.filters a.clear{color:#7e8a9a;font-size:12px;text-decoration:none}
form.filters a.clear:hover{color:#cfd6df;text-decoration:underline}

/* ─── mock-page treatment ──────────────────────────────────────── */
.coming-soon{background:#13171e;border:1px dashed #262c36;border-radius:12px;
  padding:36px 24px;text-align:center;color:#9aa7b8;margin-top:24px}
.coming-soon h3{margin:0 0 6px;font-size:16px;color:#cfd6df}
.coming-soon .sketch{margin:16px 0 0;color:#7e8a9a;font-size:13px;
  font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre;
  text-align:left;display:inline-block}

/* ─── Intent / Rules (Task 9) ────────────────────────────────────── */
.card{background:#141a22;border:1px solid #1f2632;border-radius:10px;
  padding:16px 18px;margin:18px 0}
.card h2{margin:0 0 8px;font-size:16px;color:#e5edf7}
.card h3{margin:8px 0;font-size:14px;color:#cfd6df}
.card-head{display:flex;align-items:center;justify-content:space-between}
.btn{display:inline-block;background:#1d2733;border:1px solid #2a3548;
  color:#cfd6df;border-radius:6px;padding:5px 10px;font-size:12px;
  text-decoration:none;cursor:pointer}
.btn:hover{background:#26344a}
.btn.primary{background:#2e6ad1;border-color:#2e6ad1;color:#fff}
.btn.danger{color:#e08a8a;border-color:#3b2530}
.btn.danger:hover{background:#3b2530;color:#fff}
.rule-row{padding:12px 0;border-bottom:1px solid #1f2632}
.rule-row:last-child{border-bottom:0}
.rule-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.rule-head b{color:#e5edf7;font-size:14px}
.rule-head .severity{color:#9aa7b8;font-size:12px}
.chip{font-size:11px;padding:2px 8px;border-radius:10px;
  background:#1d2733;color:#9aa7b8}
.chip.enabled{background:#1f3320;color:#9dd5a3}
.chip.disabled{background:#2f2225;color:#e08a8a}
.rule-scope,.rule-intent{font-size:13px;color:#cfd6df;margin-top:4px}
.rule-intent .intent-text{color:#e5edf7}
.rule-row .muted{color:#7e8a9a;font-size:12px;margin-top:4px}
.rule-actions{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
.empty{padding:24px;text-align:center;color:#7e8a9a;font-style:italic}
.rule-form input[type=text],.rule-form textarea,.rule-form select{
  width:100%;max-width:560px;background:#0c0f14;border:1px solid #2a3140;
  color:#cfd6df;border-radius:6px;padding:8px 10px;font:inherit;
  box-sizing:border-box}
.rule-form textarea{min-height:80px;font-family:inherit;resize:vertical}
.rule-form .mode-radios,.rule-form .severity-radios{
  display:flex;gap:18px;flex-wrap:wrap}
.rule-form label.radio,.rule-form label.check{display:flex;align-items:center;
  gap:6px;color:#cfd6df;font-size:13px}
.rule-form .check-list{display:flex;flex-direction:column;gap:6px;
  margin-top:6px;max-height:200px;overflow:auto;padding:6px 0}
.rule-form details summary{cursor:pointer;color:#9aa7b8;font-size:13px;
  padding:4px 0}
.rule-form .hint{color:#7e8a9a;font-size:12px;margin-top:6px}
.rule-form .subject-row{display:flex;align-items:center;gap:8px;
  flex-wrap:wrap;color:#cfd6df;font-size:13px}
.rule-form .form-actions{display:flex;justify-content:flex-end;
  gap:10px;margin-top:20px}

/* ─── Event clip play affordance (Task 1) ───────────────────────── */
.thumb-wrap{position:relative;display:inline-block;width:100%}
.thumb-wrap .play-overlay{
  position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  background:rgba(0,0,0,0.25);color:#fff;font-size:28px;line-height:1;
  text-decoration:none;border-radius:6px;opacity:0;transition:opacity 100ms ease;
}
.thumb-wrap:hover .play-overlay,.thumb-wrap:focus-within .play-overlay{opacity:1}
.thumb-wrap .play-overlay span{
  display:flex;align-items:center;justify-content:center;
  width:44px;height:44px;border-radius:50%;background:rgba(0,0,0,0.55);
  font-size:18px;padding-left:3px /* visually center the ▶ glyph */;
}
video.event-clip{display:block;width:100%;max-width:720px;border-radius:8px;
  background:#0a0c10;margin:8px auto;}
a.play{display:inline-block;color:#9aa7b8;text-decoration:none;
  margin-left:8px;padding:0 4px;border-radius:3px;font-size:12px}
a.play:hover{color:#fff;background:#26344a}

/* ─── Matches table (Task 9) ────────────────────────────────────── */
table.matches-table,table.matrix-table{width:100%;border-collapse:collapse;
  margin-top:12px;font-size:13px;color:#cfd6df}
table.matches-table th,table.matches-table td,
table.matrix-table th,table.matrix-table td{
  text-align:left;padding:6px 8px;border-bottom:1px solid #1f2632;
  vertical-align:top}
table.matches-table th,table.matrix-table th{color:#9aa7b8;font-weight:600;
  font-size:12px;text-transform:uppercase;letter-spacing:0.04em}

/* ─── Cameras list + detail (Iter 2.B) ──────────────────────────── */
.cameras-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));
  gap:14px;margin-top:18px}
a.camera-tile{display:block;background:#141a22;border:1px solid #1f2632;
  border-radius:10px;padding:14px 16px;color:#cfd6df;text-decoration:none;
  transition:border-color 100ms ease}
a.camera-tile:hover{border-color:#3a4a64}
.camera-tile .cam-head{display:flex;align-items:center;
  justify-content:space-between;gap:10px}
.camera-tile .cam-head b{color:#e5edf7;font-size:14px}
.camera-tile .cam-meta{font-size:12px;color:#9aa7b8;margin-top:6px}
.camera-tile .cam-meta.muted{color:#7e8a9a}
.camera-tile .err{color:#e08a8a;font-size:12px;margin-top:6px}
.chip.cam-state.ok{background:#1f3320;color:#9dd5a3}
.chip.cam-state.warn{background:#332b1f;color:#d5b793}
.chip.cam-state.bad{background:#3b2530;color:#e08a8a}
.chip.cam-state.muted{background:#1d2733;color:#9aa7b8}
.chip.cap-src{font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:10px;letter-spacing:0.05em}
.chip.cap-src.ok{background:#1f3320;color:#9dd5a3}
.chip.cap-src.warn{background:#332b1f;color:#d5b793}
.chip.cap-src.bad{background:#3b2530;color:#e08a8a}
.chip.cap-src.muted{background:#1d2733;color:#9aa7b8}
.cam-snap{display:block;width:100%;max-width:480px;border-radius:6px;
  background:#0a0c10;margin-top:10px}
.cam-row{font-size:13px;color:#cfd6df}
.cam-row .err{color:#e08a8a;font-size:12px;margin-top:6px}
.back-link{display:inline-block;color:#9aa7b8;font-size:12px;
  text-decoration:none;margin-bottom:10px}
.back-link:hover{color:#cfd6df;text-decoration:underline}

/* ─── Conversational drawer (Iter 3 / Part X §34) ───────────── */
/* Smoothness note: the drawer ships server-rendered (it's in the DOM
   on first paint), so transitions can't fire on the bare appearance.
   Instead we add classes from JS:
     .entering — only on the very first page where the drawer is open
                 (slides in from the right);
     .closing  — when the close link is clicked (slides out to the
                 right, then JS navigates to the close URL once the
                 animation finishes).
   Across nav-while-open we deliberately skip .entering so the drawer
   appears to "stay put" between pages rather than re-sliding every
   click. prefers-reduced-motion disables everything. */
main.with-drawer{padding-right:380px;transition:padding-right 220ms cubic-bezier(.2,.7,.2,1)}
main.with-drawer.drawer-closing{padding-right:0}
@keyframes kkh-drawer-in{
  from{transform:translateX(380px);opacity:0}
  to{transform:translateX(0);opacity:1}
}
aside.drawer{position:fixed;top:54px;right:0;width:360px;height:calc(100vh - 54px);
  background:#0f141b;border-left:1px solid #1f2632;overflow-y:auto;
  padding:14px 16px 80px;z-index:50;
  box-shadow:-12px 0 24px rgba(0,0,0,.25);
  transition:transform 200ms cubic-bezier(.4,0,.6,1),opacity 180ms ease-out;
  will-change:transform,opacity}
aside.drawer.entering{animation:kkh-drawer-in 220ms cubic-bezier(.2,.7,.2,1) both}
aside.drawer.closing{transform:translateX(380px);opacity:0;pointer-events:none}
@media (prefers-reduced-motion: reduce){
  main.with-drawer,aside.drawer{transition:none}
  aside.drawer.entering{animation:none}
}
.drawer-head{display:flex;justify-content:space-between;align-items:center;
  margin-bottom:8px}
.drawer-head h3{margin:0;font-size:14px;color:#e5edf7;font-weight:600}
.drawer-close{color:#9aa7b8;text-decoration:none;font-size:12px}
.drawer-close:hover{color:#cfd6df}
.drawer-context{background:#1a2230;border-radius:6px;padding:8px 10px;
  font-size:12px;color:#cfd6df;margin-bottom:10px}
.drawer-empty{color:#9aa7b8;font-size:13px;padding:14px 0;line-height:1.5}
.drawer-thread{display:flex;flex-direction:column;gap:10px;
  margin:8px 0 14px}
.drawer-turn{font-size:13px}
.drawer-turn .turn-meta{font-size:11px;color:#7e8a9a;margin-bottom:2px}
.drawer-turn.user .turn-body{background:#1a2230;color:#cfd6df;
  padding:8px 10px;border-radius:6px}
.drawer-turn.system .turn-body{color:#cfd6df}
.drawer-card{background:#141a22;border:1px solid #1f2632;border-radius:8px;
  padding:10px 12px;color:#cfd6df;font-size:13px}
.drawer-card.committed{background:#1f2a20;border-color:#345a35}
.drawer-card .drawer-meta{font-size:11px;margin:6px 0}
.drawer-card .drawer-reasoning{margin-top:8px;color:#9aa7b8;font-size:12px;
  font-style:italic}
.drawer-card .drawer-actions{margin-top:10px;display:flex;gap:6px}
.clarify-q{margin-top:6px;color:#d5b793;font-size:12px}
.drawer-composer{display:flex;flex-direction:column;gap:6px;margin-top:12px}
.drawer-composer textarea{background:#141a22;border:1px solid #1f2632;
  color:#cfd6df;border-radius:6px;padding:8px 10px;font-size:13px;
  font-family:inherit;resize:vertical}

/* ─── /memory unified browse (Iter 3 / Part IX §28) ──────────── */
.memory-drawer-trigger{margin:18px 0 12px}
.memory-drawer-trigger .btn{font-size:14px;padding:10px 16px}
.memory-cut{display:flex;gap:6px;margin-bottom:14px;
  border-bottom:1px solid #1f2632}
.memory-cut a{padding:8px 14px;color:#9aa7b8;text-decoration:none;
  font-size:13px;border-bottom:2px solid transparent;
  margin-bottom:-1px;transition:color 100ms ease}
.memory-cut a:hover{color:#cfd6df}
.memory-cut a.active{color:#cfd6df;border-bottom-color:#5d8aa8}
.origin-icon{font-size:11px;color:#7e8a9a;cursor:help;margin-left:6px}
.rule-row .rule-meta{font-size:12px;margin-top:4px}
section.drift-banner{background:#1f1d14;border:1px solid #4a3a18;
  border-radius:8px;padding:12px 14px;margin:14px 0}
section.drift-banner h3{margin:0 0 6px;color:#d5b793;font-size:13px}
.drift-row{margin-top:8px;font-size:13px;color:#cfd6df}
section.llm-down-banner{background:#231518;border:1px solid #5a2a32;
  border-radius:8px;padding:12px 14px;margin:14px 0}
section.llm-down-banner h3{margin:0 0 6px;color:#e08a8a;font-size:13px}
section.llm-down-banner code{background:#1a2230;padding:1px 5px;
  border-radius:3px;font-size:12px}
"""


def _e(s: Any) -> str:
    """HTML-escape any value (forces str first so ints/floats are safe)."""
    return html.escape(str(s), quote=True)


# ─── camera display-name normalization (Task 3) ──────────────────────


# Stream-quality suffixes commonly tacked onto camera friendly names by
# integrations (Reolink: Fluent/Clear/Balanced; Dahua: Main/Sub; etc.).
# Stripped from display names so headlines don't read
# *"Front South Camera Fluent"*. Case-insensitive whole-word match at the
# *end* of the name only.
_STREAM_QUALITY_SUFFIXES = (
    "Fluent",
    "Clear",
    "Balanced",
    "Main",
    "Sub",
    "Substream",
    "Mainstream",
    "Stream",
    "HD",
    "SD",
)


def camera_display_name(raw_name: str | None) -> str:
    """Normalize an HA camera friendly_name into a clean display name.

    Strips trailing stream-quality suffixes (``Fluent``, ``Clear``, ``Main``,
    ``Sub``, etc.). Conservative: only the final whole word is stripped, and
    only if it's in the well-known list — so a camera intentionally named
    ``"Reolink Front"`` keeps its name (no suffix matches).

    Appends ``" Camera"`` only if the result doesn't already contain
    *Camera* / *Cam* (avoids *"Front South Camera Camera"*).

    Falls back to a humanized version of an entity-id slug
    (``front_south`` → ``Front South Camera``) when ``raw_name`` is empty.
    """
    if not raw_name:
        return ""
    name = str(raw_name).strip()

    # Strip well-known stream-quality suffixes, iteratively (handles double
    # suffixes like "Front Cam Main Stream"). One pass per suffix at a time;
    # bounded loop so a pathological name can't run forever.
    for _ in range(4):
        stripped = False
        for suffix in _STREAM_QUALITY_SUFFIXES:
            # case-insensitive whole-word match at end
            if len(name) > len(suffix) and name[-len(suffix) :].lower() == suffix.lower():
                # require whitespace boundary so we don't eat parts of words
                if name[-len(suffix) - 1] in " -_":
                    name = name[: -len(suffix) - 1].rstrip()
                    stripped = True
                    break
        if not stripped:
            break

    # Tasteful trailing "Camera" only when not already present.
    lower = name.lower()
    if "camera" not in lower and "cam" not in lower:
        name = f"{name} Camera"

    return name or str(raw_name).strip()


def base_href_for_path(request_path: str) -> str:
    """Compute the right ``<base href>`` so relative URLs in the shell
    resolve to the application root regardless of the current page's
    depth.

    Algorithm: count path components. For depth=1 (``/home``, ``/areas``,
    ``/system``, ...) ``./`` works because the browser drops the trailing
    component to find the current directory. For depth ≥ 2
    (``/areas/new``, ``/cameras/{id}``, ``/identities/{id}``, ...) we
    need ``../`` for each extra level so the base resolves back to the
    application root.

    Without this, a nav link like ``href='system'`` on the camera
    detail page would resolve to ``/cameras/system`` instead of
    ``/system`` — which is the camera-tab-nav-broken bug the user
    reported.
    """
    parts = [p for p in (request_path or "/").split("/") if p]
    depth = len(parts)
    if depth <= 1:
        return "./"
    return "../" * (depth - 1)


def render_shell(
    active: str,
    content_html: str,
    *,
    version: str = "",
    flash: str | None = None,
    drawer_html: str = "",
    request_path: str = "",
) -> str:
    """Wrap ``content_html`` in the shared shell. ``active`` is the path of
    the current page (one of :data:`NAV_ITEMS` paths) so its nav link is
    highlighted. ``flash`` is an optional one-line notice rendered above the
    content (success / error messages from a POST-redirect).

    ``drawer_html`` is the optional conversational drawer (Part X §34) —
    when non-empty, it renders as a fixed right-side panel and the main
    content shifts to make room. Pass empty string to render without
    the drawer.

    ``request_path`` is the URL path of the current request. The shell
    uses it to pick the right ``<base href>`` so relative links resolve
    to the application root from any depth (see
    :func:`base_href_for_path`). Pass an empty string for legacy
    callers + flat pages — the default ``./`` works for depth-1 routes
    like ``/home`` / ``/areas`` / ``/memory``.
    """
    # Drawer persistence across nav (Part X §34 "persistent across page
    # navigation"). When the drawer is currently open on this page,
    # carry ?drawer=1 forward on every nav link so clicking Home / Areas
    # / Cameras / etc. keeps the conversation panel open. drawer_open
    # is detected from drawer_html being non-empty — the route handler
    # has already decided whether to render the aside.
    drawer_open = bool(drawer_html)
    drawer_q = "?drawer=1" if drawer_open else ""
    nav = "".join(
        f"<a class='{'active' if path == active else ''}' href='{path}{drawer_q}'>{_e(label)}</a>"
        for path, label in NAV_ITEMS
    )
    flash_html = f"<div class='flash'>{_e(flash)}</div>" if flash else ""
    main_class = "with-drawer" if drawer_html else ""
    base_href = base_href_for_path(request_path) if request_path else "./"
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        # base href is computed from the current request path so links
        # in the nav + content resolve to the application root from any
        # depth (depth-1 = './', depth-2 = '../', etc.). Identical
        # behavior under HA Ingress + direct port.
        f"<base href='{base_href}'>"
        f"<title>Kukii-Home</title><style>{_STYLE}</style>"
        # Iter 3 / Part X §40: push-reply fragment-load. HA Companion
        # opens push notifications at /alert/{id}#drawer; the URL
        # fragment is client-side only (server can't see it), so we
        # rewrite it to a real ?drawer=1 query and reload. The reload
        # is a no-op when ?drawer=1 is already present.
        "<script>"
        "if(location.hash==='#drawer'&&!/[?&]drawer=/.test(location.search)){"
        "var sep=location.search?'&':'?';"
        "location.replace(location.pathname+location.search+sep+'drawer=1');"
        "}"
        "</script>"
        "</head><body>"
        "<header>"
        "<span class='brand'>Kukii-Home</span>"
        f"<nav>{nav}</nav>"
        # Persistent conversational drawer trigger (Part X §34). Stays on
        # the current page so page_context is preserved — opening from
        # /cameras/pool lands in a drawer that knows you were on Pool
        # Camera, opening from /alert/{id} prefills with that alert.
        #
        # The href is computed from request_path (with leading slash
        # stripped) + '?drawer=1'. Under <base href> at any depth this
        # resolves to <root>/{request_path}?drawer=1 — the same page
        # with the drawer signal set.
        #
        # An earlier attempt used query-only href '?drawer=1' with a JS
        # handler — that broke because RFC 3986 §5.3 resolves query-only
        # references against <base>, not the document URL. So ?drawer=1
        # with base './' on /home went to <root>?drawer=1 (the add-on
        # landing page) not /home?drawer=1. Using the full relative path
        # avoids that entire class of issue.
        + (
            "<a class='drawer-toggle' href='"
            f"{_e((request_path or '/memory').lstrip('/')) or 'memory'}"
            "?drawer=1' title='Tell me what to watch for'>✨</a>"
        )
        + f"<span class='version'>{_e(version)}</span>"
        "</header>"
        f"<main class='{main_class}'>{flash_html}{content_html}</main>"
        f"{drawer_html}"
        # Drawer pop-out animation hooks. See the CSS comment above
        # main.with-drawer for the why; this script is the how.
        #
        #   1. On load, if the drawer is present in the DOM, check
        #      sessionStorage: was the drawer open on the previous
        #      page in this tab? If NO → we just opened it, add
        #      .entering so the keyframe plays. If YES → the user is
        #      navigating between pages with the drawer kept open, so
        #      we skip the animation (otherwise it would re-slide-in
        #      on every nav click — distracting).
        #      Either way, mark the drawer as open for next paint.
        #   2. Intercept clicks on a.drawer-close: add .closing to the
        #      drawer + .drawer-closing to <main> so both glide out
        #      together, then navigate after the transition finishes.
        #      We listen for transitionend on the drawer but also set
        #      a safety timeout so a bug in CSS / transitions never
        #      strands the close.
        "<script>(function(){"
        "var d=document.querySelector('aside.drawer');"
        "var m=document.querySelector('main.with-drawer');"
        "try{"
        "var wasOpen=sessionStorage.getItem('kkh-drawer-open')==='1';"
        "if(d){"
        "if(!wasOpen){d.classList.add('entering');"
        "d.addEventListener('animationend',function(){"
        "d.classList.remove('entering');},{once:true});}"
        "sessionStorage.setItem('kkh-drawer-open','1');"
        "}else{sessionStorage.setItem('kkh-drawer-open','0');}"
        "}catch(e){}"
        "document.querySelectorAll('a.drawer-close').forEach(function(a){"
        "a.addEventListener('click',function(ev){"
        "if(!d)return;"
        "ev.preventDefault();"
        "var href=a.getAttribute('href');"
        "d.classList.add('closing');"
        "if(m)m.classList.add('drawer-closing');"
        "try{sessionStorage.setItem('kkh-drawer-open','0');}catch(e){}"
        "var done=false,go=function(){if(done)return;done=true;"
        "location.href=href;};"
        "d.addEventListener('transitionend',go,{once:true});"
        "setTimeout(go,260);"
        "});"
        "});"
        "})();</script>"
        "</body></html>"
    )


# ─── friendly-time formatting (used by Home + Activity + trace pages) ────


def _clock_time(ts: float) -> str:
    """Local 12-hour clock time, e.g. ``4:51 PM``. Returns ``''`` on
    timestamp errors so callers can degrade silently. Uses ``%I`` + lstrip
    rather than ``%-I`` so this is Windows-portable (POSIX-only directive)."""
    try:
        formatted = datetime.fromtimestamp(ts, UTC).astimezone().strftime("%I:%M %p")
        return formatted.lstrip("0") if formatted.startswith("0") else formatted
    except (ValueError, OSError):
        return ""


def _iso_local(ts: float) -> str:
    """Absolute timestamp for the ``title`` tooltip attribute."""
    try:
        return datetime.fromtimestamp(ts, UTC).astimezone().isoformat(timespec="seconds")
    except (ValueError, OSError):
        return ""


def friendly_time(ts: float, *, now: float | None = None) -> str:
    """Graduated relative + clock-time timestamp (Part III §23, iteration 1
    Task 2). Buckets:

    - ``Just now`` (< 60 s)
    - ``5 minutes ago`` (< 1 h)
    - ``An hour ago`` (< 2 h)
    - ``3h ago`` (< 24 h)
    - ``Yesterday at 4:51 PM`` (< 48 h)
    - ``Last Tuesday at 12:05 PM`` (< 7 d)
    - ``Mar 12 at 8:14 AM`` (older)

    The clock-time on the older buckets disambiguates a same-day event from a
    next-day one — *"yesterday at noon"* reads very differently from *"yesterday
    at 11 PM."* Local timezone, 12-hour format (en-US familiar; falls back to
    24-h on platforms where strftime can't strip the leading zero).
    """
    if now is None:
        now = datetime.now(UTC).timestamp()
    delta = max(0.0, now - ts)
    if delta < 60:
        return "Just now"
    if delta < 3600:
        m = int(delta // 60)
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if delta < 7200:
        return "An hour ago"
    if delta < 86400:
        h = int(delta // 3600)
        return f"{h}h ago"
    clock = _clock_time(ts)
    suffix = f" at {clock}" if clock else ""
    if delta < 172800:
        return f"Yesterday{suffix}"
    if delta < 604800:
        try:
            weekday = datetime.fromtimestamp(ts, UTC).astimezone().strftime("%A")
            return f"Last {weekday}{suffix}"
        except (ValueError, OSError):
            return f"Earlier this week{suffix}"
    try:
        month_day = (
            datetime.fromtimestamp(ts, UTC).astimezone().strftime("%b %d").replace(" 0", " ")
        )
        return f"{month_day}{suffix}"
    except (ValueError, OSError):
        return f"Older{suffix}"


def friendly_time_html(ts: float, *, now: float | None = None) -> str:
    """``friendly_time(ts)`` wrapped in a ``<span title="ISO timestamp">``
    so hovering shows the absolute time. Convenience for renderers that
    consistently want the tooltip."""
    label = friendly_time(ts, now=now)
    iso = _iso_local(ts)
    if iso:
        return f"<span title='{_e(iso)}'>{_e(label)}</span>"
    return _e(label)


# Backwards-compat alias for any caller still using the old name; subject to
# removal once all call sites are migrated.
relative_time = friendly_time
