"""Server-rendered "Review" page for the add-on's ingress Web UI.

The identity Inbox (Build #292 / Epic 10): the cameras have been embedding +
persisting every person/pet they see; this page surfaces those un-named tracks
so the operator can *label* one — which builds a template and retroactively
resolves every past + future appearance. The one screen that turns the
always-embed loop into something usable.

Pure rendering + form parsing here (unit-testable, no I/O); the aiohttp
handlers in ``__main__`` do the HTTP + call the preprocessor client. All URLs
are RELATIVE so the page resolves identically under HA Ingress
(``/api/hassio_ingress/<token>/review``) and direct port-8765 access — same
trick the per-alert page uses.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

_KIND_GLYPH = {"person": "🧍", "pet": "🐾", "dog": "🐕", "cat": "🐈"}

_STYLE = """
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;
  background:#0f1115;color:#e6e6e6}
header{padding:14px 18px;background:#161a21;border-bottom:1px solid #262c36;
  display:flex;align-items:center;gap:14px}
header h1{font-size:18px;margin:0}
header a{color:#8ab4f8;text-decoration:none;font-size:14px}
.wrap{padding:18px;max-width:1100px;margin:0 auto}
.flash{background:#16361f;border:1px solid #2e7d46;color:#b6f0c6;padding:10px 14px;
  border-radius:8px;margin-bottom:16px}
.notice{background:#2a2410;border:1px solid #6b5a1e;color:#f0e2b6;padding:12px 16px;
  border-radius:8px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:#9aa7b8;
  margin:24px 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px}
.card{background:#161a21;border:1px solid #262c36;border-radius:10px;overflow:hidden;
  display:flex;flex-direction:column}
.card img{width:100%;height:150px;object-fit:cover;background:#0a0c10}
.card .meta{padding:10px 12px;font-size:13px}
.card .sub{color:#9aa7b8;font-size:12px;margin-top:2px}
.badge{display:inline-block;background:#222936;border-radius:6px;padding:1px 6px;
  font-size:11px;color:#9fc1ff;margin-right:4px}
.resolved{color:#7fe0a0;font-weight:600}
.lowconf{color:#f0c674}
form.label{display:flex;gap:6px;padding:0 12px 12px}
form.label input[type=text]{flex:1;min-width:0;background:#0c0f14;border:1px solid #2a3140;
  color:#e6e6e6;border-radius:6px;padding:6px 8px;font-size:13px}
form.label button{background:#2e6ad1;border:0;color:#fff;border-radius:6px;
  padding:6px 10px;font-size:13px;cursor:pointer}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{background:#161a21;border:1px solid #262c36;border-radius:20px;padding:6px 12px;
  font-size:13px}
.empty{color:#7e8a9a;padding:24px 0}
form.reject{padding:0 12px 12px}
form.reject button{background:transparent;border:1px solid #5a3340;color:#e69aae;
  border-radius:6px;padding:5px 9px;font-size:12px;cursor:pointer;width:100%}
form.merge{display:flex;align-items:center;gap:8px;margin-top:14px;flex-wrap:wrap;
  background:#13171e;border:1px solid #262c36;border-radius:8px;padding:10px 12px}
form.merge select{background:#0c0f14;border:1px solid #2a3140;color:#e6e6e6;
  border-radius:6px;padding:5px 8px;font-size:13px}
form.merge button{background:#3a2f6a;border:0;color:#fff;border-radius:6px;
  padding:6px 12px;font-size:13px;cursor:pointer}
form.merge span{color:#9aa7b8;font-size:13px}
.card a{display:block}
.detail{max-width:540px}
.clip{width:100%;max-width:360px;border-radius:10px;border:1px solid #262c36;
  background:#0a0c10;display:block;margin:0 auto 16px}
.cand{display:flex;align-items:center;gap:10px;margin:8px 0}
.cand form{margin:0}
.cand button{background:#23502f;border:1px solid #2e7d46;color:#cdeccf;border-radius:6px;
  padding:7px 12px;font-size:14px;cursor:pointer;white-space:nowrap}
.cand .score{color:#9aa7b8;font-size:13px;white-space:nowrap}
.bar{height:6px;border-radius:3px;background:#222936;flex:1;max-width:120px;overflow:hidden}
.bar > i{display:block;height:100%;background:#3a7bd5}
.divider{color:#5b6675;text-align:center;margin:16px 0}
"""


def _e(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _hms(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts), UTC).astimezone().strftime("%H:%M:%S")
    except (ValueError, OSError, TypeError):
        return "?"


def _track_card(t: dict) -> str:
    eid = _e(t.get("event_id", ""))
    tid = _e(t.get("track_id", ""))
    glyph = _KIND_GLYPH.get(t.get("kind", ""), "•")
    cam = _e(t.get("camera_id", "?"))
    when = _hms(t.get("t1", 0))
    nframes = _e(t.get("n_frames", 0))
    mods = "".join(f"<span class='badge'>{_e(m)}</span>" for m in t.get("modalities", []))
    thumb = f"review/thumb/{eid}/{tid}.jpg"

    if t.get("status") == "resolved":
        conf = t.get("confidence")
        conf_txt = f" {conf:.2f}" if isinstance(conf, (int, float)) else ""
        cls = "resolved" + (" lowconf" if isinstance(conf, (int, float)) and conf < 0.7 else "")
        action = (
            f"<div class='meta'><span class='{cls}'>✓ {_e(t.get('subject_name') or '?')}"
            f"{_e(conf_txt)}</span></div>"
            # split-to-unknown: wrong merge → back to the queue, then re-label.
            "<form class='reject' method='post' action='review/reject'>"
            f"<input type='hidden' name='event_id' value='{eid}'>"
            f"<input type='hidden' name='track_id' value='{tid}'>"
            "<button type='submit' title='Not them — return to queue'>✗ not them</button>"
            "</form>"
        )
    else:
        action = (
            "<form class='label' method='post' action='review/label'>"
            f"<input type='hidden' name='event_id' value='{eid}'>"
            f"<input type='hidden' name='track_id' value='{tid}'>"
            "<input type='text' name='name' placeholder='name…' autocomplete='off' required>"
            "<button type='submit'>Label</button></form>"
        )
    # Thumbnail links to the track-detail page (animated clip + candidates).
    detail_url = f"review-track?e={quote(str(t.get('event_id', '')))}&t={quote(str(t.get('track_id', '')))}"
    return (
        "<div class='card'>"
        f"<a href='{detail_url}' title='Open track — animated, with candidates'>"
        f"<img src='{thumb}' alt='track {tid}' loading='lazy'></a>"
        f"<div class='meta'>{glyph} <b>{cam}</b> {when}"
        f"<div class='sub'>{nframes} frames · {mods or '—'}</div></div>"
        f"{action}</div>"
    )


def _subject_chip(s: dict) -> str:
    glyph = _KIND_GLYPH.get(s.get("kind", ""), "•")
    name = _e(s.get("display_name", "?"))
    mods = " ".join(s.get("modalities", [])) or "—"
    seen = _e(s.get("appearances", 0))
    species = s.get("species")
    extra = f" ({_e(species)})" if species else ""
    return f"<span class='chip'>{glyph} <b>{name}</b>{extra} · {_e(mods)} · {seen} seen</span>"


def _merge_form(subjects: list[dict]) -> str:
    """Merge two same-subject labels. Only shown with ≥2 subjects of the same
    kind (cross-kind merges are rejected server-side anyway)."""
    if len(subjects) < 2:
        return ""
    opts = "".join(
        f"<option value='{_e(s.get('subject_id'))}'>{_e(s.get('display_name'))}</option>"
        for s in subjects
    )
    return (
        "<form class='merge' method='post' action='review/merge'>"
        "<span>Same subject? Merge</span>"
        f"<select name='from_id'>{opts}</select>"
        "<span>→</span>"
        f"<select name='into_id'>{opts}</select>"
        "<button type='submit'>Merge</button>"
        "</form>"
    )


def render_review_html(
    tracks: list[dict],
    subjects: list[dict],
    *,
    configured: bool,
    flash: str | None = None,
) -> str:
    """The full Review page. ``configured=False`` (no preprocessor_url) renders
    a setup notice instead of the queue."""
    flash_html = f"<div class='flash'>{_e(flash)}</div>" if flash else ""

    if not configured:
        body = (
            "<div class='notice'>Identity Review needs a preprocessor. Set "
            "<b>preprocessor_url</b> in the add-on options to the inference box "
            "(e.g. <code>http://192.168.x.x:8090</code>) and enable its identity "
            "API (<code>KUKIIHOME_PREPROCESSOR_DETECTION_DB_PATH</code>).</div>"
        )
    else:
        unresolved = [t for t in tracks if t.get("status") != "resolved"]
        resolved = [t for t in tracks if t.get("status") == "resolved"]
        subj_html = (
            "<div class='chips'>" + "".join(_subject_chip(s) for s in subjects) + "</div>"
            + _merge_form(subjects)
            if subjects else "<div class='empty'>No one enrolled yet.</div>"
        )
        body = (
            "<h2>To review · unnamed tracks</h2>"
            + (
                "<div class='grid'>" + "".join(_track_card(t) for t in unresolved) + "</div>"
                if unresolved else "<div class='empty'>Nothing to review — all caught up.</div>"
            )
            + "<h2>People &amp; Pets</h2>" + subj_html
            + (
                "<h2>Resolved</h2><div class='grid'>"
                + "".join(_track_card(t) for t in resolved) + "</div>"
                if resolved else ""
            )
        )

    # Body-only HTML; the route handler wraps this in render_shell() so the
    # global nav + sticky header come for free. Page-specific styles travel
    # with the body in an inline <style> block.
    return (
        f"<style>{_STYLE}</style>"
        "<div class='wrap'>"
        "<h1>Identity Review</h1>"
        f"{flash_html}{body}"
        "</div>"
    )


def _candidate_row(detail: dict, c: dict) -> str:
    """One ranked candidate: a one-tap Confirm + a similarity bar."""
    e, t = _e(detail.get("event_id", "")), _e(detail.get("track_id", ""))
    name = _e(c.get("name", "?"))
    score = c.get("score", 0.0)
    modality = _e(c.get("modality", ""))
    pct = int(max(0.0, min(1.0, score if isinstance(score, (int, float)) else 0.0)) * 100)
    return (
        "<div class='cand'>"
        "<form method='post' action='review/label'>"
        f"<input type='hidden' name='event_id' value='{e}'>"
        f"<input type='hidden' name='track_id' value='{t}'>"
        f"<input type='hidden' name='name' value='{name}'>"
        f"<button type='submit'>Confirm {name}</button></form>"
        f"<div class='bar'><i style='width:{pct}%'></i></div>"
        f"<span class='score'>{_e(f'{score:.2f}') if isinstance(score, (int, float)) else '?'}"
        f" ({modality})</span></div>"
    )


def render_track_detail_html(detail: dict, *, flash: str | None = None) -> str:
    """The track-detail page: the whole track animated (padded crops) + the
    small-gallery candidate ranking with one-tap Confirm — the fix for "one
    crop isn't enough to tell who this is.\""""
    e, t = _e(detail.get("event_id", "")), _e(detail.get("track_id", ""))
    clip = f"review-track-clip?e={quote(str(detail.get('event_id', '')))}" \
           f"&t={quote(str(detail.get('track_id', '')))}"
    glyph = _KIND_GLYPH.get(detail.get("kind", ""), "•")
    cam = _e(detail.get("camera_id", "?"))
    nframes = _e(detail.get("n_frames", 0))
    mods = "".join(f"<span class='badge'>{_e(m)}</span>" for m in detail.get("modalities", []))
    flash_html = f"<div class='flash'>{_e(flash)}</div>" if flash else ""

    status_html = ""
    if detail.get("status") == "resolved":
        conf = detail.get("confidence")
        conf_txt = f" {conf:.2f}" if isinstance(conf, (int, float)) else ""
        status_html = (
            f"<p><span class='resolved'>✓ {_e(detail.get('subject_name') or '?')}"
            f"{_e(conf_txt)}</span>"
            "<form class='reject' method='post' action='review/reject' "
            "style='display:inline;margin-left:10px'>"
            f"<input type='hidden' name='event_id' value='{e}'>"
            f"<input type='hidden' name='track_id' value='{t}'>"
            "<button type='submit'>✗ not them</button></form></p>"
        )

    cands = detail.get("candidates", [])
    if cands:
        margin = detail.get("margin")
        margin_html = (
            f"<div class='score'>top-2 margin {margin:.2f}</div>"
            if isinstance(margin, (int, float)) else ""
        )
        cand_html = (
            "<h2>We think this is…</h2>"
            + "".join(_candidate_row(detail, c) for c in cands)
            + margin_html
        )
    else:
        cand_html = "<div class='empty'>No one enrolled to compare against yet.</div>"

    label_new = (
        "<div class='divider'>— or —</div>"
        "<form class='label' method='post' action='review/label'>"
        f"<input type='hidden' name='event_id' value='{e}'>"
        f"<input type='hidden' name='track_id' value='{t}'>"
        "<input type='text' name='name' placeholder='label as someone new…' "
        "autocomplete='off' required><button type='submit'>Label new</button></form>"
    )

    body = (
        f"<img class='clip' src='{clip}' alt='track {t} clip'>"
        f"<p>{glyph} <b>{cam}</b> · {nframes} frames · {mods or '—'}</p>"
        f"{status_html}{cand_html}{label_new}"
    )
    # Body-only HTML; route handler wraps in render_shell().
    return (
        f"<style>{_STYLE}</style>"
        "<div class='wrap detail'>"
        "<h1>Track</h1>"
        "<p class='sub'><a href='review'>← back to Identity Review</a></p>"
        f"{flash_html}{body}"
        "</div>"
    )


def parse_label_form(form: dict[str, str]) -> dict[str, Any] | None:
    """Validate the label form into a /identity/label payload, or None if the
    required fields are missing."""
    event_id = (form.get("event_id") or "").strip()
    track_id = (form.get("track_id") or "").strip()
    name = (form.get("name") or "").strip()
    if not (event_id and track_id and name):
        return None
    payload: dict[str, Any] = {"event_id": event_id, "track_id": track_id, "name": name}
    if form.get("kind"):
        payload["kind"] = form["kind"]
    if form.get("species"):
        payload["species"] = form["species"]
    return payload


def parse_reject_form(form: dict[str, str]) -> dict[str, str] | None:
    event_id = (form.get("event_id") or "").strip()
    track_id = (form.get("track_id") or "").strip()
    if not (event_id and track_id):
        return None
    return {"event_id": event_id, "track_id": track_id}


def parse_merge_form(form: dict[str, str]) -> dict[str, str] | None:
    from_id = (form.get("from_id") or "").strip()
    into_id = (form.get("into_id") or "").strip()
    if not (from_id and into_id) or from_id == into_id:
        return None
    return {"from_id": from_id, "into_id": into_id}
