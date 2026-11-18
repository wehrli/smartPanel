from __future__ import annotations


import pytest

from smartpanel.config import Config
from smartpanel.services.controller import Controller
from smartpanel.sessions import focus as focus_mod
from smartpanel.sessions import journal, launch as launch_mod
from smartpanel.sessions.prompt import AnswerResult
from smartpanel.state import Store
from smartpanel.state.actions import Actions
from smartpanel.state.model import PHASE_JOURNALING, PHASE_LAUNCHING
from tests.fakes import Clock, FakeLoop, FakeSession, FakeTmux
from tests.panes import pane

OLD, YOUNG = 1000, 9_990          # created; the clock reads 10_000


class Harness:
    def __init__(self, tmp_path, sessions=None, config=None, **kw):
        self.store = Store()
        self.tmux = FakeTmux(sessions or {})
        self.config = Config(config or {})
        self.events = []
        self.notes = []
        self.focus_calls = []
        self.launches = []
        self.journal_calls = []
        self.ambiguous = {}
        self.store.on_event(self.events.append)
        self.launch_result = kw.pop("launch_result", None)
        self.focus_result = kw.pop("focus_result", focus_mod.RAISED)
        self.journal_outcome = kw.pop("journal_outcome", journal.OUTCOME_JOURNALED)
        self.ctl = Controller(
            self.store, self.config, None, run=self.tmux, loop=FakeLoop(), clock=Clock(),
            which=lambda n: f"/usr/bin/{n}", journal_dir=tmp_path / "journals",
            focus=self._focus, launcher=self._launch, journal_runner=self._journal,
            notifier=lambda *a: self.notes.append(a), find_transcript=lambda c: None,
            **kw)
        self.ctl.sessions.hostname = "box"
        self.tmp_path = tmp_path

    def _focus(self, name, title, term, reattach):
        self.focus_calls.append((name, title, term, reattach))
        return self.focus_result

    def _launch(self, directory, term):
        self.launches.append((directory, term))
        return self.launch_result or launch_mod.LaunchResult(launch_mod.STARTED, "", directory)

    def _journal(self, project, cwd, duration_s=None, run_skill=True, session_name=None,
                 close_session=None, *, title="", on_progress=None, journal_dir=None,
                 ambiguous=False):
        self.journal_calls.append((session_name, project, cwd, title))
        self.ambiguous[session_name] = ambiguous
        on_progress(0)
        on_progress(1)
        # The row is still there mid-run, flagged journaling.
        row = self.ctl.sessions.find(session_name)
        assert row.phase == PHASE_JOURNALING and row.journal.step == 1
        on_progress(2)
        on_progress(3)
        close_session()
        on_progress(4)
        journal_dir.mkdir(parents=True, exist_ok=True)
        path = journal_dir / f"20261117-1200{len(self.journal_calls):02d}-{project}.md"
        path.write_text(f"# Session journal — {project}\n\n## `/journal`\nok\n")
        return journal.JournalResult(self.journal_outcome, path, "ok")

    def toasts(self):
        return [e.data for e in self.events if e.kind == "toast"]

    def kinds(self):
        return [e.kind for e in self.events]


@pytest.fixture
def h(tmp_path):
    hh = Harness(tmp_path, {
        "claude-webapp": FakeSession(created=OLD, title="✳ Fix login",
                                     cwd="/home/u/dev/webapp"),
        "claude-api": FakeSession(created=YOUNG, cwd="/home/u/dev/api"),
    })
    hh.ctl.start()
    return hh


def test_implements_every_action():
    names = [n for n in dir(Actions) if not n.startswith("_")]
    for n in names:
        assert callable(getattr(Controller, n)), n


def test_start_lists_sessions_and_indexes_receipts(h):
    assert [s.name for s in h.store.state.sessions.items] == ["claude-webapp", "claude-api"]
    assert h.store.state.journal.entries == []


def test_unknown_session_is_a_toast_not_an_exception(h):
    for fn in (h.ctl.focus_session, h.ctl.retitle_session, h.ctl.request_close):
        fn("claude-nope")
    h.ctl.close_session("claude-nope", True)
    h.ctl.answer_prompt("claude-nope", 0, "x")
    assert all(t["error"] for t in h.toasts())
    assert len(h.toasts()) == 5


# ── navigation ──

