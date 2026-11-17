"""`org.kde.StatusNotifierItem` exported over Gio, no GTK, no libayatana.

Matches what the Ubuntu AppIndicator extension reads
(`/usr/share/gnome-shell/extensions/ubuntu-appindicators@ubuntu.com/appIndicator.js`):

* Registration: `RegisterStatusNotifierItem(<object path>)` on
  `org.kde.StatusNotifierWatcher`. A service argument starting with '/' makes the
  watcher take the bus name from the sender, so several items (e.g. a test item)
  can share one connection at different paths. We watch the watcher's name and
  re-register whenever it (re)appears - the shell restarting, the extension being
  toggled.
* It ignores items while `Status` is 'Passive' except for `Id`/`Menu`/`Status`,
  so we stay 'Active' and go 'Passive' on stop (the host hides passive items).
* Signals without arguments (`NewIcon`, `NewTitle`, `NewToolTip`) make it re-read
  `<Prop>`, `<Prop>Name`, `<Prop>Pixmap`, `<Prop>AccessibleDesc`. `NewStatus` and
  `XAyatanaNewLabel(label, guide)` carry their value.
* `IconName` that is an absolute path to a wide PNG keeps its aspect ratio (D2).
* Middle click calls `XAyatanaSecondaryActivate(timestamp)` first, then falls back
  to `SecondaryActivate(x, y)`; scroll calls `Scroll(delta, orientation)`.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from gi.repository import Gio, GLib

from smartpanel.tray.dbusmenu import register_object

log = logging.getLogger(__name__)

INTERFACE = "org.kde.StatusNotifierItem"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"

ACTIVE, PASSIVE, NEEDS_ATTENTION = "Active", "Passive", "NeedsAttention"

INTROSPECTION = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="IconAccessibleDesc" type="s" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionAccessibleDesc" type="s" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="XAyatanaLabel" type="s" access="read"/>
    <property name="XAyatanaLabelGuide" type="s" access="read"/>
    <property name="XAyatanaOrderingIndex" type="u" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="XAyatanaSecondaryActivate">
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="ProvideXdgActivationToken">
      <arg name="token" type="s" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
    <signal name="NewIconThemePath">
      <arg name="icon_theme_path" type="s"/>
    </signal>
    <signal name="NewMenu"/>
    <signal name="XAyatanaNewLabel">
      <arg name="label" type="s"/>
      <arg name="guide" type="s"/>
    </signal>
  </interface>
</node>
"""

# Property -> GVariant type.
PROP_TYPES: dict[str, str] = {
    "Category": "s", "Id": "s", "Title": "s", "Status": "s", "WindowId": "i",
    "IconThemePath": "s", "Menu": "o", "ItemIsMenu": "b", "IconName": "s",
    "IconPixmap": "a(iiay)", "IconAccessibleDesc": "s", "OverlayIconName": "s",
    "OverlayIconPixmap": "a(iiay)", "AttentionIconName": "s",
    "AttentionIconPixmap": "a(iiay)", "AttentionAccessibleDesc": "s",
    "AttentionMovieName": "s", "ToolTip": "(sa(iiay)ss)", "XAyatanaLabel": "s",
    "XAyatanaLabelGuide": "s", "XAyatanaOrderingIndex": "u",
}


