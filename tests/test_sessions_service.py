from __future__ import annotations

from pathlib import Path

from smartpanel.config import Config
from smartpanel.services import sessions as svc
from smartpanel.services.sessions import SessionsService, compute_costs
from smartpanel.state import Store
from smartpanel.state.model import (
    IDLE, PHASE_JOURNALING, PHASE_LAUNCHING, RUNNING, WAITING)
from tests.fakes import Clock, FakeLoop, FakeSession, FakeSpend, FakeTmux
from tests.panes import pane


def make(sessions=None, *, spend=None, which=lambda n: f"/usr/bin/{n}", clock=None,
         find_transcript=None, available=True):
    store = Store()
    tm = FakeTmux(sessions, available=available)
    clock = clock or Clock()
    s = SessionsService(store, Config(), spend, run=tm, loop=FakeLoop(), clock=clock,
                        which=which, find_transcript=find_transcript or (lambda c: None),
                        hostname="box")
    return s, store, tm, clock


def names(store):
    return [x.name for x in store.state.sessions.items]


def test_refresh_builds_records_in_display_order():
    s, store, tm, _ = make({
        "claude-idle": FakeSession(created=1, title="✳ Tidy docs", cwd="/home/u/dev/docs"),
        "claude-run": FakeSession(created=2, pane=pane("running_bash")),
        "claude-wait": FakeSession(created=3, pane=pane("edit_permission"), attached=True),
    })
    s.refresh()
    assert names(store) == ["claude-wait", "claude-run", "claude-idle"]
    wait, run, idle = store.state.sessions.items
    assert wait.activity == WAITING and wait.prompt.options[0] == "Yes"
    assert wait.attached and wait.waiting_since == 10_000.0
    assert run.activity == RUNNING and run.doing == "Bash · npm test -- filters"
    assert run.prompt is None and run.waiting_since is None
    assert idle.activity == IDLE and idle.title == "Tidy docs"
    assert idle.project == "docs" and idle.cwd == "/home/u/dev/docs"
    assert idle.created == 1.0


def test_one_capture_per_session_per_refresh_and_cwd_once():
    s, store, tm, _ = make({"claude-a": FakeSession(), "claude-b": FakeSession()})
    s.refresh()
    s.refresh()
    caps = [c for c in tm.calls if c[0] == "capture-pane"]
    cwds = [c for c in tm.calls if c[0] == "display-message"]
    assert len(caps) == 4 and len(cwds) == 2


def test_unchanged_list_does_not_notify():
    s, store, tm, _ = make({"claude-a": FakeSession()})
    seen = []
    store.subscribe(seen.append)
    assert s.refresh() is True
    assert s.refresh() is False
    assert seen == [{"sessions"}]


def test_waiting_since_is_kept_then_cleared():
    clock = Clock()
    s, store, tm, _ = make({"claude-a": FakeSession(pane=pane("question_3"))}, clock=clock)
    s.refresh()
    clock.now += 240
    s.refresh()
    assert store.state.sessions.items[0].waiting_since == 10_000.0
    tm.sessions["claude-a"].pane = pane("running_bash")
    s.refresh()
    assert store.state.sessions.items[0].waiting_since is None


def test_terminal_titles_pushed_once_and_again_after_retitle():
    s, store, tm, _ = make({"claude-webapp": FakeSession(title="✳ Fix login")})
    s.refresh()
    s.refresh()
    sets = [c for c in tm.calls if c[0] == "set-option" and "set-titles-string" in c]
    assert len(sets) == 1
    assert tm.titles["claude-webapp"] == "Fix login — webapp"
    assert s.heading("claude-webapp") == "Fix login — webapp"
    s.retitle("claude-webapp")
    sets = [c for c in tm.calls if c[0] == "set-option" and "set-titles-string" in c]
    assert len(sets) == 2


def test_untitled_session_title_falls_back_to_the_project_heading():
    s, store, tm, _ = make({"claude-webapp": FakeSession(title="✳ Claude Code")})
    s.refresh()
    assert store.state.sessions.items[0].title == ""
    assert store.state.sessions.items[0].display_title == "webapp"
    assert tm.titles["claude-webapp"] == "webapp"


def test_environment_check():
    s, store, tm, _ = make(which=lambda n: None)
    assert s.check_environment() == (True, False)
    assert store.state.sessions.terminal_ok is False
    s2, store2, _, _ = make(available=False)
    s2.check_environment()
    assert store2.state.sessions.tmux_ok is False


def test_start_schedules_refresh_env_and_cost_timers_and_stop_cancels():
    s, store, tm, _ = make({"claude-a": FakeSession()}, spend=FakeSpend())
    s.start()
    assert sorted(t for t, _ in s.loop.timers) == [svc.REFRESH_S, svc.COST_MIN_INTERVAL_S,
                                                   svc.ENV_CHECK_S]
    assert names(store) == ["claude-a"]
    s.stop()
    assert len(s.loop.cancelled) == 3


