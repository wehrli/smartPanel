"""MetricCard: caption, big value, optional chart, sub-line, level border (SP-0009)."""
from __future__ import annotations

from typing import Sequence

from smartpanel.state.model import LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING
from smartpanel.ui.widgets._gi import Gtk
from smartpanel.ui.widgets.chart import Sparkline
from smartpanel.ui.widgets.labels import LevelChip

_LEVEL_CLASS = {LEVEL_WARNING: "sp-level-warn", LEVEL_CRITICAL: "sp-level-crit"}


class MetricCard(Gtk.Box):
    """Every setter is a no-op when the value is unchanged, so pages can call
    them on every store notification without causing redraws."""

    def __init__(self, title: str, *, chart_width: int = 160, chart_height: int = 30,
                 vmax: float | None = None, chart: bool = True, chip: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("sp-card")
        self.add_css_class("sp-metric-card")

        head = Gtk.Box(spacing=6)
        self._title = Gtk.Label(label=title.upper(), xalign=0, hexpand=True)
        self._title.add_css_class("sp-cap")
        head.append(self._title)
        self.chip: LevelChip | None = None
        if chip:
            self.chip = LevelChip(LEVEL_OK)
            head.append(self.chip)
        self.append(head)

        self._value = Gtk.Label(label="—", xalign=0)
        self._value.add_css_class("sp-metric-value")
        self.append(self._value)

        self.sparkline: Sparkline | None = None
        if chart:
            self.sparkline = Sparkline(chart_width, chart_height, vmax=vmax)
            self.sparkline.set_margin_top(2)
            self.sparkline.set_margin_bottom(2)
            self.append(self.sparkline)

        self._sub = Gtk.Label(label="", xalign=0, wrap=True)
        self._sub.add_css_class("sp-muted")
        self._sub.add_css_class("caption")
        self.append(self._sub)
        self._level = LEVEL_OK
        self._na = False
        self._title_text = title
        self._sync_a11y()

    # ── setters ──
    def set_title(self, title: str) -> None:
        if title != self._title_text:
            self._title_text = title
            self._title.set_text(title.upper())
            self._sync_a11y()

    def set_value(self, text: str) -> None:
        if self._value.get_text() != text:
            self._value.set_text(text)
            self._sync_a11y()

    def set_sub(self, text: str) -> None:
        if self._sub.get_text() != text:
            self._sub.set_text(text)
            self._sub.set_visible(bool(text))

    def set_history(self, values: Sequence[float | None], *,
                    thresholds: Sequence[tuple[float, str]] | None = None) -> None:
        if self.sparkline is not None:
            self.sparkline.set_values(values, thresholds=thresholds)

    def set_level(self, level: int | None) -> None:
        level = LEVEL_OK if level is None else level
        if level == self._level:
            return
        if self._level in _LEVEL_CLASS:
            self.remove_css_class(_LEVEL_CLASS[self._level])
        if level in _LEVEL_CLASS:
            self.add_css_class(_LEVEL_CLASS[level])
        self._level = level
        self._sync_a11y()

    def set_chip(self, level: int | None, na_text: str | None = None) -> None:
        if self.chip is not None:
            self.chip.set_level(level, na_text)

    def set_na(self, na: bool) -> None:
        """Greyed 'not available' look (no swap, no GPU)."""
        if na != self._na:
            self._na = na
            (self.add_css_class if na else self.remove_css_class)("sp-na")

    # ── read-back (tests, a11y) ──
    @property
    def value_text(self) -> str:
        return self._value.get_text()

    @property
    def sub_text(self) -> str:
        return self._sub.get_text()

    @property
    def level(self) -> int:
        return self._level

    def _sync_a11y(self) -> None:
        lvl = {LEVEL_WARNING: ", warning", LEVEL_CRITICAL: ", critical"}.get(self._level, "")
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{self._title_text} {self._value.get_text()}{lvl}"])
