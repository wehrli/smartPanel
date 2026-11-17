"""Sparkline and HourBars: Gtk.DrawingArea wrappers over ui.charts (SP-0009/0010)."""
from __future__ import annotations

from typing import Sequence

from smartpanel.ui import charts, tokens
from smartpanel.ui.widgets._gi import Adw, Gtk


class _ChartArea(Gtk.DrawingArea):
    """Redraws on data change and on light/dark flips; hidden from screen readers
    (the owning card carries the spoken value)."""

    def __init__(self, width: int, height: int):
        super().__init__(accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.set_content_width(width)
        self.set_content_height(height)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        # Follow light/dark only while realized, so a dropped widget isn't kept
        # alive by the StyleManager's handler.
        self._style_handler = 0
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)

    def _on_realize(self, *_):
        if not self._style_handler:
            self._style_handler = Adw.StyleManager.get_default().connect(
                "notify::dark", lambda *_: self.queue_draw())

    def _on_unrealize(self, *_):
        if self._style_handler:
            Adw.StyleManager.get_default().disconnect(self._style_handler)
            self._style_handler = 0

    def _colors(self) -> dict[str, str]:
        return tokens.chart_colors(Adw.StyleManager.get_default().get_dark())

    def _draw(self, _area, cr, width, height):  # pragma: no cover - painting
        raise NotImplementedError


class Sparkline(_ChartArea):
    """Area + line + end dot, optional dashed thresholds [(60, 'warn'), (85, 'crit')]."""

    def __init__(self, width: int = 160, height: int = 30, vmax: float | None = None):
        super().__init__(width, height)
        self._values: list[float] = []
        self._vmax = vmax
        self._thresholds: list[tuple[float, str]] = []

    def set_values(self, values: Sequence[float | None], vmax: float | None = ...,
                   thresholds: Sequence[tuple[float, str]] | None = None) -> None:
        vals = [v for v in values if v is not None]
        changed = vals != self._values
        self._values = vals
        if vmax is not ...:
            changed |= vmax != self._vmax
            self._vmax = vmax
        if thresholds is not None and list(thresholds) != self._thresholds:
            self._thresholds = list(thresholds)
            changed = True
        if changed:
            self.queue_draw()

    def set_thresholds(self, thresholds: Sequence[tuple[float, str]]) -> None:
        self.set_values(self._values, thresholds=thresholds)

    @property
    def values(self) -> list[float]:
        return list(self._values)

    def _draw(self, _area, cr, width, height):
        charts.paint_sparkline(cr, width, height, self._values, colors=self._colors(),
                               vmax=self._vmax, thresholds=self._thresholds)


class HourBars(_ChartArea):
    """Hourly spend bars; the highlighted bar is "now" (default: the last one)."""

    def __init__(self, width: int = 240, height: int = 46):
        super().__init__(width, height)
        self._values: list[float] = []
        self._highlight: int | None = -1

    def set_values(self, values: Sequence[float | None], highlight: int | None = -1) -> None:
        vals = [0.0 if v is None else float(v) for v in values]
        if vals != self._values or highlight != self._highlight:
            self._values, self._highlight = vals, highlight
            self.queue_draw()

    @property
    def values(self) -> list[float]:
        return list(self._values)

    def _draw(self, _area, cr, width, height):
        charts.paint_bars(cr, width, height, self._values, colors=self._colors(),
                          highlight=self._highlight)