# ── launching (SP-1203) ──

def test_launch_placeholder_until_tmux_lists_it():
    clock = Clock()
    s, store, tm, _ = make({"claude-webapp": FakeSession()}, clock=clock)
    toasts = []
    store.on_event(lambda e: toasts.append(e.data["text"]))
    s.refresh()
    s.add_launch("/home/u/dev/webapp")
    rows = store.state.sessions.items
    assert [r.phase for r in rows] == [PHASE_LAUNCHING, "none"]
    assert rows[0].project == "webapp"
    # The already-running claude-webapp is not the new one; claude-webapp-2 is.
    clock.now += 3
    s.refresh()
    assert any(r.phase == PHASE_LAUNCHING for r in store.state.sessions.items)
    tm.sessions["claude-webapp-2"] = FakeSession(created=10_003, cwd="/home/u/dev/webapp")
    s.refresh()
    assert names(store) == ["claude-webapp", "claude-webapp-2"]
    assert toasts == ["Session started in /home/u/dev/webapp"]


def test_launch_times_out_after_ten_seconds():
    clock = Clock()
    s, store, tm, _ = make({}, clock=clock)
    errors = []
    store.on_event(lambda e: errors.append(e.data))
    s.add_launch("/home/u/dev/api")
    clock.now += 11
    s.refresh()
    assert store.state.sessions.items == []
    assert errors[0]["error"] and "didn’t start within 10 s" in errors[0]["text"]


# ── journaling (SP-1601) ──

def test_journaling_row_survives_the_kill_until_the_outcome():
    s, store, tm, _ = make({"claude-a": FakeSession(title="✳ Fix"),
                            "claude-b": FakeSession(created=5)})
    s.refresh()
    s.begin_journal("claude-a")
    a = next(x for x in store.state.sessions.items if x.name == "claude-a")
    assert a.phase == PHASE_JOURNALING and a.journal.step == 0
    s.journal_progress("claude-a", 2, headless=True)
    del tm.sessions["claude-a"]           # the run killed it
    s.refresh()
    a = next(x for x in store.state.sessions.items if x.name == "claude-a")
    assert a.phase == PHASE_JOURNALING and a.journal.step == 2 and a.journal.headless
    assert a.title == "Fix"
    s.journal_progress("claude-a", 1)     # never goes backwards
    s.journal_progress("claude-a", 2, failed=True)
    a = next(x for x in store.state.sessions.items if x.name == "claude-a")
    assert a.journal.step == 2 and a.journal.failed
    # Journaling sorts above idle.
    assert names(store) == ["claude-a", "claude-b"]
    s.end_journal("claude-a")
    assert names(store) == ["claude-b"]


# ── per-session cost (SP-1401) ──

def test_compute_costs_gives_an_ambiguous_cwd_to_the_newest_only():
    paths = {"/p/a": Path("/t/a.jsonl"), "/p/b": Path("/t/b.jsonl")}
    spend = FakeSpend({"/t/a.jsonl": 2.9, "/t/b.jsonl": 1.0})
    out = compute_costs([("old", "/p/a", 1), ("new", "/p/a", 5), ("b", "/p/b", 2),
                         ("none", None, 3), ("x", "/p/x", 4)],
                        paths.get, spend.session_cost)
    assert out == {"old": None, "new": 2.9, "b": 1.0, "none": None, "x": None}


def test_cost_is_throttled_and_lands_in_the_records():
    spend = FakeSpend({"/t/a.jsonl": 2.9})
    s, store, tm, _ = make({"claude-a": FakeSession(cwd="/p/a")}, spend=spend,
                           find_transcript={"/p/a": Path("/t/a.jsonl")}.get)
    s.refresh()
    s.request_costs()
    assert store.state.sessions.items[0].cost_usd == 2.9
    s.request_costs()                      # within 15 s: skipped
    assert len(spend.asked) == 1
    s.request_costs(force=True)
    assert len(spend.asked) == 2


def test_cost_unknown_when_spend_returns_none():
    spend = FakeSpend({})
    s, store, tm, _ = make({"claude-a": FakeSession(cwd="/p/a")}, spend=spend,
                           find_transcript={"/p/a": Path("/t/a.jsonl")}.get)
    s.refresh()
    s.request_costs()
    assert store.state.sessions.items[0].cost_usd is None


def test_spend_section_change_triggers_cost_recompute():
    spend = FakeSpend({"/t/a.jsonl": 1.5})
    s, store, tm, _ = make({"claude-a": FakeSession(cwd="/p/a")}, spend=spend,
                           find_transcript={"/p/a": Path("/t/a.jsonl")}.get)
    s.start()
    asked = len(spend.asked)
    s._cost_last = float("-inf")
    store.update("spend", cost_today=3.0)
    assert len(spend.asked) == asked + 1


def test_no_tmux_server_is_an_empty_list():
    s, store, tm, _ = make({})
    s.refresh()
    assert store.state.sessions.items == []
