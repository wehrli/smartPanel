"""Overview + System pages (W2-overview-system): pure copy, then GTK pages.

The model tests need no display; the page tests build the real widgets against
demo scenarios (no window is presented) and are skipped without a display.
"""
from dataclasses import replace

import pytest

from smartpanel.demo import scenarios as sc
from smartpanel.state import Event, Store
from smartpanel.state.model import (IDLE, LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING,
                                    PHASE_JOURNALING, PHASE_LAUNCHING, WAITING, CpuState,
                                    DiskState, Forecast, GpuState, JournalProgress, Loading,
                                    MemoryState, NetState, ProjectSpend, Prompt, Session,
                                    SpendState, SwapState)
from smartpanel.ui.widgets import overview_system_model as m

NOW = sc.DEMO_NOW


# ───────────────────────── numbers ─────────────────────────

def test_money_and_unknowns():
    assert m.money(23.84) == "$23.84"
    assert m.money(None) == "—"
    assert m.money(2318.55) == "$2,318.55"
    assert m.pct(None) == "—" and m.pct(12.4) == "12%"


@pytest.mark.parametrize("bps,text", [
    (None, "—"), (0, "0 B/s"), (512, "512 B/s"), (180_000, "180 KB/s"),
    (2_400_000, "2.4 MB/s"), (3.2e9, "3.2 GB/s"), (-5, "0 B/s")])
def test_rate_units(bps, text):
    assert m.rate(bps) == text


@pytest.mark.parametrize("secs,text", [
    (None, ""), (10, "now"), (38 * 60, "38m"), (190 * 60, "3h10"), (62 * 60, "1h02")])
def test_fmt_age_matches_prototype(secs, text):
    assert m.fmt_age(secs) == text


def test_fmt_waiting():
    assert m.fmt_waiting(None, NOW) == ""
    assert m.fmt_waiting(NOW - 20, NOW) == "just now"
    assert m.fmt_waiting(NOW - 240, NOW) == "4m waiting"


# ───────────────────────── metric cards ─────────────────────────

def test_cpu_and_memory_cards():
    cpu = CpuState(percent=23, temp_c=61, cores=8)
    assert m.cpu_card(cpu) == m.Card("23%", "61 °C")
    assert m.cpu_card(cpu, detail=True).sub == "61 °C · 8 cores · 15 min history"
    mem = MemoryState(percent=68, used_gb=10.88, total_gb=16)
    assert m.memory_card(mem).sub == "10.9 of 16 GB"
    assert m.memory_card(mem, detail=True).sub == "10.9 of 16 GB used"
    assert m.memory_card(MemoryState()).value == "—"


def test_swap_card_levels_and_no_swap():
    swap = SwapState(percent=12, used_gb=0.96, total_gb=8, level=LEVEL_OK)
    assert m.swap_card(swap) == m.Card("12%", "1.0 of 8 GB", LEVEL_OK)
    assert m.swap_card(swap, detail=True).sub == \
        "1.0 of 8 GB · warns at 60%, critical at 85%"
    assert m.swap_thresholds(swap) == [(60.0, "warn"), (85.0, "crit")]
    crit = replace(swap, percent=88, used_gb=7.04, level=LEVEL_CRITICAL)
    assert m.swap_card(crit).level == LEVEL_CRITICAL
    none = SwapState()                         # no swap configured: not 0 %
    assert m.swap_card(none) == m.Card("—", "No swap configured", LEVEL_OK)
    detail = m.swap_card(none, detail=True)
    assert detail.na and "OOM killer, not a slowdown" in detail.sub
    assert m.swap_thresholds(none) == []


@pytest.mark.parametrize("pct,level", [(41, LEVEL_OK), (90, LEVEL_OK), (90.5, LEVEL_WARNING),
                                       (97, LEVEL_WARNING), (97.5, LEVEL_CRITICAL),
                                       (None, LEVEL_OK)])
def test_disk_level(pct, level):
    assert m.disk_level(pct) == level


