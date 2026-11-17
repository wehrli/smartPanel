"""Dashboard page 'spend' (SP-0810, SP-1003..1006, SP-2602, SP-2604).

    header: Today · This week · Lifetime · Cache hits
    ┌ day list (14 rows) ┐  ┌ recessed wrap ─────────────┐
    │ Today        23.84 │  │        [ receipt ]         │
    │ ▬▬▬▬▬▬▬▬▬▬         │  │                            │
    │ Mon 16 Nov   31.02 │  └────────────────────────────┘

While the first transcript scan runs (`app.loading`) the page shows a progress
state instead; with no transcripts at all (`app.first_run`) an empty state with
**New session**. The page subscribes to the store once and updates in place:
labels are re-set only when their text changes. The day selection follows Today
(also across midnight) until the user picks another day, which is then kept by
date across ticks; reopening the window goes back to Today.

API (fixed for every page): build(store, actions, config) -> Gtk.Widget
"""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from smartpanel.ui import tokens  # noqa: E402
from smartpanel.ui.widgets import EmptyState, Track  # noqa: E402
from smartpanel.ui.widgets import spend_journal_css as sjcss  # noqa: E402
from smartpanel.ui.widgets import spend_journal_model as m  # noqa: E402
from smartpanel.ui.widgets.spend_journal_receipt import Receipt  # noqa: E402

DAY_LIST_WIDTH = 200
STACK_BELOW = 560       # px of the day-list + receipt area
HEAD_TWO_BELOW = 640    # px: below this the header cards go 2+2, never 3+1
NARROW_MIN = 280
_WATCH = {"spend", "app", "settings"}


# The page scrolls itself (window.py leaves it alone), so the breakpoint bin can
# sit outside the scroller.
SCROLLS = True


def _scroller(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                            hexpand=True)
    sw.set_child(child)
    return sw


def _set_text(label: Gtk.Label, text: str) -> None:
    if label.get_text() != text:
        label.set_text(text)


