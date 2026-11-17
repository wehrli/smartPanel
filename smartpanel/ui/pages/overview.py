"""Overview page (prototype `overviewHTML` + `metricsHTML`).

    ┌ Today ─┐┌ CPU ─┐┌ Memory ┐┌ Swap ─┐      4 metric cards (FlowBox, wraps when narrow)
    ┌ Needs you · N ───────────────────────┐   only while a session waits on you
    ┌ Sessions (boxed list) ┐ ┌ Daily budget ┐
    │ ❓ title · sub   age ⟳ ✕│ ├ Today by project ┤
                                              two columns; stacked below ~620 px

Built once, then updated in place from store notifications (SP-2108): every
widget keeps its identity, the session list reorders through a sort function
instead of being rebuilt, so scroll position, focus and hover survive each tick.
All copy comes from `ui/widgets/overview_system_model.py`.
"""
from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from smartpanel.state.model import PanelState  # noqa: E402
from smartpanel.ui.widgets import EmptyState, HourBars, MetricCard, Track  # noqa: E402
from smartpanel.ui.widgets import overview_system_model as m  # noqa: E402
from smartpanel.ui.widgets.overview_system_widgets import (  # noqa: E402
    NeedsRow, ProjectBars, SessionRow, SkeletonCard, a11y_label, card_box, cap,
    install_css, set_text, toggle_class)

SCROLLS = True                 # the page owns its ScrolledWindow (inside the breakpoint bin)
CHART_W, CHART_H = 120, 30     # min chart size; charts expand with their card
AGE_TICK_S = 20                # session ages / waiting times, only while mapped
NARROW = "max-width: 620sp"
RETITLE_TOAST = "Title re-read from tmux"


def build(store, actions, config=None):
    return OverviewPage(store, actions, config)


