"""Palette registry, fuzzy matching and footer (SP-2201, SP-2202, SP-0111)."""
from smartpanel import commands as cmd
from smartpanel.demo import scenarios as sc
from smartpanel.state.model import (IDLE, PHASE_JOURNALING, PHASE_LAUNCHING, RUNNING,
                                    WAITING, Loading, PanelState, Session)

NOW = 1_763_380_000.0


class Rec:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, a))


def titles(state, config=None):
    return [c.title for c in cmd.build_commands(state, config)]


# ───────────────────────── registry ─────────────────────────

def test_busy_order_waiting_first_then_focus_journal_then_global():
    state = sc.build_state("busy", NOW)
    cmds = cmd.build_commands(state, {})
    ts = [c.title for c in cmds]
    waiting = [s for s in state.sessions.items if s.activity == WAITING]
    assert waiting, "busy has a waiting session"
    assert ts[0] == f"Answer · {waiting[0].display_title}"
    first_focus = next(i for i, t in enumerate(ts) if t.startswith("Focus · "))
    first_close = next(i for i, t in enumerate(ts) if t.startswith("Journal & close · "))
    new = ts.index("New session…")
    assert all(t.startswith("Answer · ") for t in ts[:first_focus])
    assert first_focus < first_close < new
    assert ts[new + 1] == "Close all sessions…"
    pages = [t for t in ts if t.startswith("Go to ")]
    assert pages == ["Go to Overview", "Go to Sessions", "Go to Spend", "Go to System",
                     "Go to Journal", "Go to Settings"]
    assert ts[-3:] == ["Show desktop HUD", "Start rotating the top bar",
                       f"Open {cmd.log_display_name(state.journal.monthly_log_path)}"]
    assert ts[-1].startswith("Open JOURNAL/")
    ids = [c.id for c in cmds]
    assert len(ids) == len(set(ids))


def test_empty_has_no_session_commands_and_no_close_all():
    ts = titles(sc.build_state("empty", NOW))
    assert not [t for t in ts if " · " in t]
    assert "Close all sessions…" not in ts
    assert "New session…" in ts


def test_notmux_still_offers_new_session_and_pages():
    state = sc.build_state("notmux", NOW)
    ts = titles(state)
    assert "New session…" in ts and "Go to Sessions" in ts
    assert not [t for t in ts if t.startswith(("Focus", "Answer", "Journal &"))]


def _state_with(*sessions):
    st = PanelState()
    st.sessions.items = list(sessions)
    return st


def test_starting_and_journaling_sessions_get_no_actions():
    st = _state_with(Session("claude-a", project="a", title="Alpha", activity=RUNNING,
                             phase=PHASE_LAUNCHING),
                     Session("claude-b", project="b", title="Beta", activity=WAITING,
                             phase=PHASE_JOURNALING),
                     Session("claude-c", project="c", activity=IDLE))
    ts = titles(st)
    assert not [t for t in ts if "Alpha" in t or "Beta" in t]
    assert "Focus · c" in ts and "Journal & close · c" in ts
    assert "Close all sessions…" in ts


def test_runs_go_through_actions():
    st = _state_with(Session("claude-x", project="x", title="Ex", activity=WAITING))
    by_id = {c.id: c for c in cmd.build_commands(st)}
    rec = Rec()
    by_id["answer:claude-x"].run(rec)
    by_id["focus:claude-x"].run(rec)
    by_id["close:claude-x"].run(rec)
    by_id["new-session"].run(rec)
    by_id["close-all"].run(rec)
    by_id["page:spend"].run(rec)
    by_id["hud"].run(rec)
    by_id["ticker"].run(rec)
    assert rec.calls == [("open_dashboard", ("sessions",)), ("focus_session", ("claude-x",)),
                         ("request_close", ("claude-x",)), ("request_new_session", ()),
                         ("request_close_all", ()), ("open_dashboard", ("spend",)),
                         ("set_hud", (True,)), ("set_setting", ("bar.ticker", True))]
    assert by_id["focus:claude-x"].glyph == "❓"
    assert by_id["close:claude-x"].subtitle == "x"


def test_toggles_follow_config_then_store():
    st = PanelState()
    st.settings.values = {"hud.enabled": True, "bar.ticker": True}
    ts = titles(st)
    assert "Hide desktop HUD" in ts and "Stop rotating the top bar" in ts
    ts = titles(st, {"hud.enabled": False, "bar.ticker": False})
    assert "Show desktop HUD" in ts and "Start rotating the top bar" in ts