def test_disk_net_gpu_cards():
    disk = m.disk_card(DiskState(mount="/", percent=41, used_gb=196, total_gb=476))
    assert disk == m.Card("41%", "196 of 476 GB on /", LEVEL_OK)
    assert m.disk_card(DiskState(mount="/data")).na
    net = m.net_card(NetState(down_bps=2.4e6, up_bps=1.8e5, iface="wlp2s0"))
    assert net == m.Card("↓ 2.4 MB/s", "↑ 180 KB/s · wlp2s0")
    assert m.net_card(NetState()).na
    gpu = m.gpu_card(GpuState())
    assert gpu.na and gpu.value == "—" and gpu.sub.startswith("No NVIDIA or AMD GPU found.")
    on = m.gpu_card(GpuState(available=True, percent=37, vram_used_gb=3.1,
                             vram_total_gb=8, temp_c=54))
    assert on == m.Card("37%", "VRAM 3.1 of 8 GB · 54 °C")


def test_today_card():
    spend = sc.build_state("busy", NOW).spend
    assert m.today_card(spend, False) == m.Card("$23.84", "41.2M tokens · $3.10/h")
    assert m.today_card(SpendState(), True) == m.Card("—", "No transcripts yet")
    assert m.today_card(SpendState(), False).value == "—"


def test_hourly_bars_stop_at_the_current_hour():
    spend = sc.build_state("busy", NOW).spend
    values, hi = m.hourly_bars(spend, NOW)
    assert len(values) == 15 and hi == 14          # 00..14 at 14:32
    assert m.hourly_bars(SpendState(hourly_today=[]), NOW) == ([], -1)


# ───────────────────────── budget ─────────────────────────

def _spend(spent, burn, budget, **f):
    return SpendState(cost_today=spent, burn_per_hour=burn,
                      forecast=Forecast(budget_usd=budget, **f))


def test_budget_copy():
    b = m.budget(_spend(23.84, 3.10, 40.0, pct=59.6, eta="19:45"))
    assert b == m.Budget("$23.84 / $40.00", 59.6, "At $3.10/h you reach it at 19:45", False)
    over = m.budget(_spend(46.5, 3.1, 40.0, pct=116.2, eta_reason="over", over_by_usd=6.5))
    assert over.over and over.sub == "Over budget by $6.50" and over.percent > 100
    assert m.budget(_spend(10, 3.1, 40.0, pct=25, eta_reason="tomorrow")).sub == \
        "At $3.10/h you reach it tomorrow"
    assert m.budget(_spend(10, 0.0, 40.0, pct=25, eta_reason="never")).sub == \
        "At $0.00/h you won’t reach it today"
    assert m.budget(_spend(10, 3.1, None)) is None           # no budget -> settings link


# ───────────────────────── projects ─────────────────────────

def test_project_colours_are_stable_and_from_the_palette():
    a = m.project_color("smartPanel")
    assert a == m.project_color("smartPanel") and a in m.PROJECT_PALETTE
    colours = {m.project_color(p) for p in sc.PROJECTS}
    assert len(colours) >= 4                       # spread, not all one colour


def test_project_colours_are_distinct_among_shown_rows():
    busy = sc.loaded_spend(NOW).projects_today
    bars = m.project_bars(busy)
    assert len(bars) >= 4
    assert len({b.color for b in bars}) == len(bars)
    # no near-duplicate oranges in the palette any more
    assert "#e66100" not in m.PROJECT_PALETTE
    # the resolver is independent of ranking order, so Spend can share it
    names = [b.name for b in bars]
    assert m.project_colors(names, m.ProjectColors()) == \
        m.project_colors(reversed(names), m.ProjectColors())
    assert len(set(m.project_colors(sc.PROJECTS, m.ProjectColors()).values())) == min(
        len(set(sc.PROJECTS)), len(m.PROJECT_PALETTE))


