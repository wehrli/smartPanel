"""Command palette window (B5).

WAVE-1 PLACEHOLDER (WP-ui). WP-pages-c replaces this file in wave 2; keep the API:

    PaletteWindow(app, store, actions, config).present()

This minimal version lists the pages and the session verbs, filters by
substring, runs the selected item on Enter and closes on Esc / focus loss.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from smartpanel.state.model import PAGES  # noqa: E402
from smartpanel.ui import shellmodel, tokens  # noqa: E402


def _items(actions):
    items = [(f"Go to {shellmodel.PAGE_TITLES[p]}", lambda p=p: actions.open_dashboard(p))
             for p in PAGES]
    items += [
        ("New session…", actions.request_new_session),
        ("Close all sessions…", actions.request_close_all),
        ("Sessions board", lambda: actions.open_dashboard("sessions:board")),
    ]
    return items


class PaletteWindow(Adw.Window):
    def __init__(self, app, store, actions, config):
        tokens.install()
        super().__init__(application=app, title="smartPanel palette", modal=False,
                         resizable=False, default_width=560, hide_on_close=True)
        self.store, self.actions, self.config = store, actions, config
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.entry = Gtk.SearchEntry(placeholder_text="Type a command", margin_top=12,
                                     margin_bottom=8, margin_start=12, margin_end=12)
        box.append(self.entry)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.list.add_css_class("navigation-sidebar")
        self._rows = []
        for text, fn in _items(actions):
            row = Gtk.ListBoxRow(child=Gtk.Label(label=text, xalign=0, margin_start=6))
            row.run = fn
            row.text = text.lower()
            self.list.append(row)
            self._rows.append(row)
        self.list.set_filter_func(lambda row: self.entry.get_text().lower() in row.text)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      min_content_height=280)
        scroller.set_child(self.list)
        box.append(scroller)
        self.set_content(box)

        self.entry.connect("search-changed", self._on_search)
        self.entry.connect("activate", lambda *_: self._run_selected())
        self.entry.connect("stop-search", lambda *_: self.close())
        self.list.connect("row-activated", lambda _l, row: self._run(row))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    def present(self):  # noqa: D401 — Gtk API
        self.entry.set_text("")
        self._select_first()
        super().present()
        self.entry.grab_focus()

    def _visible_rows(self):
        return [r for r in self._rows if self.entry.get_text().lower() in r.text]

    def _select_first(self):
        rows = self._visible_rows()
        if rows:
            self.list.select_row(rows[0])

    def _on_search(self, *_):
        self.list.invalidate_filter()
        self._select_first()

    def _on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            rows = self._visible_rows()
            if not rows:
                return True
            cur = self.list.get_selected_row()
            i = rows.index(cur) if cur in rows else -1
            i = (i + (1 if keyval == Gdk.KEY_Down else -1)) % len(rows)
            self.list.select_row(rows[i])
            return True
        return False

    def _run_selected(self):
        row = self.list.get_selected_row()
        if row is not None and row in self._visible_rows():
            self._run(row)

    def _run(self, row):
        self.close()
        row.run()