def test_open_log_uses_open_path_when_available():
    st = PanelState()
    st.journal.monthly_log_path = "/nonexistent/JOURNAL/2026-11.md"
    c = [c for c in cmd.build_commands(st) if c.id == "journal-log"][0]
    assert c.title == "Open JOURNAL/2026-11.md"
    rec = Rec()
    c.run(rec)
    assert rec.calls == [("open_path", ("/nonexistent/JOURNAL/2026-11.md",))]

    class NoOpen:
        def __init__(self):
            self.calls = []

        def open_dashboard(self, page):
            self.calls.append(page)

    a = NoOpen()
    c.run(a)                         # file doesn't exist -> the Journal page
    assert a.calls == ["journal"]


def test_no_log_command_when_path_unknown():
    assert not [t for t in titles(PanelState()) if t.startswith("Open ")]


# ───────────────────────── matching ─────────────────────────

def test_jour_finds_journal_and_close():
    st = sc.build_state("busy", NOW)
    rows = cmd.filter_commands(cmd.build_commands(st), "jour")
    assert rows
    assert rows[0][0].title.startswith("Journal & close · ")
    assert rows[0][1] == [(0, 4)]


def test_fsah_finds_fix_swap_alert_hysteresis():
    st = sc.build_state("busy", NOW)
    rows = cmd.filter_commands(cmd.build_commands(st), "fsah", limit=None)
    found = [c.title for c, _r in rows]
    assert any(t.endswith("Fix swap alert hysteresis") for t in found)
    score, ranges = cmd.match("fsah", "Fix swap alert hysteresis")
    assert score == cmd.SCORE_SCATTERED
    title = "Fix swap alert hysteresis"
    assert [title[a:b] for a, b in ranges] == ["F", "s", "a", "h"]


def test_match_basics():
    assert cmd.match("", "Anything") == (0, [])
    assert cmd.match("  ", "Anything") == (0, [])
    assert cmd.match("SYS", "Go to System") == (cmd.SCORE_SUBSTRING, [(6, 9)])
    assert cmd.match("xyz", "Go to System") is None
    assert cmd.match("gts", "Go to System")[0] == cmd.SCORE_SCATTERED
    # out of order is not a match
    assert cmd.match("ba", "ab") is None
    # adjacent picked letters merge into one range
    assert cmd.match("gto", "Go to")[1] == [(0, 1), (3, 5)]


def test_substring_ranks_above_scattered_then_registry_order():
    cmds = [cmd.Command("a", "Show desktop HUD"), cmd.Command("b", "Go to Sessions"),
            cmd.Command("c", "Go to System")]
    rows = cmd.filter_commands(cmds, "sys")
    assert [c.id for c, _ in rows] == ["c"]
    rows = cmd.filter_commands(cmds, "s")
    assert [c.id for c, _ in rows] == ["a", "b", "c"]          # all substrings: registry order
    rows = cmd.filter_commands(cmds, "gtse")
    assert [c.id for c, _ in rows] == ["b", "c"]


def test_filter_limits_to_nine():
    cmds = [cmd.Command(str(i), f"Go {i}") for i in range(20)]
    assert len(cmd.filter_commands(cmds, "")) == 9
    assert len(cmd.filter_commands(cmds, "", limit=None)) == 20


# ───────────────────────── footer ─────────────────────────

def test_status_line_busy():
    st = sc.build_state("busy", NOW)
    line = cmd.status_line(st)
    assert line.startswith("Swap ")
    assert line.endswith(" · ↑↓ move · ⏎ run")
    n = len(st.sessions.items)
    assert f"{n} sessions" in line
    assert " waiting" in line
    assert f"${st.spend.cost_today:,.2f} today" in line


def test_status_line_edges():
    st = PanelState()
    assert cmd.status_parts(st) == ["Swap —", "0 sessions", "— today"]
    st.app.loading = Loading(3, 10)
    assert cmd.status_parts(st)[2] == "reading transcripts…"
    st.app.first_run = True
    assert cmd.status_parts(st)[2] == "no spend yet"
    st.system.swap.percent = 12.4
    st.spend.cost_today = 23.84
    st.app.first_run = False
    st.sessions.items = [Session("claude-a", activity=IDLE)]
    assert cmd.status_parts(st) == ["Swap 12%", "1 session", "$23.84 today"]
    st.sessions.tmux_ok = False
    assert cmd.status_parts(st)[1] == "tmux not found"


def test_status_line_matches_prototype_example():
    st = PanelState()
    st.system.swap.percent = 12
    st.spend.cost_today = 23.84
    st.sessions.items = [Session(f"claude-{i}", activity=a)
                         for i, a in enumerate((WAITING, RUNNING, IDLE, IDLE))]
    assert cmd.status_line(st) == ("Swap 12% · 4 sessions · 1 waiting · $23.84 today"
                                   " · ↑↓ move · ⏎ run")
