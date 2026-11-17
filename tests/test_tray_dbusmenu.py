"""dbusmenu layout model: GVariant encoding, diffing (SP-2002), method dispatch — no bus."""
from __future__ import annotations

import pytest
from gi.repository import GLib

from smartpanel.tray import dbusmenu, menu
from smartpanel.tray.dbusmenu import (
    DbusMenuServer, MenuModel, MenuNode, layout_variant, structure,
)
from tests.fixtures import tray_states as fx

NOW = fx.NOW


def tree(label="A", enabled=True, extra=False):
    kids = [MenuNode(1, {"label": label, "enabled": enabled}), dbusmenu.separator(2),
            MenuNode(3, {"label": "HUD", "toggle-type": "checkmark", "toggle-state": 1})]
    if extra:
        kids.append(MenuNode(4, {"label": "new"}))
    return dbusmenu.root(kids)


def test_layout_variant_signature_and_content():
    v = layout_variant(tree())
    assert v.get_type_string() == "(ia{sv}av)"
    nid, props, children = v.unpack()
    assert nid == 0 and props == {"children-display": "submenu"}
    assert [c[0] for c in children] == [1, 2, 3]
    assert children[0][1] == {"label": "A", "enabled": True}
    assert children[1][1] == {"type": "separator", "visible": True}
    # typed correctly for the client: toggle-state is an int32
    raw = v.get_child_value(2).get_child_value(2).get_variant()
    assert raw.get_child_value(1).lookup_value("toggle-state", None).get_type_string() == "i"


def test_layout_depth_and_property_filter():
    nid, props, children = layout_variant(tree(), depth=0).unpack()
    assert children == []
    _, _, children = layout_variant(tree(), depth=1, names=["label"]).unpack()
    assert children[0][1] == {"label": "A"}
    assert children[1][1] == {}


def test_structure_ignores_labels_but_not_ids_or_types():
    assert structure(tree("A")) == structure(tree("B", enabled=False))
    assert structure(tree()) != structure(tree(extra=True))


def test_model_emits_props_only_when_structure_same():
    m = MenuModel(tree())
    assert m.set_root(tree()) is None
    kind, updated, removed = m.set_root(tree("B", enabled=False))
    assert kind == MenuModel.PROPS
    assert updated == [(1, {"label": "B", "enabled": False})] and removed == []
    assert m.revision == 1


def test_model_layout_on_structure_change():
    m = MenuModel(tree())
    assert m.set_root(tree(extra=True)) == (MenuModel.LAYOUT, 2)


def test_removed_properties_reported():
    m = MenuModel(tree())
    t = tree()
    del t.children[2].props["toggle-type"]
    del t.children[2].props["toggle-state"]
    kind, updated, removed = m.set_root(t)
    assert removed == [(3, ["toggle-type", "toggle-state"])]


def test_updated_variant_signature():
    v = dbusmenu.updated_variant([(1, {"label": "x", "visible": False})], [(3, ["toggle-type"])])
    assert v.get_type_string() == "(a(ia{sv})a(ias))"


def test_escape_label():
    assert dbusmenu.escape_label("a_b") == "a__b"


# ── the real menu across state changes (SP-2002) ──

def test_real_menu_never_relayouts_across_scenarios():
    m = MenuModel(menu.build_menu(fx.busy(), None, NOW))
    for make in fx.SCENARIOS.values():
        change = m.set_root(menu.build_menu(make(), {"bar.ticker": True}, NOW))
        assert change is None or change[0] == MenuModel.PROPS
    assert m.revision == 1


def test_only_changed_rows_are_sent():
    m = MenuModel(menu.build_menu(fx.busy(), None, NOW))
    st = fx.busy()
    st.system.cpu.percent = 57
    _, updated, removed = m.set_root(menu.build_menu(st, None, NOW))
    assert updated == [(menu.ROW_IDS["sys.cpu"], {"label": "CPU 57%"})] and not removed


# ── server dispatch without a bus ──

def make_server():
    events = []
    srv = DbusMenuServer("/test/Menu", on_event=lambda *a: events.append(a))
    srv.set_root(menu.build_menu(fx.busy(), None, NOW))
    return srv, events


def test_get_layout_full():
    srv, _ = make_server()
    v = srv.handle("GetLayout", (0, -1, ["type", "children-display"]))
    assert v.get_type_string() == "(u(ia{sv}av))"
    rev, (nid, props, children) = v.unpack()
    assert rev == srv.model.revision and nid == 0 and len(children) == len(menu.ROW_KEYS)


def test_get_layout_unknown_parent():
    srv, _ = make_server()
    with pytest.raises(LookupError):
        srv.handle("GetLayout", (999, -1, []))


def test_get_group_properties_all_and_filtered():
    srv, _ = make_server()
    (out,) = srv.handle("GetGroupProperties", ([1, 2, 999], [])).unpack()
    assert [i for i, _ in out] == [1, 2]
    assert out[0][1]["label"] == "System" and out[0][1]["enabled"] is False
    (out,) = srv.handle("GetGroupProperties", ([1], ["label"])).unpack()
    assert out == [(1, {"label": "System"})]
    (out,) = srv.handle("GetGroupProperties", ([], [])).unpack()
    assert len(out) == len(menu.ROW_KEYS) + 1


def test_get_property_and_defaults():
    srv, _ = make_server()
    assert srv.handle("GetProperty", (2, "label")).unpack() == ("CPU 23%",)
    assert srv.handle("GetProperty", (2, "type")).unpack() == ("standard",)
    with pytest.raises(LookupError):
        srv.handle("GetProperty", (2, "icon-name"))


def test_events_dispatch():
    srv, events = make_server()
    srv.handle("Event", (5, "clicked", GLib.Variant("i", 0), 42))
    assert events == [(5, "clicked", 42)]
    (errors,) = srv.handle("EventGroup", ([(5, "clicked", GLib.Variant("i", 0), 1),
                                           (999, "clicked", GLib.Variant("i", 0), 1)],)).unpack()
    assert errors == [999] and events[-1] == (5, "clicked", 1)


def test_about_to_show():
    srv, _ = make_server()
    assert srv.handle("AboutToShow", (0,)).unpack() == (False,)
    assert srv.handle("AboutToShowGroup", ([0, 1, 999],)).unpack() == ([], [999])


def test_event_handler_exception_is_contained():
    srv = DbusMenuServer("/t", on_event=lambda *a: 1 / 0)
    srv.set_root(tree())
    srv.handle("Event", (1, "clicked", GLib.Variant("i", 0), 0))   # no raise


def test_set_root_reports_signal_kind():
    srv = DbusMenuServer("/t")
    srv.set_root(tree())
    assert srv.set_root(tree("Z")) == "ItemsPropertiesUpdated"
    assert srv.set_root(tree(extra=True)) == "LayoutUpdated"
    assert srv.set_root(tree(extra=True)) is None