class OverviewPage(Adw.BreakpointBin):
    def __init__(self, store, actions, config=None, *, clock=time.time):
        super().__init__()
        install_css()
        self.store, self.actions, self.config, self.clock = store, actions, config, clock
        self.set_size_request(320, 240)
        self._rows: dict[str, SessionRow] = {}
        self._order: dict[str, int] = {}
        self._needs: dict[str, NeedsRow] = {}
        self._tick = 0

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("os-page")
        content.append(self._build_metrics())
        content.append(self._build_needs())
        self.cols = Gtk.Box(spacing=12)
        self.cols.append(self._build_sessions())
        self.cols.append(self._build_right())
        content.append(self.cols)

        clamp = Adw.Clamp(maximum_size=1200, tightening_threshold=1000, child=content)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True, hexpand=True, child=clamp)
        self.set_child(scroller)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(NARROW))
        bp.add_setter(self.cols, "orientation", Gtk.Orientation.VERTICAL)
        self.add_breakpoint(bp)

        self._unsub = store.subscribe(self._on_changed)
        self._unsub_events = store.on_event(self._on_event)
        self.connect("map", lambda *_: self._start_tick())
        self.connect("unmap", lambda *_: self._stop_tick())
        self.connect("destroy", lambda *_: self.dispose_page())
        self.refresh()

    # ───────────────────────── construction ─────────────────────────

    def _build_metrics(self) -> Gtk.Widget:
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           min_children_per_line=2, max_children_per_line=4,
                           column_spacing=10, row_spacing=10)
        grid.add_css_class("os-grid")

        self.today = MetricCard("Today", chart=False, chart_width=CHART_W)
        self.today_bars = HourBars(CHART_W, CHART_H)
        self.today_bars.set_margin_top(2)
        self.today_bars.set_margin_bottom(2)
        value = self.today.get_first_child().get_next_sibling()
        self.today.insert_child_after(self.today_bars, value)
        self.cpu = MetricCard("CPU", vmax=100, chart_width=CHART_W, chart_height=CHART_H)
        self.memory = MetricCard("Memory", vmax=100, chart_width=CHART_W, chart_height=CHART_H)
        self.swap = MetricCard("Swap", vmax=100, chart_width=CHART_W, chart_height=CHART_H)

        self.today_slot = self._slot(self.today, "Today")
        self.memory_slot = self._slot(self.memory, "Memory")
        for card in (self.today, self.cpu, self.memory, self.swap):
            card.get_last_child().set_max_width_chars(16)   # see system.py
        for w in (self.today_slot, self.cpu, self.memory_slot, self.swap):
            grid.append(w)
        child = grid.get_first_child()
        while child is not None:
            child.set_focusable(False)
            child = child.get_next_sibling()
        self.metrics = grid
        return grid

    @staticmethod
    def _slot(card: Gtk.Widget, label: str) -> Gtk.Stack:
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                          transition_duration=150, hhomogeneous=True, vhomogeneous=False)
        stack.add_named(card, "card")
        stack.add_named(SkeletonCard(label), "skeleton")
        stack.set_visible_child_name("card")
        return stack

    def _build_needs(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("os-needs")
        self.needs_cap = cap("Needs you")
        self.needs_cap.add_css_class("os-cap-wait")
        box.append(self.needs_cap)
        self.needs_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(self.needs_list)
        box.set_visible(False)
        box.set_accessible_role(Gtk.AccessibleRole.GROUP)
        self.needs = box
        return box

    def _build_sessions(self) -> Gtk.Widget:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True,
                      valign=Gtk.Align.START)
        col.append(cap("Sessions"))
        self.sessions_stack = Gtk.Stack(vhomogeneous=False, hhomogeneous=False,
                                        transition_type=Gtk.StackTransitionType.NONE)
        self.session_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.session_list.add_css_class("boxed-list")
        self.session_list.add_css_class("os-sessions")
        self.session_list.set_sort_func(self._sort_rows)
        a11y_label(self.session_list, "Sessions")
        self.sessions_stack.add_named(self.session_list, "list")

        empty = card_box()
        self.sessions_empty = EmptyState(
            "No sessions running", glyph="⌁", action_label="New session",
            on_action=lambda: self.actions.request_new_session())
        self.sessions_empty.add_css_class("os-compact")
        empty.append(self.sessions_empty)
        self.sessions_stack.add_named(empty, "empty")

        notmux = card_box()
        self.sessions_notmux = EmptyState(
            "tmux isn’t installed", "Sessions can’t be listed.",
            action_label="How to fix", suggested=False,
            on_action=lambda: self.actions.open_dashboard("sessions"))
        self.sessions_notmux.add_css_class("os-compact")
        notmux.append(self.sessions_notmux)
        self.sessions_stack.add_named(notmux, "notmux")
        col.append(self.sessions_stack)
        return col

    def _build_right(self) -> Gtk.Widget:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.START)
        col.set_size_request(250, -1)
        self.right_stack = Gtk.Stack(vhomogeneous=False, hhomogeneous=False,
                                     transition_type=Gtk.StackTransitionType.NONE)

        normal = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        # budget card (SP-0903)
        self.budget_card = card_box("os-box", spacing=6)
        head = Gtk.Box(spacing=8)
        head.append(Gtk.Label(label="Daily budget", xalign=0, hexpand=True))
        self.budget_value = Gtk.Label(xalign=1)
        self.budget_value.add_css_class("os-num")
        self.budget_value.add_css_class("os-title")
        head.append(self.budget_value)
        self.budget_card.append(head)
        self.budget_track = Track()
        self.budget_track.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        self.budget_card.append(self.budget_track)
        self.budget_sub = Gtk.Label(xalign=0, wrap=True, max_width_chars=30)
        self.budget_sub.add_css_class("os-sub")
        self.budget_card.append(self.budget_sub)
        self.budget_link = Gtk.Button(label="Set a daily budget", halign=Gtk.Align.START)
        self.budget_link.add_css_class("flat")
        self.budget_link.add_css_class("os-link")
        self.budget_link.set_tooltip_text("Open Settings")
        self.budget_link.connect("clicked", lambda *_: self.actions.open_dashboard("settings"))
        self.budget_card.append(self.budget_link)
        normal.append(self.budget_card)

        # today by project (SP-0807)
        proj = card_box("os-box", spacing=8)
        proj.append(cap("Today by project"))
        self.project_bars = ProjectBars()
        proj.append(self.project_bars)
        self.projects_none = Gtk.Label(label="No spend yet today", xalign=0)
        self.projects_none.add_css_class("os-sub")
        proj.append(self.projects_none)
        receipts = Gtk.Button(label="Open receipts", halign=Gtk.Align.START)
        receipts.connect("clicked", lambda *_: self.actions.open_dashboard("spend"))
        proj.append(receipts)
        self.projects_card = proj
        normal.append(proj)
        self.right_stack.add_named(normal, "normal")

        first = card_box("os-box")
        first_text = Gtk.Label(
            label="Spend appears here once Claude Code writes its first transcript.",
            wrap=True, max_width_chars=30, justify=Gtk.Justification.CENTER,
            margin_top=12, margin_bottom=12)
        first_text.add_css_class("sp-empty-text")
        first.append(first_text)
        self.right_stack.add_named(first, "first")

        loading = card_box("os-box", spacing=8)
        for frac, h in ((0.45, 12), (1.0, 7), (0.8, 10), (0.6, 10), (0.9, 10)):
            bar = Gtk.Box(halign=Gtk.Align.FILL if frac == 1.0 else Gtk.Align.START)
            bar.add_css_class("os-skel")
            bar.set_size_request(int(220 * frac), h)
            loading.append(bar)
        loading.set_accessible_role(Gtk.AccessibleRole.GROUP)
        a11y_label(loading, "Spend, loading")
        self.right_stack.add_named(loading, "loading")

        col.append(self.right_stack)
        return col

    # ───────────────────────── live updates ─────────────────────────

    def refresh(self) -> None:
        self._update_system()
        self._update_spend()
        self._update_sessions()

    def _on_changed(self, sections: set[str]) -> None:
        if "system" in sections or "app" in sections:
            self._update_system()
        if sections & {"spend", "app"}:
            self._update_spend()
        if sections & {"sessions", "journal"}:
            self._update_sessions()

    def _on_event(self, event) -> None:
        if event.kind == "toast" and (event.data or {}).get("text") == RETITLE_TOAST:
            for row in self._rows.values():
                row.retitle_done()

    @property
    def state(self) -> PanelState:
        return self.store.state

    def _update_system(self) -> None:
        st = self.state
        sysst, loading = st.system, st.app.loading is not None
        c = m.cpu_card(sysst.cpu)
        self.cpu.set_value(c.value)
        self.cpu.set_sub(c.sub)
        self.cpu.set_history(sysst.cpu.history)

        c = m.memory_card(sysst.memory)
        self.memory.set_value(c.value)
        self.memory.set_sub(c.sub)
        self.memory.set_history(sysst.memory.history)
        self._show(self.memory_slot, "skeleton" if loading else "card")

        swap = sysst.swap
        c = m.swap_card(swap)
        self.swap.set_value(c.value)
        self.swap.set_sub(c.sub)
        self.swap.set_level(c.level)
        self.swap.set_history(swap.history if m.swap_configured(swap) else [],
                              thresholds=m.swap_thresholds(swap))

    def _update_spend(self) -> None:
        st = self.state
        spend, app = st.spend, st.app
        loading = app.loading is not None
        c = m.today_card(spend, app.first_run)
        self.today.set_value(c.value)
        self.today.set_sub(c.sub)
        values, hi = m.hourly_bars(spend, self.clock())
        self.today_bars.set_values([] if app.first_run else values, highlight=hi)
        self._show(self.today_slot, "skeleton" if loading else "card")

        if app.first_run:
            self._show(self.right_stack, "first")
            return
        if loading and spend.cost_today is None:
            self._show(self.right_stack, "loading")
            return
        self._show(self.right_stack, "normal")
        b = m.budget(spend)
        has = b is not None
        self.budget_track.set_visible(has)
        self.budget_sub.set_visible(has and bool(b.sub))
        self.budget_value.set_visible(has)
        self.budget_link.set_visible(not has)
        if has:
            set_text(self.budget_value, b.headline)
            self.budget_track.set_percent(b.percent)
            set_text(self.budget_sub, b.sub)
            toggle_class(self.budget_sub, "os-over", b.over)
            toggle_class(self.budget_sub, "os-sub", not b.over)
            a11y_label(self.budget_card, f"Daily budget {b.headline}. {b.sub}")
        else:
            a11y_label(self.budget_card, "No daily budget set")
        bars = m.project_bars(spend.projects_today)
        self.project_bars.update(bars)
        self.project_bars.set_visible(bool(bars))
        self.projects_none.set_visible(not bars)

    def _update_sessions(self) -> None:
        st = self.state
        sess = st.sessions
        now = self.clock()
        items = sess.items if sess.tmux_ok else []

        # Needs you (SP-1209, SP-1307)
        needs = m.needs_rows(items, now)
        set_text(self.needs_cap, f"NEEDS YOU · {len(needs)}")
        a11y_label(self.needs, f"{len(needs)} session{'s' if len(needs) != 1 else ''} need you")
        names = [n for n, _ in needs]
        if list(self._needs) != names:
            for row in self._needs.values():
                self.needs_list.remove(row)
            kept = self._needs
            self._needs = {}
            for n in names:
                row = kept.get(n) or NeedsRow(n, self._answer)
                self.needs_list.append(row)
                self._needs[n] = row
        for n, model in needs:
            self._needs[n].update(model)
        self.needs.set_visible(bool(needs))

        # Session list (SP-1208)
        if not sess.tmux_ok:
            self._show(self.sessions_stack, "notmux")
        elif not items:
            self._show(self.sessions_stack, "empty")
        else:
            self._show(self.sessions_stack, "list")
        log = st.journal.monthly_log_path
        order = {s.name: i for i, s in enumerate(items)}
        for name in [n for n in self._rows if n not in order]:
            row = self._rows.pop(name)
            row.dispose_timers()
            self.session_list.remove(row)
        for s in items:
            row = self._rows.get(s.name)
            if row is None:
                row = SessionRow(s.name, on_focus=self._focus, on_retitle=self._retitle,
                                 on_close=self._close)
                self._rows[s.name] = row
                self.session_list.append(row)
            row.update(m.session_row(s, now, log))
        if order != self._order:
            self._order = order
            self.session_list.invalidate_sort()

    def _sort_rows(self, a, b) -> int:
        return self._order.get(a.name, 0) - self._order.get(b.name, 0)

    @staticmethod
    def _show(stack: Gtk.Stack, name: str) -> None:
        if stack.get_visible_child_name() != name:
            stack.set_visible_child_name(name)

    # ───────────────────────── actions ─────────────────────────

    def _focus(self, name: str) -> None:
        self.actions.focus_session(name)

    def _retitle(self, name: str) -> None:
        self.actions.retitle_session(name)

    def _close(self, name: str) -> None:
        self.actions.request_close(name)

    def _answer(self, _name: str) -> None:
        self.actions.open_dashboard("sessions")

    # ───────────────────────── age ticker ─────────────────────────

    def _start_tick(self) -> None:
        if not self._tick:
            self._update_sessions()
            self._tick = GLib.timeout_add_seconds(AGE_TICK_S, self._on_tick)

    def _on_tick(self) -> bool:
        self._update_sessions()
        return GLib.SOURCE_CONTINUE

    def _stop_tick(self) -> None:
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0

    def dispose_page(self) -> None:
        self._stop_tick()
        for row in self._rows.values():
            row.dispose_timers()
        unsubs = (self._unsub, self._unsub_events)
        self._unsub = self._unsub_events = None
        for unsub in unsubs:
            if unsub:
                try:
                    unsub()
                except Exception:  # noqa: BLE001 — teardown at exit; the store may be gone
                    pass
