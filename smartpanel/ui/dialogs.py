"""Dialog host (SP-2104): modal Adw.Dialogs presented inside the dashboard window.

libadwaita draws the dimmed backdrop and closes a dialog on Esc (SP-2107) by
itself. The host only remembers what is open, so a second request replaces the
first instead of stacking, and so the app can close everything on quit.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


class DialogHost:
    def __init__(self, parent: Gtk.Widget):
        self.parent = parent
        self._current: Adw.Dialog | None = None

    def present(self, dialog: Adw.Dialog, *, replace: bool = True) -> Adw.Dialog:
        if replace and self._current is not None and self._current is not dialog:
            self._current.force_close()
        self._current = dialog
        dialog.connect("closed", self._on_closed)
        dialog.present(self.parent)
        return dialog

    @property
    def current(self) -> Adw.Dialog | None:
        return self._current

    def close_all(self) -> None:
        if self._current is not None:
            d, self._current = self._current, None
            d.force_close()

    def _on_closed(self, dialog: Adw.Dialog) -> None:
        if dialog is self._current:
            self._current = None
