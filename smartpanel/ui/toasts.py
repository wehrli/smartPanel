"""Toast host (SP-2502): one toast at a time at the bottom of the window.

Text, an optional action button that routes `{label, page}` to the window's
`show_page`, an error style, auto-dismiss after 4 s (6 s for errors). A new toast
replaces the current one. The decisions are in `shellmodel.toast_model`.
"""
from __future__ import annotations

import logging
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from smartpanel.ui import shellmodel  # noqa: E402

log = logging.getLogger(__name__)

ERROR_CLASS = "sp-toast-error"


class ToastHost:
    def __init__(self, overlay: Adw.ToastOverlay, show_page: Callable[[str], None]):
        self.overlay = overlay
        self._show_page = show_page
        self._current: Adw.Toast | None = None
        self.last: shellmodel.ToastModel | None = None

    def show(self, data: dict | None) -> Adw.Toast | None:
        model = shellmodel.toast_model(data)
        if model is None:
            return None
        self.last = model
        if self._current is not None:
            prev, self._current = self._current, None
            prev.dismiss()
        toast = Adw.Toast(title=model.text, timeout=model.timeout)
        toast.set_use_markup(False)
        toast.set_priority(Adw.ToastPriority.HIGH)
        if model.action_label:
            toast.set_button_label(model.action_label)
            page = model.action_page
            toast.connect("button-clicked", lambda *_: page and self._show_page(page))
        (self.overlay.add_css_class if model.error else self.overlay.remove_css_class)(
            ERROR_CLASS)
        toast.connect("dismissed", self._on_dismissed)
        self._current = toast
        self.overlay.add_toast(toast)
        return toast

    def dismiss(self) -> None:
        if self._current is not None:
            self._current.dismiss()

    def _on_dismissed(self, toast: Adw.Toast) -> None:
        if toast is self._current:
            self._current = None
            self.overlay.remove_css_class(ERROR_CLASS)
