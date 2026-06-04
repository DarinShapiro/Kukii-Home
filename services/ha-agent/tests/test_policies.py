"""Policies (Part VII) — store + page."""

from __future__ import annotations

import pytest
from kukiihome_ha_agent.policy_store import Policy, PolicyHit, PolicyStore
from kukiihome_ha_agent.web_ui.policies import render_policies_page

NOW = 1_700_000_000.0


@pytest.fixture
def store():
    s = PolicyStore(path=None)
    yield s
    s.close()


# ─── store ────────────────────────────────────────────────────────


def _dismissal(name="Dog at front", **kw):
    base: dict = dict(  # noqa: C408
        id="", kind="dismissal", name=name,
        descriptor={"camera_id": "front", "kind": "dog"},
        rationale="user ✗-ed similar event",
    )
    base.update(kw)
    return Policy(**base)


def _intent(name="Watch for Bob", **kw):
    base: dict = dict(  # noqa: C408
        id="", kind="transient_intent", name=name,
        descriptor={"actor_id": "bob", "fire_once": True},
    )
    base.update(kw)
    return Policy(**base)


def test_create_assigns_uuid_id(store):
    p = store.create(_dismissal())
    assert p.id.startswith("pol_")
    assert store.get(p.id) is not None


def test_create_preserves_descriptor_json(store):
    p = store.create(_dismissal())
    out = store.get(p.id)
    assert out.descriptor == {"camera_id": "front", "kind": "dog"}


def test_all_policies_filters_by_kind(store):
    store.create(_dismissal())
    store.create(_intent())
    dis = store.all_policies(kind="dismissal")
    ti = store.all_policies(kind="transient_intent")
    assert len(dis) == 1 and dis[0].kind == "dismissal"
    assert len(ti) == 1 and ti[0].kind == "transient_intent"


def test_all_policies_hides_revoked_by_default(store):
    p = store.create(_dismissal())
    store.revoke(p.id)
    assert all(x.id != p.id for x in store.all_policies(kind="dismissal"))
    assert any(
        x.id == p.id for x in store.all_policies(
            kind="dismissal", include_revoked=True,
        )
    )


def test_all_policies_hides_expired_ttl_even_without_revoke(store):
    p = store.create(_dismissal(expires_at=NOW - 60))  # already expired
    assert all(
        x.id != p.id for x in store.all_policies(now_ts=NOW, kind="dismissal")
    )


def test_all_policies_keeps_unexpired_ttl(store):
    p = store.create(_dismissal(expires_at=NOW + 3600))
    assert any(
        x.id == p.id for x in store.all_policies(now_ts=NOW, kind="dismissal")
    )


def test_revoke_then_reinstate_roundtrip(store):
    p = store.create(_dismissal())
    store.revoke(p.id)
    store.reinstate(p.id)
    assert store.get(p.id).revoked_at is None


def test_record_hit_bumps_counter_for_active_outcomes(store):
    p = store.create(_dismissal())
    store.record_hit(PolicyHit(
        policy_id=p.id, incident_id="inc1", applied_at=NOW,
        outcome="dismissed",
    ))
    refreshed = store.get(p.id)
    assert refreshed.apply_count == 1
    assert refreshed.last_applied_at == NOW


def test_record_hit_noop_doesnt_bump_counter(store):
    p = store.create(_dismissal())
    store.record_hit(PolicyHit(
        policy_id=p.id, incident_id="inc1", applied_at=NOW, outcome="noop",
    ))
    assert store.get(p.id).apply_count == 0


def test_hits_for_policy_newest_first(store):
    p = store.create(_dismissal())
    for i, ts in enumerate([NOW - 60, NOW - 30, NOW - 10]):
        store.record_hit(PolicyHit(
            policy_id=p.id, incident_id=f"i{i}", applied_at=ts,
            outcome="dismissed",
        ))
    hits = store.hits_for_policy(p.id)
    assert [h.applied_at for h in hits] == [NOW - 10, NOW - 30, NOW - 60]


def test_hits_for_incident_reverse_link(store):
    p1 = store.create(_dismissal(name="A"))
    p2 = store.create(_dismissal(name="B"))
    store.record_hit(PolicyHit(policy_id=p1.id, incident_id="inc99",
                                applied_at=NOW, outcome="dismissed"))
    store.record_hit(PolicyHit(policy_id=p2.id, incident_id="inc99",
                                applied_at=NOW, outcome="dismissed"))
    hits = store.hits_for_incident("inc99")
    assert {h.policy_id for h in hits} == {p1.id, p2.id}


def test_persist_to_disk_survives_reopen(tmp_path):
    db = tmp_path / "pol.db"
    s1 = PolicyStore(path=str(db))
    s1.create(_dismissal(name="Persisted"))
    s1.close()
    s2 = PolicyStore(path=str(db))
    assert any(p.name == "Persisted" for p in s2.all_policies(kind="dismissal"))
    s2.close()


# ─── page rendering ───────────────────────────────────────────────


def test_render_policies_page_empty_state_explains_creation_flow():
    html = render_policies_page(
        dismissals=[], transient_intents=[], now_ts=NOW,
    )
    assert "<h1>Policies</h1>" in html
    assert "Dismissals" in html
    assert "Transient intents" in html
    # Empty-state copy in both sections
    assert "No dismissals yet" in html
    assert "No transient intents" in html


def test_render_policies_page_lists_with_revoke_buttons():
    pols = [
        Policy(id="p1", kind="dismissal", name="Dog at front",
               rationale="user ✗-ed", apply_count=3, last_applied_at=NOW - 600,
               created_at=NOW - 86400),
        Policy(id="p2", kind="dismissal", name="Wind in tree",
               apply_count=0, created_at=NOW - 100),
    ]
    html = render_policies_page(
        dismissals=pols, transient_intents=[], now_ts=NOW,
    )
    assert "Dog at front" in html
    assert "Wind in tree" in html
    assert "applied 3 times" in html
    assert "applied 0 times" in html
    assert "user ✗-ed" in html
    # revoke form action per policy
    assert "policies/p1/revoke" in html
    assert "policies/p2/revoke" in html


def test_render_policies_page_transient_intents_section_separate():
    intent = Policy(id="ti1", kind="transient_intent",
                     name="Watch for Bob", apply_count=0,
                     created_at=NOW)
    html = render_policies_page(
        dismissals=[], transient_intents=[intent], now_ts=NOW,
    )
    # The intent appears under the transients section heading
    ti_idx = html.index("Transient intents")
    name_idx = html.index("Watch for Bob")
    assert ti_idx < name_idx


def test_render_policies_page_html_escapes_policy_names():
    p = Policy(id="x", kind="dismissal", name="<script>",
                created_at=NOW)
    html = render_policies_page(dismissals=[p], transient_intents=[], now_ts=NOW)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_policies_page_never_applied_label():
    p = Policy(id="p", kind="dismissal", name="Fresh", created_at=NOW)
    html = render_policies_page(dismissals=[p], transient_intents=[], now_ts=NOW)
    assert "never applied" in html


def test_render_policies_page_expires_line_present_when_ttl_set():
    p = Policy(id="p", kind="dismissal", name="TTL'd",
                expires_at=NOW + 3600, created_at=NOW)
    html = render_policies_page(dismissals=[p], transient_intents=[], now_ts=NOW)
    assert "expires" in html
