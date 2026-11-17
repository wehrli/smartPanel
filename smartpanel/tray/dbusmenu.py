"""`com.canonical.dbusmenu` server over Gio, no GTK.

Two halves:

* **Pure model** (`MenuNode`, `MenuModel`, `layout_variant`, ...): the tree, its
  GVariant encoding and the diff between two trees. Unit-tested without a bus.
* **Glue** (`DbusMenuServer`): exports the interface on a `Gio.DBusConnection`,
  answers the host's calls and emits the two signals.

Update policy (SP-2002), matched to the GNOME AppIndicator extension's client
(`dbusMenu.js`): ids are stable, so a new tree with the same *structure* (ids,
order, `type`, `children-display`) only sends `ItemsPropertiesUpdated` for the
properties that actually changed. `LayoutUpdated` - which makes the client re-fetch
the whole tree and can make an open menu flicker - is emitted only when the
structure changes.

Client notes (dbusMenu.js): it asks `GetLayout(0, -1, ['type','children-display'])`
then `GetGroupProperties(ids, [])` (empty list = all properties); labels go through
`label.replace(/_([^_])/, '$1')`, so a literal underscore must be sent doubled
(`escape_label`); `toggle-type: 'checkmark'` + `toggle-state: 1` draws the check;
clicks arrive as `Event(id, 'clicked', <i 0>, timestamp)`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

VERSION = 3

# Property -> GVariant type string (the dbusmenu spec's set).
PROP_TYPES: dict[str, str] = {
    "type": "s",
    "label": "s",
    "enabled": "b",
    "visible": "b",
    "icon-name": "s",
    "icon-data": "ay",
    "shortcut": "aas",
    "toggle-type": "s",
    "toggle-state": "i",
    "children-display": "s",
    "disposition": "s",
    "accessible-desc": "s",
}

# What a client assumes when a property is absent.
PROP_DEFAULTS: dict[str, Any] = {
    "type": "standard", "label": "", "enabled": True, "visible": True,
}

STRUCTURAL = ("type", "children-display")


def escape_label(text: str) -> str:
    """Underscores are mnemonics in dbusmenu labels; '__' is a literal '_'."""
    return text.replace("_", "__")


@dataclass
class MenuNode:
    id: int
    props: dict[str, Any] = field(default_factory=dict)
    children: list["MenuNode"] = field(default_factory=list)

    def walk(self) -> Iterable["MenuNode"]:
        yield self
        for c in self.children:
            yield from c.walk()


def separator(id: int, visible: bool = True) -> MenuNode:
    return MenuNode(id, {"type": "separator", "visible": visible})


def root(children: list[MenuNode]) -> MenuNode:
    return MenuNode(0, {"children-display": "submenu"}, children)


# ───────────────────────── encoding ─────────────────────────

def prop_variant(name: str, value: Any) -> GLib.Variant:
    sig = PROP_TYPES.get(name)
    if sig is None:
        # Unknown extension properties: infer a sane type.
        if isinstance(value, bool):
            sig = "b"
        elif isinstance(value, int):
            sig = "i"
        else:
            sig, value = "s", str(value)
    if sig == "i":
        value = int(value)
    elif sig == "b":
        value = bool(value)
    return GLib.Variant(sig, value)


def props_dict(props: dict[str, Any], names: Iterable[str] | None = None) -> dict[str, GLib.Variant]:
    wanted = set(names) if names else None
    return {k: prop_variant(k, v) for k, v in props.items()
            if v is not None and (wanted is None or k in wanted)}


def _layout_tuple(node: MenuNode, depth: int, names: list[str] | None):
    children = []
    if depth != 0:
        sub = depth - 1 if depth > 0 else -1
        children = [GLib.Variant("(ia{sv}av)", _layout_tuple(c, sub, names))
                    for c in node.children]
    return (node.id, props_dict(node.props, names), children)


def layout_variant(node: MenuNode, depth: int = -1,
                   names: list[str] | None = None) -> GLib.Variant:
    """`(ia{sv}av)` for GetLayout: depth -1 = everything, 0 = the node alone."""
    return GLib.Variant("(ia{sv}av)", _layout_tuple(node, depth, names))


def structure(node: MenuNode) -> tuple:
    """What, if it changes, needs a LayoutUpdated (ids, order, type, submenu-ness)."""
    return (node.id, tuple(node.props.get(k) for k in STRUCTURAL),
            tuple(structure(c) for c in node.children))


def index(node: MenuNode) -> dict[int, dict[str, Any]]:
    return {n.id: {k: v for k, v in n.props.items() if v is not None} for n in node.walk()}


def diff_props(old: dict[int, dict[str, Any]], new: dict[int, dict[str, Any]]):
    """-> (updated [(id, {prop: value})], removed [(id, [prop])]), ids in new order."""
    updated, removed = [], []
    for nid, props in new.items():
        before = old.get(nid, {})
        ch = {k: v for k, v in props.items() if before.get(k) != v}
        gone = [k for k in before if k not in props]
        if ch:
            updated.append((nid, ch))
        if gone:
            removed.append((nid, gone))
    return updated, removed


def updated_variant(updated, removed) -> GLib.Variant:
    """`(a(ia{sv})a(ias))` for ItemsPropertiesUpdated."""
    return GLib.Variant("(a(ia{sv})a(ias))", (
        [(nid, props_dict(p)) for nid, p in updated],
        [(nid, list(keys)) for nid, keys in removed],
    ))


class MenuModel:
    """The current tree plus revision; `set_root` says what to tell the client."""

    LAYOUT = "layout"
    PROPS = "props"

    def __init__(self, node: MenuNode | None = None):
        self.root = node or root([])
        self.revision = 1
        self._index = index(self.root)
        self._structure = structure(self.root)

    def set_root(self, node: MenuNode):
        """-> None (no change) | ('layout', revision) | ('props', updated, removed)."""
        st = structure(node)
        idx = index(node)
        self.root = node
        if st != self._structure:
            self._structure, self._index = st, idx
            self.revision += 1
            return (self.LAYOUT, self.revision)
        updated, removed = diff_props(self._index, idx)
        self._index = idx
        if not updated and not removed:
            return None
        return (self.PROPS, updated, removed)

    def find(self, nid: int) -> MenuNode | None:
        for n in self.root.walk():
            if n.id == nid:
                return n
        return None

    def props_of(self, nid: int) -> dict[str, Any] | None:
        return self._index.get(nid)


# ───────────────────────── D-Bus glue ─────────────────────────

INTERFACE = "com.canonical.dbusmenu"

INTROSPECTION = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
  </interface>
</node>
"""

