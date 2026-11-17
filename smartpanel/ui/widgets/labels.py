"""Small labelled indicators: Pill, ActivityGlyph, LevelChip, NavBadge (SP-0009, SP-2109).

State is never colour-only: a pill carries a word, a glyph has a distinct shape
and an accessible name ("Needs you"), a chip says "Warning".
"""
from __future__ import annotations

from smartpanel.ui import shellmodel
from smartpanel.ui.widgets._gi import Gtk


def _swap_class(widget: Gtk.Widget, prefix: str, new: str | None, current: str | None) -> str | None:
    if current == new:
        return current
    if current:
        widget.remove_css_class(f"{prefix}{current}")
    if new:
        widget.add_css_class(f"{prefix}{new}")
    return new


def _set_a11y_label(widget: Gtk.Widget, text: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


class ActivityGlyph(Gtk.Label):
    """❓ ▶ · ◌ ⏳ coloured by state, spoken as 'Needs you' / 'Running' / …"""

    def __init__(self, state: str | None = None):
        super().__init__()
        self.add_css_class("sp-glyph")
        self._css: str | None = None
        self._state: str | None = object()  # force first update
        self.set_state(state)

    def set_state(self, state: str | None) -> None:
        if state == self._state:
            return
        self._state = state
        meta = shellmodel.activity_meta(state)
        self.set_text(meta.glyph)
        self._css = _swap_class(self, "sp-glyph-", meta.css, self._css)
        self.set_tooltip_text(meta.label)
        _set_a11y_label(self, meta.label)

    @property
    def state(self) -> str | None:
        return self._state


class Pill(Gtk.Label):
    """Rounded state word. `set_activity(state)` for sessions, or set_text_style(text, css)."""

    def __init__(self, text: str = "", css: str | None = None):
        super().__init__(label=text)
        self.add_css_class("sp-pill")
        self.set_valign(Gtk.Align.CENTER)
        self._css: str | None = None
        self._css = _swap_class(self, "sp-pill-", css, None)

    def set_activity(self, state: str | None) -> None:
        meta = shellmodel.activity_meta(state)
        self.set_text_style(meta.label, meta.css)

    def set_text_style(self, text: str, css: str | None) -> None:
        if self.get_text() != text:
            self.set_text(text)
        self._css = _swap_class(self, "sp-pill-", css, self._css)


class LevelChip(Gtk.Label):
    """Fine / Warning / Critical / Not configured."""

    def __init__(self, level: int | None = 0, na_text: str = "Not configured"):
        super().__init__()
        self.add_css_class("sp-chip")
        self.set_valign(Gtk.Align.CENTER)
        self._css: str | None = None
        self._na_text = na_text
        self.set_level(level)

    def set_level(self, level: int | None, na_text: str | None = None) -> None:
        if na_text is not None:
            self._na_text = na_text
        text, css = shellmodel.level_chip(level, na_text=self._na_text)
        if self.get_text() != text:
            self.set_text(text)
        self._css = _swap_class(self, "sp-chip-", css, self._css)


class NavBadge(Gtk.Label):
    """Sidebar count / '!' / '·' bubble. Hidden when there's nothing to say."""

    def __init__(self):
        super().__init__()
        self.add_css_class("sp-badge")
        self.set_valign(Gtk.Align.CENTER)
        self.set_visible(False)
        self._css: str | None = None
        self._badge: shellmodel.Badge | None = None

    def set_badge(self, badge: shellmodel.Badge | None) -> bool:
        """Returns True if anything changed."""
        if badge == self._badge:
            return False
        self._badge = badge
        if badge is None:
            self.set_visible(False)
            _set_a11y_label(self, "")
            return True
        self.set_text(badge.text)
        self._css = _swap_class(self, "", badge.css, self._css)
        self.set_tooltip_text(badge.a11y)
        _set_a11y_label(self, badge.a11y)
        self.set_visible(True)
        return True

    @property
    def badge(self) -> shellmodel.Badge | None:
        return self._badge