def test_navigation_events(h):
    h.ctl.open_dashboard("journal:abc")
    h.ctl.open_palette()
    h.ctl.quit()
    assert h.kinds() == ["open_dashboard", "open_palette", "quit"]
    assert h.events[0].data == {"page": "journal:abc"}


def test_pin_ticker_and_hud(h):
    h.ctl.pin_ticker(True)
    assert h.store.state.app.ticker_pinned is True
    h.ctl.set_hud(True)
    assert h.config.get("hud.enabled") is True
    assert h.store.state.settings.values["hud.enabled"] is True


# ── focus ──

def test_focus_raises_with_the_pushed_title(h):
    results = []
    h.ctl.focus_session("claude-webapp", on_result=results.append)
    assert h.focus_calls == [("claude-webapp", "Fix login — webapp", "terminator", True)]
    assert h.toasts()[-1]["text"] == "Raised the terminal for “Fix login”"
    assert results == ["raised"]


def test_focus_reattach_and_failure_copy(tmp_path):
    hh = Harness(tmp_path, {"claude-webapp": FakeSession(created=OLD)},
                 config={"sessions.reattach": False}, focus_result=focus_mod.FAILED)
    hh.ctl.start()
    hh.ctl.focus_session("claude-webapp")
    t = hh.toasts()[-1]
    assert t["error"] and t["action"] == {"label": "Settings", "page": "settings"}
    assert hh.focus_calls[0][3] is False
    hh.focus_result = focus_mod.REATTACHED
    hh.ctl.focus_session("claude-webapp")
    assert hh.toasts()[-1]["text"].startswith("Re-attached “webapp”")


def test_focus_result_is_reported_even_when_nothing_is_tried(h):
    results = []
    h.ctl.focus_session("claude-nope", on_result=results.append)
    assert results == ["failed"] and h.focus_calls == []


def test_retitle(h):
    h.ctl.retitle_session("claude-webapp")
    assert h.toasts()[-1]["text"] == "Title re-read from tmux"
    retitled = [e.data for e in h.events if e.kind == "retitled"]
    assert retitled == [{"name": "claude-webapp"}]


# ── close (SP-1609) ──

def test_request_close_asks_for_an_old_session(h):
    h.ctl.request_close("claude-webapp")
    assert h.events[-1].kind == "confirm_close"
    assert h.events[-1].data == {"name": "claude-webapp"}
    assert h.tmux.killed == []


def test_request_close_closes_a_young_session_at_once_and_says_why(h):
    h.ctl.request_close("claude-api")
    assert h.tmux.killed == ["claude-api"]
    assert h.toasts()[-1]["text"] == "Closed “api”. Open under 2 min, so no journal."
    assert [s.name for s in h.store.state.sessions.items] == ["claude-webapp"]


def test_request_close_with_journaling_off(tmp_path):
    hh = Harness(tmp_path, {"claude-webapp": FakeSession(created=OLD)},
                 config={"sessions.journal": False})
    hh.ctl.start()
    hh.ctl.request_close("claude-webapp")
    assert hh.tmux.killed == ["claude-webapp"]
    assert hh.toasts()[-1]["text"] == \
        "Closed “webapp”. Journaling is off in Settings, so no journal."
    assert hh.journal_calls == []


def test_close_without_journal(h):
    h.ctl.close_session("claude-webapp", journal=False)
    assert h.tmux.killed == ["claude-webapp"] and h.journal_calls == []
    assert h.toasts()[-1]["text"] == "Closed “Fix login”."


def test_close_with_journal_runs_the_skill_then_reports(h):
    h.ctl.close_session("claude-webapp", journal=True)
    assert h.journal_calls == [("claude-webapp", "webapp", "/home/u/dev/webapp", "Fix login")]
    assert h.tmux.killed == ["claude-webapp"]
    assert [s.name for s in h.store.state.sessions.items] == ["claude-api"]
    toast = h.toasts()[-1]
    assert toast["text"] == "Journaled & closed “Fix login”"
    assert toast["action"]["label"] == "View"
    assert toast["action"]["page"].startswith("journal:20261117-1200")
    assert h.notes[-1][0] == "journal" and h.notes[-1][1] == "Journal written · Fix login"
    # The new receipt is in the journal section.
    [entry] = h.store.state.journal.entries
    assert toast["action"]["page"] == f"journal:{entry.id}"