EventHandler = Callable[[int, str, int], None]   # (id, event_id, timestamp)


class DbusMenuServer:
    """Exports a `MenuModel` at `path`; `on_event(id, event_id, timestamp)` on clicks."""

    def __init__(self, path: str, on_event: EventHandler | None = None):
        self.path = path
        self.model = MenuModel()
        self.on_event = on_event
        self.status = "normal"
        self._conn: Gio.DBusConnection | None = None
        self._reg_id = 0
        self._info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION).interfaces[0]

    # ── lifecycle ──
    def export(self, connection: Gio.DBusConnection) -> None:
        self._conn = connection
        self._reg_id = connection.register_object(
            self.path, self._info, self._on_method, self._on_get_property, None)

    def unexport(self) -> None:
        if self._conn and self._reg_id:
            try:
                self._conn.unregister_object(self._reg_id)
            except Exception:
                log.debug("dbusmenu unregister failed", exc_info=True)
        self._conn, self._reg_id = None, 0

    # ── updates ──
    def set_root(self, node: MenuNode) -> str | None:
        """Install a new tree; emits the minimal signal. Returns what was emitted."""
        change = self.model.set_root(node)
        if change is None:
            return None
        if change[0] == MenuModel.LAYOUT:
            self._emit("LayoutUpdated", GLib.Variant("(ui)", (change[1], 0)))
            return "LayoutUpdated"
        self._emit("ItemsPropertiesUpdated", updated_variant(change[1], change[2]))
        return "ItemsPropertiesUpdated"

    def _emit(self, name: str, params: GLib.Variant) -> None:
        if not self._conn or not self._reg_id:
            return
        try:
            self._conn.emit_signal(None, self.path, INTERFACE, name, params)
        except Exception:
            log.debug("dbusmenu %s emit failed", name, exc_info=True)

    # ── D-Bus handlers ──
    def _on_get_property(self, _conn, _sender, _path, _iface, name):
        return {
            "Version": GLib.Variant("u", VERSION),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", self.status),
            "IconThemePath": GLib.Variant("as", []),
        }.get(name)

    def _on_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        try:
            result = self.handle(method, params.unpack())
        except LookupError as e:
            invocation.return_dbus_error("com.canonical.dbusmenu.Error.InvalidId", str(e))
            return
        except Exception as e:  # never raise into the loop
            log.exception("dbusmenu %s failed", method)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))
            return
        invocation.return_value(result)

    def handle(self, method: str, args: tuple) -> GLib.Variant | None:
        """Bus-independent method dispatch (tests call this directly)."""
        m = self.model
        if method == "GetLayout":
            parent, depth, names = args
            node = m.find(parent)
            if node is None:
                raise LookupError(f"no menu item {parent}")
            lay = _layout_tuple(node, depth, list(names) or None)
            return GLib.Variant("(u(ia{sv}av))", (m.revision, lay))
        if method == "GetGroupProperties":
            ids, names = args
            wanted = list(ids) or [n.id for n in m.root.walk()]
            out = []
            for nid in wanted:
                props = m.props_of(nid)
                if props is not None:
                    out.append((nid, props_dict(props, list(names) or None)))
            return GLib.Variant("(a(ia{sv}))", (out,))
        if method == "GetProperty":
            nid, name = args
            props = m.props_of(nid)
            if props is None:
                raise LookupError(f"no menu item {nid}")
            if name in props:
                value = props[name]
            elif name in PROP_DEFAULTS:
                value = PROP_DEFAULTS[name]
            else:
                raise LookupError(f"item {nid} has no property {name}")
            return GLib.Variant("(v)", (prop_variant(name, value),))
        if method == "Event":
            nid, event_id, _data, ts = args
            if m.props_of(nid) is None:
                raise LookupError(f"no menu item {nid}")
            self._dispatch(nid, event_id, ts)
            return None
        if method == "EventGroup":
            (events,) = args
            errors = []
            for nid, event_id, _data, ts in events:
                if m.props_of(nid) is None:
                    errors.append(nid)
                else:
                    self._dispatch(nid, event_id, ts)
            return GLib.Variant("(ai)", (errors,))
        if method == "AboutToShow":
            (nid,) = args
            if m.props_of(nid) is None:
                raise LookupError(f"no menu item {nid}")
            return GLib.Variant("(b)", (False,))
        if method == "AboutToShowGroup":
            (ids,) = args
            errors = [i for i in ids if m.props_of(i) is None]
            return GLib.Variant("(aiai)", ([], errors))
        raise NotImplementedError(method)

    def _dispatch(self, nid: int, event_id: str, ts: int) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(nid, event_id, ts)
        except Exception:
            log.exception("menu event handler failed for item %s", nid)
