"""Command palette window, B5 (SP-2203, SP-2205, SP-0111).

    PaletteWindow(app, store, actions, config).present(token=None)

An undecorated window holding a search field ("Type a session or an action…"),
up to nine results and a status footer:

    🔍  Type a session or an action…                              Esc
    ❓  Answer · Fix swap alert hysteresis           smartPanel      ⏎
    ▶  Focus · Migrate the pricing table             ledgerly
    …
    Swap 12% · 4 sessions · 1 waiting · $23.84 today       ↑↓ move · ⏎ run

The list comes from `smartpanel.commands` (pure, unit-tested): this module only
draws it. ↑↓ move the selection (clamped, like the prototype), Enter or a click
runs the command, Esc closes. The window hides as soon as it loses focus and
before a command runs, so a dialog the command opens gets the focus.

Rows are nine widgets built once and updated in place; a store change while the
palette is up re-filters the list (keeping the selected command if it is still
there) and refreshes the footer.

Focus on open (SP-2205): the window grabs the entry on every `present`. When the
launcher passed an activation token (`smartpanel --palette` from a GNOME
shortcut forwards it in the command line's platform data, which GtkApplication
applies on its own), `present(token)` also sets it explicitly, so GNOME raises
the palette instead of showing "smartPanel is ready".
"""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from smartpanel import commands  # noqa: E402
from smartpanel.ui import tokens  # noqa: E402
from smartpanel.ui.widgets import settings_palette_css as css  # noqa: E402

log = logging.getLogger(__name__)

PLACEHOLDER = "Type a session or an action…"
WIDTH = 560
REFRESH_SECTIONS = {"sessions", "system", "spend", "journal", "settings", "app"}


def highlight_markup(title: str, ranges: list[tuple[int, int]], color: str) -> str:
    """Pango markup for `title` with `ranges` in bold accent colour."""
    out, pos = [], 0
    for a, b in ranges:
        a, b = max(a, pos), min(b, len(title))
        if a >= b:
            continue
        out.append(GLib.markup_escape_text(title[pos:a]))
        out.append(f'<span foreground="{color}" weight="bold">'
                   f"{GLib.markup_escape_text(title[a:b])}</span>")
        pos = b
    out.append(GLib.markup_escape_text(title[pos:]))
    return "".join(out)


