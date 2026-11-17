"""Banner: an Adw.Banner with a level style (SP-0009, SP-0106).

`Adw.Banner` is a final type, so this is an `Adw.Bin` holding one; the inner
banner (`.banner`) carries the `sp-banner-warn` / `sp-banner-crit` class.
"""
from __future__ import annotations

from typing import Callable

from smartpanel.state.model import LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING
from smartpanel.ui.widgets._gi import Adw

_CLASS = {LEVEL_WARNING: "sp-banner-warn", LEVEL_CRITICAL: "sp-banner-crit"}


class Banner(Adw.Bin):
    """`show(text, level, action_label)` / `hide()`; both dedupe so a per-poll call
    neither flickers nor re-announces itself."""

    def __init__(self, on_action: Callable[[], None] | None = None):
        super().__init__()
        self.banner = Adw.Banner()
        self.banner.set_use_markup(False)
        self.set_child(self.banner)
        self._level = LEVEL_OK
        self._key: tuple | None = None
        self._on_action = on_action
        self.banner.connect("button-clicked", self._clicked)

    def show(self, text: str, level: int = LEVEL_WARNING, action_label: str | None = None) -> bool:
        key = (text, level, action_label)
        if key == self._key:
            return False
        self._key = key
        self.banner.set_title(text)
        self.banner.set_button_label(action_label or None)
        if level != self._level:
            if self._level in _CLASS:
                self.banner.remove_css_class(_CLASS[self._level])
            if level in _CLASS:
                self.banner.add_css_class(_CLASS[level])
            self._level = level
        self.banner.set_revealed(True)
        return True

    def hide(self) -> bool:
        if self._key is None:
            return False
        self._key = None
        self.banner.set_revealed(False)
        return True

    @property
    def revealed(self) -> bool:
        return self.banner.get_revealed()

    @property
    def text(self) -> str:
        return self.banner.get_title() if self._key else ""

    @property
    def action_label(self) -> str | None:
        return self._key[2] if self._key else None

    @property
    def level(self) -> int:
        return self._level if self._key else LEVEL_OK

    def set_on_action(self, fn: Callable[[], None] | None) -> None:
        self._on_action = fn

    def _clicked(self, *_):
        if self._on_action:
            self._on_action()