def test_project_colour_does_not_depend_on_the_other_projects_that_day():
    # SP-0807: smartPanel and tacomap share a CRC32 slot. A busy day used to
    # push smartPanel to the next colour; a quiet day without tacomap gave
    # it its own slot back, so the same project changed colour between days.
    assert m._slot("smartPanel") == m._slot("tacomap")
    reg = m.ProjectColors()
    busy = reg.colors(["tacomap", "ledgerly", "JobScout", "smartPanel"])
    quiet = reg.colors(["ledgerly", "smartPanel"])
    alone = reg.colors(["smartPanel"])
    assert busy["smartPanel"] == quiet["smartPanel"] == alone["smartPanel"]
    assert busy["tacomap"] != busy["smartPanel"]                  # still distinct
    # and the other way round: first seen alone, then with the colliding one
    reg2 = m.ProjectColors()
    first = reg2.colors(["smartPanel"])["smartPanel"]
    later = reg2.colors(["tacomap", "smartPanel"])
    assert later["smartPanel"] == first and later["tacomap"] != first


def test_project_colours_persist_across_restarts(tmp_path):
    path = tmp_path / "state" / "project-colors.json"
    a = m.ProjectColors(path).colors(["tacomap", "smartPanel"])
    b = m.ProjectColors(path).colors(["smartPanel"])              # a new process
    assert b["smartPanel"] == a["smartPanel"]
    path.write_text("not json")
    assert m.ProjectColors(path).colors(["x"])["x"] in m.PROJECT_PALETTE  # never raises
    ro = tmp_path / "ro"
    ro.write_text("")                                             # a file, not a dir
    assert m.ProjectColors(ro / "c.json").colors(["y"])["y"] in m.PROJECT_PALETTE


def test_project_colours_fall_back_when_the_palette_is_full():
    reg = m.ProjectColors()
    many = [f"p{i}" for i in range(len(m.PROJECT_PALETTE) + 4)]
    for name in many:                       # seen one per day: fills every slot
        reg.colors([name])
    shown = reg.colors(many[:3] + ["late"])
    assert len(set(shown.values())) == 4    # still distinct among the shown rows
    assert reg.colors(["late"])["late"] == shown["late"]


def test_project_bars_largest_first_with_others():
    projects = [ProjectSpend(f"p{i}", float(i)) for i in range(1, 10)]
    bars = m.project_bars(projects)
    assert [b.name for b in bars[:6]] == ["p9", "p8", "p7", "p6", "p5", "p4"]
    assert bars[-1].name == "3 others" and bars[-1].cost == 6.0
    assert bars[0].fraction == 1.0
    assert m.project_bars([ProjectSpend("x", 0.0)]) == []


# ───────────────────────── sessions ─────────────────────────

def _session(**kw):
    base = dict(name="claude-smartPanel", project="smartPanel", title="Fix swap",
                created=NOW - 62 * 60, activity=IDLE, cost_usd=2.9)
    base.update(kw)
    return Session(**base)


def test_session_row_sub_line():
    row = m.session_row(_session(attached=True), NOW)
    assert row.sub == "smartPanel · ● attached · $2.90"
    assert row.right == "1h02" and not row.busy
    assert m.session_row(_session(cost_usd=None), NOW).sub == "smartPanel"   # SP-1403


def test_session_row_journaling_and_starting():
    j = _session(phase=PHASE_JOURNALING, journal=JournalProgress(step=2))
    row = m.session_row(j, NOW, "~/dev/omdev/JOURNAL/2026-11.md")
    # the step goes in the wide sub-line; the right slot is a short counter
    assert row.sub == "Skill appends to JOURNAL/2026-11.md…"
    assert row.right == "⏳ 3/5"
    assert row.busy and row.journaling and not row.failed and "$" not in row.sub
    launch = m.session_row(_session(phase=PHASE_LAUNCHING), NOW)
    assert launch.right == "starting…" and launch.busy and launch.state == PHASE_LAUNCHING


def test_session_row_failed_journal_reads_as_failed():
    j = _session(phase=PHASE_JOURNALING, journal=JournalProgress(step=2, failed=True))
    row = m.session_row(j, NOW, "~/dev/omdev/JOURNAL/2026-11.md")
    assert row.failed and row.busy
    assert row.sub == "✕ Skill appends to JOURNAL/2026-11.md · failed, closing anyway"
    assert row.right == "✕ 3/5"
    running = m.session_row(_session(phase=PHASE_JOURNALING,
                                     journal=JournalProgress(step=2)), NOW)
    assert running.sub != row.sub and running.right != row.right


