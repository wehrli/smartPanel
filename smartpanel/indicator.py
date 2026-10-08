from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator, GLib, Gtk  # noqa: E402

from smartpanel import alerts, notify
from smartpanel.dashboard import Dashboard
from smartpanel.render import (
    format_top_bar,
    format_value,
    render_bar,
    render_radial,
    render_sparkline,
)
from smartpanel.theme.model import MetricRender, Theme
from smartpanel.uiformat import MULTI_ROW_METRICS

log = logging.getLogger(__name__)

# Sizing hint for the panel. Must cover the widest label a preset can render
# (swap gauge included) or the label jitters as values change width.
TOP_BAR_GUIDE = "🔴🔃99% · 🧩9 ❓9 ▶9 · $999.99 · 999M"

# Max rows pre-allocated for any multi-row block (per-project, by-model).
MAX_BLOCK_ROWS = 12


class Indicator:
    """AppIndicator + Gtk menu. Menu structure is built ONCE and only labels
    update on subsequent ticks — DBusMenu serialization means custom widgets
    would be stripped anyway, so we lean fully into text+unicode rendering.
    """

    def __init__(self, theme: Theme):
        self.theme = theme
        self.values: dict[str, dict[str, float | str | None]] = {}
        self.history: dict[str, deque[float]] = {}

        # Swap-pressure alert. Owns the hysteresis, so the badge below and the
        # desktop notification can never disagree about the current level.
        self.swap_alert = alerts.AlertTracker()
        # Seed SWAP so a theme binding {SWAP.STATUS} renders the "no reading
        # yet" dash instead of a bare 0 during the few ms before SWAP's first
        # tick (categories are primed one at a time, in theme order).
        self.values["SWAP"] = {"STATUS": self.swap_alert.status,
                               "ALERT": "", "LEVEL": self.swap_alert.level.name}
        # Same reason for SESSIONS: the list is refreshed on its own timer in
        # cli.py, which first runs after the daemon has already primed (and
        # rendered) the theme's categories. Without a seed a theme binding
        # {SESSIONS.STATUS} flashes a bare 0 on startup.
        self.values["SESSIONS"] = {"COUNT": 0.0, "NAMES": "—", "STATUS": "🧩0",
                                   "WAITING": 0.0, "RUNNING": 0.0}

        # Center-screen detail window, opened from the menu / middle-click.
        self.dashboard = Dashboard(theme)

        self.indicator = AppIndicator.Indicator.new(
            "smartpanel",
            "utilities-system-monitor",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_label("smartPanel", TOP_BAR_GUIDE)

        self.menu = Gtk.Menu()
        self._metric_items: dict[tuple[str, str], Gtk.MenuItem] = {}
        self._spark_items: dict[tuple[str, str], Gtk.MenuItem] = {}
        self._block_header: dict[tuple[str, str], Gtk.MenuItem] = {}
        self._block_rows: dict[tuple[str, str], list[Gtk.MenuItem]] = {}

        # Session callbacks, forwarded to the dashboard's Sessions section.
        self._on_close: Callable[[str], None] | None = None
        self._on_refresh: Callable[[str], None] | None = None
        self._on_close_all: Callable[[], None] | None = None
        self._on_focus: Callable[[str], None] | None = None

        self._dashboard_item: Gtk.MenuItem | None = None

        self._build_menu_structure()
        self.indicator.set_menu(self.menu)
        # Left-click (which GNOME routes to the menu) auto-opens the dashboard.
        self.menu.connect("show", self._on_menu_show)
        # Middle-click the tray icon opens the dashboard directly too.
        if self._dashboard_item is not None:
            self.indicator.set_secondary_activate_target(self._dashboard_item)

    # ---- structure: built once ----

    def _build_menu_structure(self) -> None:
        # The menu is a minimal launcher — all detail lives in the dashboard
        # window, which auto-opens when the menu is invoked (see _on_menu_show).
        # These items remain as a fallback on desktops where auto-open/popdown
        # behave differently. The interactive Sessions submenu also stays here.
        self._dashboard_item = Gtk.MenuItem(label="📊  Open dashboard")
        self._dashboard_item.connect(
            "activate", lambda *_: self.dashboard.present_centered()
        )
        self.menu.append(self._dashboard_item)

        new_item = Gtk.MenuItem(label="➕  New session")
        new_item.connect("activate", lambda *_: self.dashboard.new_session())
        self.menu.append(new_item)
        self.menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit smartPanel")
        quit_item.connect("activate", lambda *_: Gtk.main_quit())
        self.menu.append(quit_item)

        self.menu.show_all()

    def _on_menu_show(self, *_args) -> None:
        # When the tray menu is requested (left-click on most GNOME setups),
        # open the dashboard and dismiss the dropdown so the click feels like it
        # opens the window directly. popdown is a harmless no-op on hosts that
        # don't honor it — there the small fallback menu above is shown instead.
        self.dashboard.present_centered()
        GLib.idle_add(self.menu.popdown)

    def _add_metric_row(self, m: MetricRender) -> None:
        item = Gtk.MenuItem(label=_initial_label(m))
        self.menu.append(item)
        self._metric_items[(m.category, m.metric)] = item
        if m.show_line_graph:
            spark = Gtk.MenuItem(label="    ▁▁▁▁▁▁▁▁▁▁")
            self.menu.append(spark)
            self._spark_items[(m.category, m.metric)] = spark

    def _add_block(self, m: MetricRender) -> None:
        header_label = _block_header_label(m)
        header = Gtk.MenuItem(label=header_label)
        self.menu.append(header)
        self._block_header[(m.category, m.metric)] = header

        rows: list[Gtk.MenuItem] = []
        for _ in range(MAX_BLOCK_ROWS):
            row = Gtk.MenuItem(label="")
            self.menu.append(row)
            rows.append(row)
        self._block_rows[(m.category, m.metric)] = rows

    # ---- updates: labels only ----

    def update(self, category: str, values: dict[str, float | str | None]) -> None:
        self.values[category] = values
        log.debug("update %s = %s", category, _abbrev_values(values))

        if category == "SWAP":
            # Publishes SWAP.STATUS/ALERT/LEVEL back into `values` before the
            # top-bar template is rendered below, so a theme can place the
            # gauge itself with `{SWAP.STATUS}` (what the presets do) instead
            # of taking the automatic alert prefix. Same tracker either way.
            self._check_swap(values)

        for m in self.theme.metrics:
            if m.category != category:
                continue
            if m.metric in MULTI_ROW_METRICS:
                self._update_block(m, values.get(m.metric))
                continue
            self._update_metric_label(m, values.get(m.metric))

        self._refresh_top_bar_label()
        self.dashboard.update(category, values)

    def _update_metric_label(self, m: MetricRender, raw) -> None:
        item = self._metric_items.get((m.category, m.metric))
        if item is None:
            return
        parts: list[str] = [m.label.ljust(16)]
        if m.show_text:
            parts.append("—" if raw is None else format_value(m.key, raw, m.show_unit))
        if m.show_graph and isinstance(raw, (int, float)):
            parts.append(render_bar(float(raw), m.min_value, m.max_value))
        if m.show_radial and isinstance(raw, (int, float)):
            parts.append(render_radial(float(raw), m.min_value, m.max_value))
        item.set_label("  ".join(p for p in parts if p))

        if m.show_line_graph and isinstance(raw, (int, float)):
            hist = self.history.setdefault(m.key, deque(maxlen=m.history_size))
            hist.append(float(raw))
            spark = self._spark_items.get((m.category, m.metric))
            if spark is not None and len(hist) > 1:
                spark.set_label("    " + render_sparkline(hist, m.min_value, m.max_value))

    def _update_block(self, m: MetricRender, raw) -> None:
        header = self._block_header.get((m.category, m.metric))
        rows = self._block_rows.get((m.category, m.metric), [])

        if not isinstance(raw, str) or raw in ("—", ""):
            if header is not None:
                header.set_label(_block_header_label(m, empty=True))
            for row in rows:
                row.hide()
            return

        entries = raw.split(" · ")
        if header is not None:
            header.set_label(_block_header_label(m))
        for row, entry in zip(rows, entries):
            row.set_label(f"    {entry}")
            row.show()
        for row in rows[len(entries):]:
            row.hide()

    def _refresh_top_bar_label(self) -> None:
        try:
            text = format_top_bar(self.theme.top_bar_format, self.values)
        except Exception as e:
            log.debug("top_bar_format render failed: %s", e)
            text = "smartPanel"
        text = self._with_alert_badge(text)
        self.indicator.set_label(text, TOP_BAR_GUIDE)

    def _with_alert_badge(self, text: str) -> str:
        """Prefix the swap badge onto the top-bar label while the alert is live.

        Prefixed rather than appended because the panel truncates long labels
        from the right (see CLAUDE.md) — an appended warning is exactly the part
        that would get eaten by the "…". Themes that place `{SWAP.` themselves
        opt out, so the badge never renders twice: the shipped presets carry
        `{SWAP.STATUS}`, which already turns into the alert glyph in place.
        """
        badge = self.swap_alert.badge
        if not badge or "{SWAP." in self.theme.top_bar_format:
            return text
        return f"{badge} {text}" if text else badge

    # ---- swap pressure alert ----

    def _check_swap(self, values: dict[str, float | str | None]) -> None:
        """Feed the tracker, expose its verdict, and notify on a state change."""
        percent = values.get("PERCENTAGE")
        percent = float(percent) if isinstance(percent, (int, float)) else None
        used = _as_float(values.get("USED"))
        total = _as_float(values.get("TOTAL"))

        event = self.swap_alert.update(percent, used, total, now=time.monotonic())
        values["STATUS"] = self.swap_alert.status   # always-on gauge
        values["ALERT"] = self.swap_alert.badge     # empty unless alerting
        values["LEVEL"] = self.swap_alert.level.name

        self.dashboard.set_alert(
            self.swap_alert.level,
            alerts.swap_headline(self.swap_alert.level),
            alerts.swap_detail(self.swap_alert.level, percent, used, total),
        )
        if event is not None:
            log.info("swap alert: %s (%s)", event.summary, event.body)
            notify.send(event.summary, event.body, event.urgency, key="swap")

    # ---- sessions (rendered in the dashboard's dedicated section) ----

    def update_sessions(
        self,
        sessions,
        on_close: Callable[[str], None] | None = None,
        on_close_all: Callable[[], None] | None = None,
        on_refresh: Callable[[str], None] | None = None,
        on_focus: Callable[[str], None] | None = None,
    ) -> None:
        """Forward the live session list to the dashboard's Sessions section.

        Callbacks are remembered, so the refresh timer can pass only the list
        after the first call. There is no tray submenu — sessions live in the
        dashboard window.
        """
        if on_close is not None:
            self._on_close = on_close
        if on_close_all is not None:
            self._on_close_all = on_close_all
        if on_refresh is not None:
            self._on_refresh = on_refresh
        if on_focus is not None:
            self._on_focus = on_focus
        self.dashboard.update_sessions(sessions, self._on_close, self._on_close_all,
                                       self._on_refresh, self._on_focus)


# ---- pure label helpers (module-level for testability) ----

def _initial_label(m: MetricRender) -> str:
    return f"{m.label.ljust(16)}  …"


def _block_header_label(m: MetricRender, empty: bool = False) -> str:
    if m.metric == "PER_PROJECT":
        return "  Top projects today" if not empty else "  (no project usage today)"
    if m.metric == "BY_MODEL":
        return "  By model" if not empty else "  (no model breakdown today)"
    if m.metric == "PRAISE_SUMMARY":
        return "  Gratification" if not empty else "  (no gratification found)"
    if m.metric == "TOP_PRAISE":
        return "  Most frequent compliments" if not empty else "  (none yet)"
    return f"  {m.label}"


def _as_float(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _abbrev_values(values: dict) -> str:
    parts = []
    for k, v in list(values.items())[:4]:
        if isinstance(v, float):
            parts.append(f"{k}={v:.2f}")
        elif isinstance(v, str) and len(v) > 30:
            parts.append(f"{k}=<{len(v)}c>")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)
