"""Track: thin rounded progress bar (budget, disk). Over 100 % turns red."""
from __future__ import annotations

from smartpanel.ui.widgets._gi import Gtk


class Track(Gtk.ProgressBar):
    def __init__(self, fraction: float = 0.0):
        super().__init__()
        self.add_css_class("sp-track")
        self.set_valign(Gtk.Align.CENTER)
        self._over = False
        self.set_percent(fraction * 100)

    def set_percent(self, percent: float | None) -> None:
        pct = 0.0 if percent is None else float(percent)
        frac = max(0.0, min(pct / 100.0, 1.0))
        if abs(self.get_fraction() - frac) > 1e-4:
            self.set_fraction(frac)
        over = pct > 100.0
        if over != self._over:
            self._over = over
            (self.add_css_class if over else self.remove_css_class)("sp-over")