def test_needs_rows():
    p = Prompt("Do you want to make this edit to alerts.py?", ["Yes", "No"])
    items = [_session(activity=WAITING, prompt=p, waiting_since=NOW - 240),
             _session(name="b", activity=WAITING, title="", project="ledgerly"),
             _session(name="c")]
    rows = m.needs_rows(items, NOW)
    assert [n for n, _ in rows] == ["claude-smartPanel", "b"]
    assert rows[0][1].question == "“Do you want to make this edit to alerts.py?” · 4m waiting"
    assert rows[1][1] == m.NeedsRow("ledgerly", "“Waiting for input”")


# ───────────────────────── GTK pages ─────────────────────────

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, GLib, Gtk
    _DISPLAY = Gtk.init_check() if hasattr(Gtk, "init_check") else True
    _DISPLAY = bool(_DISPLAY) and Gdk.Display.get_default() is not None
except (ImportError, ValueError):  # pragma: no cover
    _DISPLAY = False

needs_display = pytest.mark.skipif(not _DISPLAY, reason="needs GTK 4 and a display")


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a))
        return rec


def _overview(scenario="busy"):
    Adw.init()
    from smartpanel.ui.pages import overview
    store, actions = Store(sc.build_state(scenario, NOW)), Recorder()
    page = overview.OverviewPage(store, actions, None, clock=lambda: NOW)
    return store, actions, page


def _system(scenario="busy"):
    Adw.init()
    from smartpanel.ui.pages import system
    store = Store(sc.build_state(scenario, NOW))
    return store, system.build(store, Recorder(), None)


@needs_display
def test_overview_busy_builds():
    store, actions, page = _overview("busy")
    assert page.today.value_text == "$23.84"
    assert page.cpu.value_text == "23%" and page.swap.sub_text == "1.0 of 8 GB"
    assert page.needs.get_visible() and page.needs_cap.get_text() == "NEEDS YOU · 1"
    assert page.sessions_stack.get_visible_child_name() == "list"
    assert len(page._rows) == 4
    assert page.right_stack.get_visible_child_name() == "normal"
    assert page.budget_value.get_text() == "$23.84 / $40.00"
    assert page.project_bars.names == ["smartPanel", "tacomap", "JobScout", "ledgerly"]
    assert not page.budget_link.get_visible()


@needs_display
def test_overview_updates_in_place():
    store, actions, page = _overview("busy")
    rows = dict(page._rows)
    cpu = page.cpu
    store.mutate("system", lambda s: setattr(s.cpu, "percent", 77.0))
    assert page.cpu is cpu and cpu.value_text == "77%"
    first = store.state.sessions.items[0].name
    store.mutate("sessions", lambda s: s.items.reverse())       # reorder, no rebuild
    assert page._rows == rows and page._order[first] == len(rows) - 1
    gone = store.state.sessions.items[0].name
    store.mutate("sessions", lambda s: s.items.pop(0))
    assert gone not in page._rows and len(page._rows) == 3


@needs_display
def test_overview_actions_go_through_actions():
    store, actions, page = _overview("busy")
    name, row = next(iter(page._rows.items()))
    row.title_button.emit("clicked")
    row.close_button.emit("clicked")
    row.retitle_button.emit("clicked")
    assert row.retitling
    settled = []
    row.retitle_done = lambda: settled.append(name)
    store.emit(Event("retitled", {"name": name}))
    assert settled == [name]
    next(iter(page._needs.values())).button.emit("clicked")
    page.budget_link.emit("clicked")
    assert ("focus_session", (name,)) in actions.calls
    assert ("request_close", (name,)) in actions.calls
    assert ("retitle_session", (name,)) in actions.calls
    assert ("open_dashboard", ("sessions:cockpit",)) in actions.calls      # SP-1209
    assert ("open_dashboard", ("settings",)) in actions.calls
    page.dispose_page()


