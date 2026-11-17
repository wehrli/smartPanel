"""SNI property values and method dispatch (no bus), SP-1907 clicks."""
from __future__ import annotations

from gi.repository import GLib

from smartpanel.tray import sni
from smartpanel.tray.sni import StatusNotifierItem


def item(**kw):
    return StatusNotifierItem("/org/ayatana/NotificationItem/smartpanel",
                              menu_path="/org/ayatana/NotificationItem/smartpanel/Menu", **kw)


def test_property_values_and_types():
    it = item()
    v = {k: it.get_variant(k) for k in sni.PROP_TYPES}
    assert v["Id"].unpack() == "smartpanel"
    assert v["Category"].unpack() == "ApplicationStatus"
    assert v["Status"].unpack() == "Active"
    assert v["Title"].unpack() == "smartPanel"
    assert v["ItemIsMenu"].unpack() is False
    assert v["Menu"].get_type_string() == "o"
    assert v["Menu"].unpack() == "/org/ayatana/NotificationItem/smartpanel/Menu"
    assert v["ToolTip"].get_type_string() == "(sa(iiay)ss)"
    assert v["XAyatanaOrderingIndex"].get_type_string() == "u"
    assert v["IconPixmap"].get_type_string() == "a(iiay)"
    assert it.get_variant("Nope") is None


def test_every_property_is_in_introspection():
    from gi.repository import Gio
    info = Gio.DBusNodeInfo.new_for_xml(sni.INTROSPECTION).interfaces[0]
    for name, sig in sni.PROP_TYPES.items():
        p = info.lookup_property(name)
        assert p is not None and p.signature == sig, name
    for m in ("Activate", "SecondaryActivate", "XAyatanaSecondaryActivate", "Scroll",
              "ContextMenu", "ProvideXdgActivationToken"):
        assert info.lookup_method(m) is not None
    sig = info.lookup_signal("XAyatanaNewLabel")
    assert [a.signature for a in sig.args] == ["s", "s"]


def test_setters_report_change_once():
    it = item()
    assert it.set_label("12 · 4", "00 · 00")
    assert not it.set_label("12 · 4", "00 · 00")
    assert it.props["XAyatanaLabel"] == "12 · 4" and it.props["XAyatanaLabelGuide"] == "00 · 00"
    assert it.set_icon("/run/x/bar-1.png", "desc")
    assert not it.set_icon("/run/x/bar-1.png", "desc")
    assert it.props["AttentionIconName"] == "/run/x/bar-1.png"
    assert it.props["IconAccessibleDesc"] == "desc"
    assert it.set_tooltip("smartPanel", "body")
    assert it.get_variant("ToolTip").unpack() == ("", [], "smartPanel", "body")
    assert it.set_status(sni.PASSIVE) and not it.set_status(sni.PASSIVE)


def test_methods_dispatch():
    calls = []
    it = item(on_activate=lambda x, y: calls.append(("activate", x, y)),
              on_secondary=lambda: calls.append(("secondary",)),
              on_scroll=lambda d, o: calls.append(("scroll", d, o)))
    it.handle("Activate", (1, 2))
    it.handle("SecondaryActivate", (3, 4))
    it.handle("XAyatanaSecondaryActivate", (99,))
    it.handle("Scroll", (-120, "vertical"))
    it.handle("ContextMenu", (0, 0))
    it.handle("ProvideXdgActivationToken", ("tok",))
    assert calls == [("activate", 1, 2), ("secondary",), ("secondary",),
                     ("scroll", -120, "vertical")]
    assert it.activation_token == "tok"


def test_unknown_method_raises():
    import pytest
    with pytest.raises(NotImplementedError):
        item().handle("Bogus", ())


def test_emit_without_connection_is_noop():
    it = item()
    it._emit("NewIcon")                      # not exported: silently ignored
    it.set_label("x", "y")
    assert isinstance(it.get_variant("XAyatanaLabel"), GLib.Variant)


def test_new_icon_only_when_icon_or_desc_changes():
    it = item()
    sent = []
    it._emit = lambda name, params=None: sent.append(name)
    it.set_icon("/run/x/bar-1.png", "smartPanel: 4 sessions")
    assert sent == ["NewIcon"]                 # Active: no NewAttentionIcon
    sent.clear()
    it.set_icon("/run/x/bar-1.png", "smartPanel: 4 sessions")
    assert sent == []
    it.set_status(sni.NEEDS_ATTENTION)
    sent.clear()
    it.set_icon("/run/x/bar-2.png", "smartPanel: 4 sessions")
    assert sent == ["NewIcon", "NewAttentionIcon"]