class StatusNotifierItem:
    """One SNI at `path`. Callbacks: on_activate(x, y), on_secondary(), on_scroll(delta, orient)."""

    def __init__(self, path: str, *, item_id: str = "smartpanel", title: str = "smartPanel",
                 menu_path: str = "/NO_DBUSMENU", icon_name: str = "utilities-terminal-symbolic",
                 on_activate: Callable[[int, int], None] | None = None,
                 on_secondary: Callable[[], None] | None = None,
                 on_scroll: Callable[[int, str], None] | None = None):
        self.path = path
        self.on_activate = on_activate
        self.on_secondary = on_secondary
        self.on_scroll = on_scroll
        self.activation_token: str | None = None
        self.props: dict[str, Any] = {
            "Category": "ApplicationStatus",
            "Id": item_id,
            "Title": title,
            "Status": ACTIVE,
            "WindowId": 0,
            "IconThemePath": "",
            "Menu": menu_path,
            "ItemIsMenu": False,
            "IconName": icon_name,
            "IconPixmap": [],
            "IconAccessibleDesc": title,
            "OverlayIconName": "",
            "OverlayIconPixmap": [],
            "AttentionIconName": icon_name,
            "AttentionIconPixmap": [],
            "AttentionAccessibleDesc": title,
            "AttentionMovieName": "",
            "ToolTip": ("", [], title, ""),
            "XAyatanaLabel": "",
            "XAyatanaLabelGuide": "",
            "XAyatanaOrderingIndex": 0,
        }
        self._conn: Gio.DBusConnection | None = None
        self._reg_id = 0
        self._watch_id = 0
        self.registered = False
        self._info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION).interfaces[0]

    # ── values ──
    def get_variant(self, name: str) -> GLib.Variant | None:
        if name not in self.props:
            return None
        return GLib.Variant(PROP_TYPES[name], self.props[name])

    # ── lifecycle ──
    def export(self, connection: Gio.DBusConnection) -> None:
        self._conn = connection
        self._reg_id = register_object(
            connection, self.path, self._info, self._on_method, self._on_get_property)
        self._watch_id = Gio.bus_watch_name_on_connection(
            connection, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared, self._on_watcher_vanished)

    def unexport(self) -> None:
        if self._conn is None:
            return
        # Passive first: the host hides the item even if our bus name lives on.
        self.set_status(PASSIVE)
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._reg_id:
            try:
                self._conn.unregister_object(self._reg_id)
            except Exception:
                log.debug("SNI unregister failed", exc_info=True)
            self._reg_id = 0
        self._conn = None
        self.registered = False

    def _on_watcher_appeared(self, conn, _name, _owner) -> None:
        conn.call(WATCHER_NAME, WATCHER_PATH, WATCHER_IFACE, "RegisterStatusNotifierItem",
                  GLib.Variant("(s)", (self.path,)), None, Gio.DBusCallFlags.NONE, 5000,
                  None, self._on_registered)

    def _on_registered(self, conn, result) -> None:
        try:
            conn.call_finish(result)
            self.registered = True
            log.info("tray item %s registered with the StatusNotifierWatcher", self.path)
        except GLib.Error as e:
            self.registered = False
            log.warning("StatusNotifierWatcher refused the tray item: %s", e.message)

    def _on_watcher_vanished(self, _conn, _name) -> None:
        if self.registered:
            log.info("StatusNotifierWatcher went away; will re-register when it returns")
        self.registered = False

    # ── setters (emit only on change) ──
    def _set(self, name: str, value: Any) -> bool:
        if self.props.get(name) == value:
            return False
        self.props[name] = value
        return True

    def set_label(self, label: str, guide: str) -> bool:
        changed = self._set("XAyatanaLabel", label)
        changed = self._set("XAyatanaLabelGuide", guide) or changed
        if changed:
            self._emit("XAyatanaNewLabel", GLib.Variant("(ss)", (label, guide)))
        return changed

    def set_icon(self, icon_name: str, accessible_desc: str | None = None) -> bool:
        """Set the icon (and its description); `NewIcon` only when either changed.

        The host re-reads *and reloads* the icon on `NewIcon` (a wide PNG takes the
        uncached `_loadCustomImage` path), so callers must pass a description that
        doesn't move with live readings. `NewAttentionIcon` only matters while the
        item is NeedsAttention, so it is not emitted otherwise.
        """
        changed = self._set("IconName", icon_name)
        attn = self._set("AttentionIconName", icon_name)
        if accessible_desc is not None:
            changed = self._set("IconAccessibleDesc", accessible_desc) or changed
            attn = self._set("AttentionAccessibleDesc", accessible_desc) or attn
        if changed:
            self._emit("NewIcon")
        if attn and self.props.get("Status") == NEEDS_ATTENTION:
            self._emit("NewAttentionIcon")
        return changed

    def set_tooltip(self, title: str, body: str) -> bool:
        if self._set("ToolTip", ("", [], title, body)):
            self._emit("NewToolTip")
            return True
        return False

    def set_title(self, title: str) -> bool:
        if self._set("Title", title):
            self._emit("NewTitle")
            return True
        return False

    def set_status(self, status: str) -> bool:
        if self._set("Status", status):
            self._emit("NewStatus", GLib.Variant("(s)", (status,)))
            return True
        return False

    def _emit(self, signal: str, params: GLib.Variant | None = None) -> None:
        if not self._conn or not self._reg_id:
            return
        try:
            self._conn.emit_signal(None, self.path, INTERFACE, signal, params)
        except Exception:
            log.debug("SNI %s emit failed", signal, exc_info=True)

    # ── D-Bus handlers ──
    def _on_get_property(self, _conn, _sender, _path, _iface, name):
        return self.get_variant(name)

    def _on_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        try:
            self.handle(method, params.unpack())
        except Exception as e:  # never raise into the loop
            log.exception("SNI %s failed", method)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))
            return
        invocation.return_value(None)

    def handle(self, method: str, args: tuple) -> None:
        """Bus-independent dispatch (tests call this directly)."""
        if method == "Activate":
            if self.on_activate:
                self.on_activate(*args)
        elif method in ("SecondaryActivate", "XAyatanaSecondaryActivate"):
            if self.on_secondary:
                self.on_secondary()
        elif method == "Scroll":
            if self.on_scroll:
                self.on_scroll(int(args[0]), str(args[1]))
        elif method == "ProvideXdgActivationToken":
            self.activation_token = args[0]
        elif method == "ContextMenu":
            pass  # the host shows our dbusmenu itself
        else:
            raise NotImplementedError(method)