def _a11y(widget: Gtk.Widget, text: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


class HeadCard(Gtk.Box):
    """Caption + big tabular number (the prototype's `.spendhead .card`)."""

    def __init__(self, title: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self.add_css_class("sp-card")
        self.add_css_class("sj-head-card")
        self._title = title
        cap = Gtk.Label(label=title.upper(), xalign=0)
        cap.add_css_class("sp-cap")
        self.append(cap)
        self.value = Gtk.Label(label="—", xalign=0)
        self.value.add_css_class("sj-head-value")
        self.append(self.value)
        _a11y(self, f"{title} —")

    def set_value(self, text: str) -> None:
        if self.value.get_text() != text:
            self.value.set_text(text)
            _a11y(self, f"{self._title} {text}")


class DayBar(Gtk.DrawingArea):
    """3 px accent bar, `fraction` of the row's width."""

    def __init__(self):
        super().__init__(hexpand=True)
        self.set_content_height(3)
        self.fraction = 0.0
        self.set_draw_func(self._draw)

    def set_fraction(self, f: float) -> None:
        if abs(f - self.fraction) > 1e-4:
            self.fraction = f
            self.queue_draw()

    def _draw(self, _a, cr, width, height):
        w = width * self.fraction
        if w <= 0:
            return
        w = max(w, 2.0)
        c = tokens.ACCENT.lstrip("#")
        r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
        cr.set_source_rgba(r, g, b, 0.75)
        rad = min(height / 2, 1.5)
        cr.new_sub_path()
        cr.arc(rad, rad, rad, math.pi / 2, 3 * math.pi / 2)
        cr.arc(w - rad, rad, rad, -math.pi / 2, math.pi / 2)
        cr.close_path()
        cr.fill()


class DayRowWidget(Gtk.ListBoxRow):
    def __init__(self):
        super().__init__()
        grid = Gtk.Grid(column_spacing=8, row_spacing=3)
        self.label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.label.add_css_class("sj-day-label")
        self.total = Gtk.Label(xalign=1)
        self.total.add_css_class("sj-day-total")
        self.total.add_css_class("sj-tnum")
        self.bar = DayBar()
        grid.attach(self.label, 0, 0, 1, 1)
        grid.attach(self.total, 1, 0, 1, 1)
        grid.attach(self.bar, 0, 1, 2, 1)
        self.set_child(grid)
        self.row: m.DayRow | None = None

    def set_row(self, row: m.DayRow) -> None:
        if row == self.row:
            return
        self.row = row
        text = GLib.markup_escape_text(row.label)
        if row.sub:
            text += f' <span alpha="70%">{GLib.markup_escape_text(row.sub)}</span>'
        self.label.set_markup(text)
        _set_text(self.total, row.total)
        self.bar.set_fraction(row.fraction)
        _a11y(self, row.a11y)


class SpendPage(Gtk.Stack):
    def __init__(self, store, actions, config):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE,
                         hhomogeneous=False, vhomogeneous=False)
        sjcss.install()
        self.store, self.actions, self.config = store, actions, config
        self._selected_date: str | None = None
        # True until the user picks another day: the selection then follows
        # Today across midnight (the window is hide-on-close and lives for days).
        self._follow_today = True
        self._pending_date: str | None = None    # a route that arrived early
        self._syncing = False
        self._rows: list[DayRowWidget] = []

        self._build_loading()
        self._build_first_run()
        self._build_no_days()
        self._build_main()

        self._unsubscribe = store.subscribe(self._on_change)
        self.connect("destroy", lambda *_: self._unsubscribe())
        self._routed = False          # a route picked the day since the last map
        self._window_closed = False   # the window was hidden since the last map
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.refresh()

    def _on_unmap(self, *_a) -> None:
        root = self.get_root()
        # Hidden with the window (not just another page shown in the stack).
        self._window_closed = root is None or not root.get_visible()

    def _on_map(self, *_a) -> None:
        # Reopening the dashboard lands on Today (SP-1003); switching pages
        # keeps the picked day, and so does a route that chose one.
        if self._window_closed and not self._routed and self._pending_date is None:
            self._follow_today = True
        self._window_closed = self._routed = False
        self.refresh()

    # ── states ──
    def _build_loading(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box.add_css_class("sp-empty")
        icon = Gtk.Box(halign=Gtk.Align.CENTER)
        icon.add_css_class("sp-empty-icon")
        self.spinner = Gtk.Spinner(hexpand=True, vexpand=True, halign=Gtk.Align.CENTER,
                                   valign=Gtk.Align.CENTER)
        self.spinner.set_size_request(26, 26)
        _a11y(self.spinner, m.LOADING_TITLE)
        icon.append(self.spinner)
        box.append(icon)
        title = Gtk.Label(label=m.LOADING_TITLE, wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("sp-empty-title")
        box.append(title)
        self.loading_text = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                      max_width_chars=44)
        self.loading_text.add_css_class("sp-empty-text")
        self.loading_text.add_css_class("sj-tnum")
        box.append(self.loading_text)
        self.loading_track = Track(0.0)
        self.loading_track.set_size_request(260, -1)
        self.loading_track.set_halign(Gtk.Align.CENTER)
        box.append(self.loading_track)
        self.add_named(_scroller(box), "loading")

    def _build_first_run(self) -> None:
        self.first_run = EmptyState(m.FIRST_RUN_TITLE, m.FIRST_RUN_TEXT, glyph="🧾",
                                    action_label="＋ New session",
                                    on_action=self._new_session)
        self.add_named(_scroller(self.first_run), "first")

    def _build_no_days(self) -> None:
        self.no_days = EmptyState(m.NO_DAYS_TITLE, m.NO_DAYS_TEXT, glyph="🧾")
        self.add_named(_scroller(self.no_days), "nodays")

    def _build_main(self) -> None:
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ("top", "bottom", "start", "end"):
            getattr(main, f"set_margin_{side}")(18)

        head = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           min_children_per_line=2, max_children_per_line=4,
                           column_spacing=14, row_spacing=14)
        head.set_can_focus(False)
        self.head = head
        self.card_today = HeadCard("Today")
        self.card_week = HeadCard("This week")
        self.card_life = HeadCard("Lifetime")
        self.card_cache = HeadCard("Cache hits")
        for c in (self.card_today, self.card_week, self.card_life, self.card_cache):
            head.append(c)
            c.get_parent().set_can_focus(False)
        main.append(head)

        recv = Gtk.Box(spacing=14)
        self.recv = recv
        days_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
        days_card.add_css_class("sp-card")
        days_card.set_size_request(DAY_LIST_WIDTH, -1)
        self.days = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.days.add_css_class("sj-days")
        _a11y(self.days, "Days")
        self.days.connect("row-selected", self._on_row_selected)
        days_card.append(self.days)
        recv.append(days_card)

        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                       valign=Gtk.Align.START)
        wrap.add_css_class("sj-wrap")
        self.receipt = Receipt()
        wrap.append(self.receipt)
        recv.append(wrap)
        # Side by side when there's room, stacked in a narrow window.
        main.append(recv)
        # The bin sits outside the scroller so its breakpoint follows the
        # viewport width and the stacked layout is re-measured for scrolling.
        bin_ = Adw.BreakpointBin()
        bin_.set_size_request(NARROW_MIN, 200)
        bin_.set_child(_scroller(main))
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {STACK_BELOW}px"))
        bp.add_setter(recv, "orientation", Gtk.Orientation.VERTICAL)
        bp.add_setter(days_card, "valign", Gtk.Align.FILL)
        bin_.add_breakpoint(bp)
        # Four cards in a row, or 2+2 — never 3+1 (the prototype's .spendhead).
        bp2 = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {HEAD_TWO_BELOW}px"))
        bp2.add_setter(head, "max-children-per-line", 2)
        bin_.add_breakpoint(bp2)
        self.add_named(bin_, "main")

    # ── store ──
    def _on_change(self, sections: set[str]) -> None:
        if sections & _WATCH:
            self.refresh()

    def refresh(self) -> None:
        st = self.store.state
        app = st.app
        if app.loading is not None:
            done, total = app.loading.done, app.loading.total
            _set_text(self.loading_text, m.loading_text(done, total))
            self.loading_track.set_percent(m.loading_fraction(done, total) * 100)
            self.spinner.set_spinning(True)
            self._show("loading")
            return
        self.spinner.set_spinning(False)
        if app.first_run:
            self._show("first")
            return
        if not st.spend.days:
            self._show("nodays")
            return
        sp = st.spend
        self.card_today.set_value(m.money(sp.cost_today))
        self.card_week.set_value(m.money(sp.cost_week))
        self.card_life.set_value(m.money(sp.cost_lifetime))
        self.card_cache.set_value(m.percent(sp.cache_hit_pct))
        self._sync_days()
        self._show("main")

    def _show(self, name: str) -> None:
        if self.get_visible_child_name() != name:
            self.set_visible_child_name(name)

    def _sync_days(self) -> None:
        days = self.store.state.spend.days
        rows = m.day_rows(days)
        self._syncing = True
        try:
            while len(self._rows) < len(rows):
                w = DayRowWidget()
                self._rows.append(w)
                self.days.append(w)
            while len(self._rows) > len(rows):
                self.days.remove(self._rows.pop())
            for w, r in zip(self._rows, rows):
                w.set_row(r)
            if self._pending_date is not None and any(
                    d.date == self._pending_date for d in days):
                self._selected_date, self._pending_date = self._pending_date, None
                self._follow_today = False
            idx = 0 if self._follow_today else m.pick_day(days, self._selected_date)
            if self.days.get_selected_row() is not self._rows[idx]:
                self.days.select_row(self._rows[idx])
            self._selected_date = days[idx].date
        finally:
            self._syncing = False
        self._update_receipt()

    def _on_row_selected(self, _box, row) -> None:
        if row is None or self._syncing:
            return
        days = self.store.state.spend.days
        idx = row.get_index()
        if 0 <= idx < len(days):
            self._selected_date = days[idx].date
            self._follow_today = idx == 0
            self._pending_date = None
            self._update_receipt()

    def _update_receipt(self) -> None:
        st = self.store.state
        days = st.spend.days
        if not days:
            return
        day = days[m.pick_day(days, self._selected_date)]
        self.receipt.set_model(m.receipt_model(day, budget=m.budget_for(st),
                                               printed_at=st.spend.updated_at))

    def _new_session(self) -> None:
        self.actions.request_new_session()

    # ── tests / routing ──
    @property
    def selected_date(self) -> str | None:
        return self._selected_date

    def select_date(self, iso: str) -> bool:
        """Select the day `iso`; if it isn't listed yet (first scan still
        running, or the day hasn't arrived), select it when it does."""
        self._routed = True
        for i, d in enumerate(self.store.state.spend.days):
            if d.date == iso and i < len(self._rows):
                self._pending_date = None
                self._follow_today = i == 0
                self._selected_date = iso
                self.days.select_row(self._rows[i])
                self._update_receipt()
                return True
        self._pending_date = iso
        return False


def build(store, actions, config):
    return SpendPage(store, actions, config)


def show_detail(widget, detail: str) -> None:
    """'spend:2026-11-16' selects that day's receipt."""
    if isinstance(widget, SpendPage) and detail:
        widget.select_date(detail)