@needs_display
def test_retitled_event_only_settles_its_own_row():
    store, actions, page = _overview("busy")
    (a, ra), (b, rb) = list(page._rows.items())[:2]
    ra.retitle_button.emit("clicked")
    rb.retitle_button.emit("clicked")
    settled = []
    ra.retitle_done = lambda: settled.append(a)
    rb.retitle_done = lambda: settled.append(b)
    store.emit(Event("toast", {"text": "Title re-read from tmux"}))
    assert settled == []                          # toast text is no longer matched
    store.emit(Event("retitled", {"name": b}))
    assert settled == [b]
    store.emit(Event("retitled", {"name": a}))
    assert settled == [b, a]
    page.dispose_page()


@needs_display
def test_overview_journaling_row_disables_controls():
    store, actions, page = _overview("busy")
    name = store.state.sessions.items[-1].name

    def journaling(s):
        for x in s.items:
            if x.name == name:
                x.phase, x.journal = PHASE_JOURNALING, JournalProgress(step=0)
    store.mutate("sessions", journaling)
    row = page._rows[name]
    assert not row.close_button.get_sensitive() and not row.retitle_button.get_sensitive()
    assert row.sub.get_text() == "Wait for the session to go idle…"
    assert row.sub.get_tooltip_text() == "Wait for the session to go idle…"
    assert row.right.get_text() == "⏳ 1/5"
    assert row.sub.has_css_class("os-jt") and not row.sub.has_css_class("os-jfail")

    def failed(s):
        for x in s.items:
            if x.name == name:
                x.journal = JournalProgress(step=2, failed=True)
    store.mutate("sessions", failed)
    assert row.sub.get_text().endswith("· failed, closing anyway")
    assert row.sub.has_css_class("os-jfail") and not row.sub.has_css_class("os-jt")
    assert row.right.get_text().startswith("✕")


@needs_display
@pytest.mark.parametrize("scenario,sessions,right", [
    ("empty", "empty", "normal"), ("notmux", "notmux", "normal"),
    ("first", "empty", "first"), ("cold", "list", "loading")])
def test_overview_states(scenario, sessions, right):
    store, actions, page = _overview(scenario)
    assert page.sessions_stack.get_visible_child_name() == sessions
    assert page.right_stack.get_visible_child_name() == right
    if scenario == "cold":
        assert page.today_slot.get_visible_child_name() == "skeleton"
        assert page.memory_slot.get_visible_child_name() == "skeleton"
        store.update("app", loading=None)
        store.replace("spend", sc.loaded_spend(NOW))
        assert page.today_slot.get_visible_child_name() == "card"
        assert page.right_stack.get_visible_child_name() == "normal"
    if scenario == "first":
        assert page.today.sub_text == "No transcripts yet"
    if scenario == "notmux":
        page.sessions_notmux.button.emit("clicked")
        assert ("open_dashboard", ("sessions",)) in actions.calls


@needs_display
def test_overview_no_budget_and_no_swap():
    store, actions, page = _overview("noswap")
    assert page.swap.value_text == "—" and page.swap.sub_text == "No swap configured"
    assert page.swap.level == LEVEL_OK
    store.mutate("spend", lambda s: setattr(s, "forecast", Forecast()))
    assert page.budget_link.get_visible() and not page.budget_track.get_visible()


@needs_display
def test_system_page_cards():
    store, page = _system("busy")
    assert page.cpu.sub_text == "61 °C · 8 cores · 15 min history"
    assert page.swap.chip.get_text() == "Fine"
    assert page.disk.sub_text == "196 of 476 GB on /" and not page.disk.chip.get_visible()
    assert page.net.value_text == "↓ 2.4 MB/s" and page.net.sub_text == "↑ 180 KB/s · wlp2s0"
    assert page.gpu.chip.get_text() == "No sensor" and page.gpu.has_css_class("sp-na")
    store.mutate("system", lambda s: setattr(s.disk, "percent", 98.0))
    assert page.disk_track.has_css_class("os-track-crit")
    assert page.disk.chip.get_visible() and page.disk.chip.get_text() == "Critical"


@needs_display
def test_system_page_swap_levels():
    store, page = _system("alert")
    assert page.swap.chip.get_text() == "Critical" and page.swap.has_css_class("sp-level-crit")
    store, page = _system("noswap")
    assert page.swap.chip.get_text() == "Not configured" and page.swap.value_text == "—"
    assert not page.swap.has_css_class("sp-level-crit")