def test_failed_journal_toast_and_notification(tmp_path):
    hh = Harness(tmp_path, {"claude-webapp": FakeSession(created=OLD)},
                 journal_outcome=journal.OUTCOME_SKILL_FAILED)
    hh.ctl.start()
    hh.ctl.close_session("claude-webapp", journal=True)
    t = hh.toasts()[-1]
    assert t["error"] and t["text"] == "Closed. The journal failed, receipt saved."
    assert hh.notes[-1][1] == "Journal failed · webapp"
    assert hh.tmux.killed == ["claude-webapp"]


def test_journal_that_raises_still_closes(tmp_path):
    hh = Harness(tmp_path, {"claude-webapp": FakeSession(created=OLD)})

    def boom(*a, **k):
        raise RuntimeError("skill exploded")

    hh.ctl._run_journal = boom
    hh.ctl.start()
    hh.ctl.close_session("claude-webapp", journal=True)
    assert hh.tmux.killed == ["claude-webapp"]
    assert hh.store.state.sessions.items == []
    assert hh.toasts()[-1]["error"]


def test_busy_sessions_refuse_actions(h):
    h.ctl.sessions.begin_journal("claude-webapp")
    h.ctl.request_close("claude-webapp")
    h.ctl.focus_session("claude-webapp")
    assert h.focus_calls == [] and h.tmux.killed == []
    assert "being journaled" in h.toasts()[-1]["text"]


# ── close all (SP-1603) ──

def test_request_close_all_counts(h):
    h.ctl.request_close_all()
    assert h.events[-1].kind == "confirm_close_all"
    assert h.events[-1].data == {"count": 2, "journaled": 1}


def test_close_all_young_first_then_journals_sequentially(tmp_path):
    hh = Harness(tmp_path, {
        "claude-a": FakeSession(created=OLD, cwd="/p/a"),
        "claude-b": FakeSession(created=OLD + 1, cwd="/p/b"),
        "claude-young": FakeSession(created=YOUNG, cwd="/p/y"),
    })
    hh.ctl.start()
    progress = []
    hh.store.subscribe(lambda s: progress.append(hh.store.state.sessions.close_all))
    hh.ctl.close_all()
    assert hh.tmux.killed[0] == "claude-young"
    assert [c[0] for c in hh.journal_calls] == ["claude-a", "claude-b"]
    assert sorted(hh.tmux.killed) == ["claude-a", "claude-b", "claude-young"]
    seen = [(p.done, p.total) for p in progress if p is not None]
    assert (1, 3) in seen and (2, 3) in seen and (3, 3) in seen
    assert hh.store.state.sessions.close_all is None
    assert hh.toasts()[-1]["text"] == "All sessions closed"
    assert hh.store.state.sessions.items == []


def test_close_all_with_nothing(tmp_path):
    hh = Harness(tmp_path, {})
    hh.ctl.start()
    hh.ctl.close_all()
    hh.ctl.request_close_all()
    assert [t["text"] for t in hh.toasts()] == ["No sessions to close"] * 2


# ── new session (SP-1501/1502) ──

def test_list_start_folders(tmp_path):
    base = tmp_path / "omdev"
    for d in ("ledgerly", "Tacomap", ".git", "apple", "webapp"):
        (base / d).mkdir(parents=True)
    (base / "notes.md").write_text("x")
    hh = Harness(tmp_path, {"claude-webapp": FakeSession(cwd=str(base / "webapp" / "api"))},
                 config={"sessions.dir": str(base)})
    hh.ctl.start()
    out = hh.ctl.list_start_folders()
    assert [f["name"] for f in out] == ["apple", "Tacomap", "ledgerly", "webapp"]
    assert [f["has_session"] for f in out] == [False, False, False, True]
    assert out[0]["path"] == str(base / "apple")


def test_list_start_folders_missing_dir(tmp_path):
    hh = Harness(tmp_path, {}, config={"sessions.dir": str(tmp_path / "nope")})
    assert hh.ctl.list_start_folders() == []


def test_new_session_started_adds_a_launching_row(h):
    h.ctl.request_new_session()
    assert h.events[-1].kind == "new_session"
    h.ctl.new_session("/home/u/dev/ledgerly")
    assert h.launches == [("/home/u/dev/ledgerly", "terminator")]
    row = h.store.state.sessions.items[0]          # starting sorts above idle
    assert row.phase == PHASE_LAUNCHING and row.project == "ledgerly"