class _Row(Gtk.ListBoxRow):
    def __init__(self):
        super().__init__()
        box = Gtk.Box(spacing=10)
        self.glyph = Gtk.Label(xalign=0.5, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.glyph.add_css_class("sp-sp-pal-glyph")
        self.title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                               use_markup=True)
        self.sub = Gtk.Label(xalign=1, ellipsize=Pango.EllipsizeMode.END, max_width_chars=22)
        self.sub.add_css_class("sp-sp-pal-sub")
        self.enter = Gtk.Label(label="⏎", accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.enter.add_css_class("sp-sp-kbd")
        # Keep the ⏎ slot's width when hidden so text doesn't shift as the
        # selection moves.
        self.enter.set_opacity(0)
        for w in (self.glyph, self.title, self.sub, self.enter):
            box.append(w)
        self.set_child(box)
        self.command: commands.Command | None = None

    def show_command(self, cmd: commands.Command, ranges, color: str) -> None:
        self.command = cmd
        self.glyph.set_text(cmd.glyph)
        markup = highlight_markup(cmd.title, ranges, color)
        if self.title.get_label() != markup:
            self.title.set_markup(markup)
        self.sub.set_text(cmd.subtitle or "")
        self.sub.set_visible(bool(cmd.subtitle))
        label = cmd.title + (f", {cmd.subtitle}" if cmd.subtitle else "")
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def set_selected_mark(self, on: bool) -> None:
        self.enter.set_opacity(1 if on else 0)
        if on:
            self.add_css_class("sp-sp-sel")
        else:
            self.remove_css_class("sp-sp-sel")


class PaletteWindow(Adw.Window):
    def __init__(self, app, store, actions, config):
        tokens.install()
        css.install()
        super().__init__(application=app, title="Command palette", modal=False,
                         resizable=False, default_width=WIDTH, hide_on_close=True,
                         decorated=False)
        self.add_css_class("sp-sp-palette")
        # Adw.Window's default 360×200 minimum would leave a blank band under
        # the footer when only a few rows match.
        self.set_size_request(WIDTH, -1)
        self.store, self.actions, self.config = store, actions, config
        self._results: list[tuple[commands.Command, list]] = []
        self._sel = 0
        self._was_active = False

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        top = Gtk.Box(spacing=10)
        top.add_css_class("sp-sp-pal-input")
        self.entry = Gtk.SearchEntry(placeholder_text=PLACEHOLDER, hexpand=True,
                                     search_delay=0)
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], ["Search"])
        top.append(self.entry)
        esc = Gtk.Label(label="Esc", valign=Gtk.Align.CENTER)
        esc.add_css_class("sp-sp-kbd")
        esc.set_tooltip_text("Esc closes the palette")
        top.append(esc)
        root.append(top)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("sp-sp-pal-list")
        self.list.update_property([Gtk.AccessibleProperty.LABEL], ["Commands"])
        self._rows = [_Row() for _ in range(commands.MAX_RESULTS)]
        for row in self._rows:
            self.list.append(row)
        root.append(self.list)

        self.none = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.none.add_css_class("sp-sp-pal-none")
        root.append(self.none)

        foot = Gtk.Box(spacing=16)
        foot.add_css_class("sp-sp-pal-foot")
        self.status = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        foot.append(self.status)
        keys = Gtk.Label(label=commands.KEYS_HINT, xalign=1)
        foot.append(keys)
        root.append(foot)
        self.set_content(root)

        self.entry.connect("search-changed", self._on_search)
        self.entry.connect("activate", lambda *_: self._run_selected())
        self.entry.connect("stop-search", lambda *_: self.close())
        self.list.connect("row-activated", self._on_row_activated)
        keys_ctl = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys_ctl.connect("key-pressed", self._on_key)
        self.add_controller(keys_ctl)
        self.connect("notify::is-active", self._on_active)
        self._unsubscribe = store.subscribe(self._on_store_changed)
        self._style_handler = Adw.StyleManager.get_default().connect(
            "notify::dark", lambda *_: self._render())
        self.connect("destroy", self._on_destroy)

        self._refresh(keep=False)

    # ───────────────────────── data ─────────────────────────

    def _commands(self) -> list[commands.Command]:
        try:
            return commands.build_commands(self.store.state, self.config)
        except Exception:  # noqa: BLE001 — a registry bug must not kill the palette
            log.exception("building palette commands failed")
            return []

    def _refresh(self, keep: bool = True) -> None:
        selected = self.selected_command.id if keep and self.selected_command else None
        self._results = commands.filter_commands(self._commands(), self.entry.get_text())
        self._sel = 0
        if selected is not None:
            for i, (cmd, _r) in enumerate(self._results):
                if cmd.id == selected:
                    self._sel = i
                    break
        self._render()
        self._update_status()

    def _render(self) -> None:
        color = tokens.palette(tokens.is_dark())["accent_text"]
        n = len(self._results)
        for i, row in enumerate(self._rows):
            if i < n:
                cmd, ranges = self._results[i]
                row.show_command(cmd, ranges, color)
                row.set_selected_mark(i == self._sel)
                row.set_visible(True)
            else:
                row.command = None
                row.set_visible(False)
        self.list.set_visible(n > 0)
        self.none.set_visible(n == 0)
        if n == 0:
            self.none.set_text(f"Nothing matches “{self.entry.get_text().strip()}”")

    def _update_status(self) -> None:
        text = " · ".join(commands.status_parts(self.store.state))
        if self.status.get_text() != text:
            self.status.set_text(text)

    @property
    def results(self) -> list[commands.Command]:
        return [c for c, _r in self._results]

    @property
    def selected_command(self) -> commands.Command | None:
        if 0 <= self._sel < len(self._results):
            return self._results[self._sel][0]
        return None

    # ───────────────────────── events ─────────────────────────

    def _on_store_changed(self, sections: set[str]) -> None:
        if not sections & REFRESH_SECTIONS:
            return
        if self.get_visible():
            self._refresh(keep=True)
        else:
            self._update_status()

    def _on_search(self, *_):
        self._refresh(keep=False)

    def move(self, delta: int) -> None:
        if not self._results:
            return
        self._sel = max(0, min(len(self._results) - 1, self._sel + delta))
        for i, row in enumerate(self._rows):
            if row.get_visible():
                row.set_selected_mark(i == self._sel)

    def _on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self.move(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self.move(-1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            self._run_selected()
            return True
        return False

    def _on_row_activated(self, _list, row) -> None:
        if isinstance(row, _Row) and row.command is not None:
            self._run(row.command)

    def _on_active(self, *_):
        if self.is_active():
            self._was_active = True
        elif self._was_active and self.get_visible():
            self.close()

    def _run_selected(self) -> None:
        cmd = self.selected_command
        if cmd is not None:
            self._run(cmd)

    def _run(self, cmd: commands.Command) -> None:
        self.close()
        try:
            cmd.run(self.actions)
        except Exception:  # noqa: BLE001
            log.exception("palette command %s failed", cmd.id)

    def close(self) -> None:  # noqa: D401 — Gtk API
        self._was_active = False
        super().close()

    # ───────────────────────── presenting (SP-2205) ─────────────────────────

    def present(self, token: str | None = None) -> None:  # noqa: D401 — Gtk API
        self.entry.set_text("")
        self._refresh(keep=False)
        if token:
            try:
                self.set_startup_id(token)
            except Exception:  # noqa: BLE001
                log.debug("set_startup_id failed", exc_info=True)
        super().present()
        self.entry.grab_focus()

    def present_with_token(self, token: str | None) -> None:
        self.present(token)

    def _on_destroy(self, *_):
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._style_handler:
            Adw.StyleManager.get_default().disconnect(self._style_handler)
            self._style_handler = 0
