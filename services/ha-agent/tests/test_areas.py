"""Areas (Part V) — store + page + form."""

from __future__ import annotations

import pytest
from kukiihome_ha_agent.area_store import Area, AreaStore, slug_for
from kukiihome_ha_agent.web_ui.areas import (
    parse_area_form,
    render_area_form,
    render_areas_list,
)


@pytest.fixture
def store():
    s = AreaStore(path=None)
    yield s
    s.close()


# ─── slug ──────────────────────────────────────────────────────────


def test_slug_for_basic_normalization():
    assert slug_for("Front Porch") == "front_porch"
    assert slug_for("Pool!") == "pool"
    assert slug_for("").startswith("area_")


# ─── store CRUD ─────────────────────────────────────────────────────


def test_create_assigns_slug_and_persists_cameras(store):
    a = store.create(Area(
        id="", name="Pool", attention_mode="attention",
        cameras=["pool_main", "pool_pump"],
    ))
    assert a.id == "pool"
    fetched = store.get(a.id)
    assert fetched is not None
    assert fetched.attention_mode == "attention"
    assert set(fetched.cameras) == {"pool_main", "pool_pump"}


def test_create_collision_suffixes_id(store):
    a1 = store.create(Area(id="", name="Pool"))
    a2 = store.create(Area(id="", name="Pool"))
    assert a1.id == "pool"
    assert a2.id.startswith("pool_") and a2.id != "pool"


def test_update_replaces_camera_set(store):
    a = store.create(Area(id="", name="Pool",
                           cameras=["pool_main", "pool_pump"]))
    store.update(a.id, cameras=["new_only"])
    out = store.get(a.id)
    assert out.cameras == ["new_only"]


def test_update_preserves_unrelated_fields(store):
    a = store.create(Area(id="", name="Pool",
                           attention_mode="attention",
                           description="east end"))
    store.update(a.id, name="Pool deck")
    out = store.get(a.id)
    assert out.name == "Pool deck"
    assert out.attention_mode == "attention"  # not nuked
    assert out.description == "east end"


def test_soft_delete_hides_from_default_list(store):
    a = store.create(Area(id="", name="Old"))
    store.soft_delete(a.id)
    assert all(x.id != a.id for x in store.all_areas())
    assert any(x.id == a.id for x in store.all_areas(include_retired=True))


def test_area_for_camera_reverse_lookup(store):
    store.create(Area(id="", name="Pool", cameras=["pool_main"]))
    store.create(Area(id="", name="Backyard", cameras=["pool_main", "yard"]))
    out = store.area_for_camera("pool_main")
    names = {a.name for a in out}
    assert names == {"Pool", "Backyard"}


def test_area_for_camera_filters_retired(store):
    a = store.create(Area(id="", name="Old", cameras=["cam"]))
    store.soft_delete(a.id)
    assert store.area_for_camera("cam") == []


def test_persist_to_disk_survives_reopen(tmp_path):
    db = tmp_path / "areas.db"
    s1 = AreaStore(path=str(db))
    s1.create(Area(id="", name="Pool", attention_mode="attention",
                    cameras=["pool_main"]))
    s1.close()
    s2 = AreaStore(path=str(db))
    out = s2.all_areas()
    assert len(out) == 1 and out[0].name == "Pool"
    assert out[0].cameras == ["pool_main"]
    s2.close()


# ─── list page rendering ────────────────────────────────────────────


def test_areas_list_empty_state():
    html = render_areas_list([])
    assert "<h1>Areas</h1>" in html
    assert "No areas defined" in html
    assert "+ New area" in html


def test_areas_list_renders_each_area_with_mode_chip_and_counts():
    areas = [
        Area(id="pool", name="Pool", attention_mode="attention",
             cameras=["pool_main"], role="shared"),
        Area(id="back", name="Backyard", attention_mode="normal",
             cameras=["yard", "shed"]),
    ]
    html = render_areas_list(areas)
    assert "Pool" in html and "Backyard" in html
    # mode chips
    assert "attention" in html and "normal" in html
    # camera counts pluralize correctly
    assert "1 camera" in html
    assert "2 cameras" in html
    # role line only when set
    assert "shared" in html


def test_areas_list_sorts_alphabetically():
    areas = [Area(id="z", name="Zone Z"), Area(id="a", name="Alpha")]
    html = render_areas_list(areas)
    assert html.index("Alpha") < html.index("Zone Z")


def test_areas_list_html_escapes_names():
    html = render_areas_list([Area(id="x", name="<bad>")])
    assert "<bad>" not in html
    assert "&lt;bad&gt;" in html


# ─── form rendering ────────────────────────────────────────────────


def test_new_form_includes_all_modes_and_roles():
    html = render_area_form(None)
    assert "New area" in html
    for m in ("normal", "attention", "unattended"):
        assert f"value='{m}'" in html
    for r in ("public", "shared", "private"):
        assert f"value='{r}'" in html
    # default mode 'normal' is checked
    assert "value='normal' checked" in html
    # action posts to create endpoint
    assert "action='areas'" in html


def test_edit_form_preserves_attention_mode_and_cameras():
    area = Area(
        id="pool", name="Pool", attention_mode="attention",
        role="shared", cameras=["pool_main"],
    )
    html = render_area_form(area, available_cameras=[
        ("pool_main", "Pool Camera"),
        ("front", "Front Camera"),
    ])
    assert "Edit area" in html
    # Selected mode is checked
    assert "value='attention' checked" in html
    # Role 'shared' is selected
    assert "value='shared' checked" in html
    # pool_main checkbox is pre-checked; front is not
    assert "value='pool_main' checked" in html
    assert "value='front'" in html
    assert "value='front' checked" not in html


def test_edit_form_shows_delete_button():
    area = Area(id="pool", name="Pool")
    html = render_area_form(area)
    assert "Delete" in html
    assert f"action='areas/{area.id}/delete'" in html


# ─── form parsing ──────────────────────────────────────────────────


def test_parse_area_form_minimum():
    out = parse_area_form({"name": "Pool"})
    assert out["name"] == "Pool"
    assert out["attention_mode"] == "normal"
    assert out["role"] is None
    assert out["cameras"] == []


def test_parse_area_form_with_attention_and_cameras():
    class M(dict):
        def getall(self, key, default):
            v = self.get(key)
            if v is None:
                return default
            return v if isinstance(v, list) else [v]

    out = parse_area_form(M({
        "name": "Pool",
        "attention_mode": "attention",
        "role": "shared",
        "cameras": ["pool_main", "pool_pump"],
    }))
    assert out["attention_mode"] == "attention"
    assert out["role"] == "shared"
    assert out["cameras"] == ["pool_main", "pool_pump"]


def test_parse_area_form_bad_mode_falls_back_to_normal():
    out = parse_area_form({"name": "x", "attention_mode": "ultra"})
    assert out["attention_mode"] == "normal"


def test_parse_area_form_bad_role_becomes_none():
    out = parse_area_form({"name": "x", "role": "garbage"})
    assert out["role"] is None


def test_parse_area_form_empty_role_is_none():
    out = parse_area_form({"name": "x", "role": ""})
    assert out["role"] is None


def test_parse_area_form_missing_name_raises():
    with pytest.raises(ValueError):
        parse_area_form({"name": "  "})
