from __future__ import annotations

import logging
from collections import defaultdict

from gi.repository import GLib

from smartpanel import sensors
from smartpanel.indicator import Indicator
from smartpanel.theme.model import Theme

log = logging.getLogger(__name__)

# Categories polled regardless of the theme, because something other than a
# menu row depends on them. SWAP drives the near-full alert (top-bar badge +
# desktop notification), which has to work on a stock turing-smart-screen theme
# that has never heard of it. Interval in seconds.
ALWAYS_POLL: dict[str, float] = {"SWAP": 10.0}


class Daemon:
    """Schedules sensor polling on the GLib main loop.

    One repeating timer per (category, interval). Each category also gets one
    synchronous tick at start() time so the menu shows real values immediately
    rather than starting with placeholder dashes.
    """

    def __init__(self, theme: Theme, indicator: Indicator):
        self.theme = theme
        self.indicator = indicator
        self._timer_ids: list[int] = []

    def start(self) -> None:
        groups: dict[tuple[str, float], None] = {}
        for m in self.theme.metrics:
            groups.setdefault((m.category, m.interval), None)

        # Add the always-on categories, but only if the theme hasn't already
        # scheduled them — otherwise a theme with a SWAP row would get two
        # timers and double the sensor reads.
        themed = {c for (c, _) in groups}
        for category, interval in ALWAYS_POLL.items():
            if category not in themed:
                groups.setdefault((category, interval), None)

        for (category, interval) in groups:
            # Prime: synchronous tick before entering the main loop so the
            # menu/top-bar render with real data on first display.
            self._tick(category)
            tid = GLib.timeout_add_seconds(
                int(max(1, interval)),
                lambda c=category: self._tick(c),
            )
            self._timer_ids.append(tid)
            log.debug("scheduled %s every %.1fs (timer=%d)", category, interval, tid)

    def stop(self) -> None:
        for tid in self._timer_ids:
            GLib.source_remove(tid)
        self._timer_ids.clear()

    def _tick(self, category: str) -> bool:
        values = sensors.read(category)
        if log.isEnabledFor(logging.DEBUG):
            preview = ", ".join(
                f"{k}={_brief(v)}" for k, v in list(values.items())[:5]
            )
            log.debug("tick %s -> %s", category, preview)
        self.indicator.update(category, values)
        return True  # keep timer alive


def _brief(v) -> str:
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, str) and len(v) > 40:
        return v[:37] + "..."
    return str(v)