@pytest.mark.parametrize("status,needle,field", [
    (launch_mod.TERMINAL_MISSING, "Couldn’t open Terminator. Install it, or set "
                                  "SMARTPANEL_TERMINAL.", "terminal_ok"),
    (launch_mod.TMUX_MISSING, "Install tmux first", "tmux_ok"),
    (launch_mod.FAILED, "Couldn’t start a session", None),
])
def test_new_session_failures(tmp_path, status, needle, field):
    hh = Harness(tmp_path, {}, launch_result=launch_mod.LaunchResult(status, "boom", "/x"))
    hh.ctl.start()
    hh.ctl.new_session("/x")
    t = hh.toasts()[-1]
    assert t["error"] and t["text"].startswith(needle)
    if field:
        assert getattr(hh.store.state.sessions, field) is False
    assert not any(s.phase == PHASE_LAUNCHING for s in hh.store.state.sessions.items)
    if status == launch_mod.TMUX_MISSING:
        assert hh.events[-1].kind == "open_dashboard"


def test_request_new_session_without_tmux(h):
    h.store.update("sessions", tmux_ok=False)
    h.ctl.request_new_session()
    assert h.toasts()[-1]["text"] == "Install tmux first"
    assert h.events[-1].data == {"page": "sessions"}


# ── answering (SP-1303/1306) ──

def _waiting(tmp_path):
    hh = Harness(tmp_path, {"claude-webapp": FakeSession(created=OLD, title="✳ Fix login",
                                                         pane=pane("edit_permission"))})
    hh.ctl.start()
    fp = hh.store.state.sessions.items[0].prompt.fingerprint
    return hh, fp


def test_answer_prompt_end_to_end_with_the_real_answerer(tmp_path, monkeypatch):
    hh, fp = _waiting(tmp_path)

    # The real prompt.answer, with its confirmation wait made instant and the
    # pane flipping to running once a key arrives.
    from smartpanel.sessions import prompt
    real = prompt.answer

    def fast(name, option, expected, run):
        def flipping(args):
            out = run(args)
            if args[0] == "send-keys":
                hh.tmux.sessions[name].pane = pane("running_bash")
            return out
        return real(name, option, expected, run=flipping, sleep=lambda s: None)

    hh.ctl._answer = fast
    hh.ctl.answer_prompt("claude-webapp", 0, fp)
    assert [k[-1] for k in hh.tmux.keys] == ["1"]
    assert hh.toasts()[-1]["text"] == "Sent “1” to Fix login"
    assert hh.store.state.sessions.items[0].activity == "running"


def test_answer_prompt_changed_and_not_waiting_copy(tmp_path):
    hh, fp = _waiting(tmp_path)
    hh.ctl.answer_prompt("claude-webapp", 0, "stale-fingerprint")
    t = hh.toasts()[-1]
    assert t["error"] and t["text"] == "The prompt changed. Open the terminal to answer."
    assert t["action"]["label"] == "Open"
    assert hh.tmux.keys == []
    n = len(hh.toasts())
    hh.tmux.sessions["claude-webapp"].pane = pane("idle_prose_question")
    hh.ctl.answer_prompt("claude-webapp", 0, fp)
    assert len(hh.toasts()) == n          # not_waiting: quiet
    assert hh.tmux.keys == []


def test_answer_prompt_ignores_a_double_click(tmp_path):
    hh, fp = _waiting(tmp_path)
    calls = []
    hh.ctl._answer = lambda *a, **k: calls.append(a) or AnswerResult.SENT
    hh.ctl._answering.add("claude-webapp")
    hh.ctl.answer_prompt("claude-webapp", 0, fp)
    assert calls == []


# ── settings / environment ──

def test_set_setting_error_is_a_toast(h):
    class Bad(type(h.config)):
        def set(self, key, value):
            raise ValueError("must be a number")
    h.ctl.config = Bad()
    h.ctl.set_setting("swap.warn", "lots")
    t = h.toasts()[-1]
    assert t["error"] and "must be a number" in t["text"]


def test_recheck_environment(tmp_path):
    hh = Harness(tmp_path, {})
    hh.tmux.available = False
    hh.ctl.recheck_environment()
    assert hh.store.state.sessions.tmux_ok is False
    assert hh.toasts()[-1]["text"] == "tmux still not found on PATH"
    hh.tmux.available = True
    hh.ctl.recheck_environment()
    assert hh.store.state.sessions.tmux_ok is True
    assert not hh.toasts()[-1]["error"]


