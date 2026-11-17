"""EmptyState: icon (glyph or icon name), title, text, optional action (SP-0009)."""
from __future__ import annotations

from typing import Callable

from smartpanel.ui.widgets._gi import Gtk


class EmptyState(Gtk.Box):
    def __init__(self, title: str = "", text: str = "", *, glyph: str | None = None,
                 icon_name: str | None = None, action_label: str | None = None,
                 on_action: Callable[[], None] | None = None, suggested: bool = True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.add_css_class("sp-empty")
        self._icon_box = Gtk.Box(halign=Gtk.Align.CENTER)
        self._icon_box.add_css_class("sp-empty-icon")
        self._glyph = Gtk.Label(hexpand=True, vexpand=True)
        self._image = Gtk.Image(pixel_size=32, hexpand=True, vexpand=True)
        self._icon_box.append(self._glyph)
        self._icon_box.append(self._image)
        self.append(self._icon_box)

        self._title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._title.add_css_class("sp-empty-title")
        self.append(self._title)
        self._text = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=44)
        self._text.add_css_class("sp-empty-text")
        self.append(self._text)

        self._button = Gtk.Button(halign=Gtk.Align.CENTER)
        self._button.add_css_class("pill")
        self._button.connect("clicked", self._clicked)
        self.append(self._button)
        self._on_action = on_action
        self._suggested = None

        self.set_icon(glyph=glyph, icon_name=icon_name)
        self.set_title(title)
        self.set_text(text)
        self.set_action(action_label, on_action, suggested=suggested)

    def set_icon(self, *, glyph: str | None = None, icon_name: str | None = None) -> None:
        self._glyph.set_text(glyph or "")
        self._glyph.set_visible(bool(glyph))
        if icon_name:
            self._image.set_from_icon_name(icon_name)
        self._image.set_visible(bool(icon_name))
        self._icon_box.set_visible(bool(glyph or icon_name))

    def set_title(self, title: str) -> None:
        if self._title.get_text() != title:
            self._title.set_text(title)
        self._title.set_visible(bool(title))

    def set_text(self, text: str) -> None:
        if self._text.get_text() != text:
            self._text.set_text(text)
        self._text.set_visible(bool(text))

    def set_action(self, label: str | None, on_action: Callable[[], None] | None = None,
                   *, suggested: bool = True) -> None:
        self._on_action = on_action
        self._button.set_visible(bool(label))
        if label:
            self._button.set_label(label)
        if suggested != self._suggested:
            (self._button.add_css_class if suggested else self._button.remove_css_class)(
                "suggested-action")
            self._suggested = suggested

    @property
    def button(self) -> Gtk.Button:
        return self._button

    def _clicked(self, *_):
        if self._on_action:
            self._on_action()