def test_terminal_setting_change_rechecks(tmp_path):
    hh = Harness(tmp_path, {})
    hh.ctl.sessions.which = lambda n: None if n == "kitty" else f"/usr/bin/{n}"
    hh.ctl.start()
    assert hh.store.state.sessions.terminal_ok
    hh.ctl.set_setting("sessions.terminal", "kitty")
    assert hh.store.state.sessions.terminal_ok is False
    hh.ctl.stop()


def test_notify_falls_back_to_the_original_send_signature(monkeypatch, h):
    from smartpanel import notify
    sent = []
    monkeypatch.setattr(notify, "send",
                        lambda summary, body="", urgency="normal", key="smartpanel":
                        sent.append((summary, body, urgency, key)))
    h.ctl._notifier = None
    h.ctl._notify("journal_failed", "Journal failed · x", "body", None)
    assert sent == [("Journal failed · x", "body", "critical", "smartpanel-journal_failed")]
    new = []
    monkeypatch.setattr(notify, "send", lambda kind, title, body, action=None:
                        new.append((kind, title, body, action)))
    h.ctl._notify("journal", "Journal written · x", "b", {"label": "View", "page": "journal:1"})
    # The v2 notifier knows the success kind as "journal_ok" and wants a notify.Action.
    assert [(k, t, b) for k, t, b, _ in new] == [("journal_ok", "Journal written · x", "b")]
    button = new[0][3]
    assert isinstance(button, notify.Action) and button.label == "View"
    events = []
    h.store.on_event(events.append)
    button.callback()
    assert events[-1].kind == "open_dashboard" and events[-1].data == {"page": "journal:1"}


def test_open_path_hands_the_file_to_the_opener(tmp_path):
    opened = []
    hh = Harness(tmp_path, opener=lambda uri, done: (opened.append(uri), done(True)))
    f = tmp_path / "2026-11.md"
    f.write_text("x")
    hh.ctl.open_path(str(f))
    assert opened == [f.as_uri()]
    assert hh.toasts() == []


def test_open_path_failure_and_missing_file_toast(tmp_path):
    hh = Harness(tmp_path, opener=lambda uri, done: done(False))
    f = tmp_path / "log.md"
    f.write_text("x")
    hh.ctl.open_path(str(f))
    hh.ctl.open_path(str(tmp_path / "gone.md"))
    assert [(t["text"], t["error"]) for t in hh.toasts()] == [
        ("Couldn’t open log.md", True), ("Couldn’t open gone.md", True)]


def test_open_path_opener_raising_never_escapes(tmp_path):
    def boom(uri, done):
        raise RuntimeError("no portal")
    hh = Harness(tmp_path, opener=boom)
    f = tmp_path / "log.md"
    f.write_text("x")
    hh.ctl.open_path(str(f))
    assert hh.toasts()[0]["text"] == "Couldn’t open log.md"


def test_sessions_sharing_a_folder_journal_as_ambiguous(tmp_path):
    hh = Harness(tmp_path, {
        "claude-ledgerly": FakeSession(created=OLD, cwd="/home/u/dev/ledgerly"),
        "claude-ledgerly-2": FakeSession(created=OLD + 5, cwd="/home/u/dev/ledgerly"),
        "claude-webapp": FakeSession(created=OLD, cwd="/home/u/dev/webapp"),
    })
    hh.ctl.start()
    hh.ctl.close_session("claude-ledgerly", journal=True)
    hh.ctl.close_session("claude-webapp", journal=True)
    assert hh.ambiguous == {"claude-ledgerly": True, "claude-webapp": False}


def test_close_all_marks_a_shared_folder_ambiguous_for_both(tmp_path):
    hh = Harness(tmp_path, {
        "claude-ledgerly": FakeSession(created=OLD, cwd="/home/u/dev/ledgerly"),
        "claude-ledgerly-2": FakeSession(created=OLD + 5, cwd="/home/u/dev/ledgerly"),
        "claude-webapp": FakeSession(created=OLD, cwd="/home/u/dev/webapp"),
    })
    hh.ctl.start()
    hh.ctl.close_all()
    assert hh.ambiguous == {"claude-ledgerly": True, "claude-ledgerly-2": True,
                            "claude-webapp": False}
