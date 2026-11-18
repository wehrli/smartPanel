"""Copy and decisions for the Sessions page and dialogs (GTK-free logic)."""
import pytest

from smartpanel.state.model import (IDLE, JOURNAL_STEPS, PHASE_JOURNALING, PHASE_LAUNCHING,
                                    RUNNING, UNKNOWN, WAITING, JournalProgress, Loading,
                                    Prompt, Session)
from smartpanel.ui.widgets import sessions_model as m

NOW = 1_800_000_000.0


def S(name="claude-a", **kw):
    kw.setdefault("project", name.removeprefix("claude-"))
    return Session(name, **kw)


@pytest.mark.parametrize("secs,text", [(0, "now"), (59, "now"), (60, "1m"), (240, "4m"),
                                       (3600, "1h00"), (3720, "1h02"), (37800, "10h30")])
def test_fmt_age_matches_the_prototype(secs, text):
    assert m.fmt_age(NOW - secs, NOW) == text


def test_fmt_age_unknown():
    assert m.fmt_age(0, NOW) == "" and m.fmt_age(None, NOW) == ""


def test_money_hides_unknown():
    assert m.money(2.9) == "$2.90"
    assert m.money(1234.5) == "$1,234.50"
    assert m.money(None) == ""
    assert m.money(0.0) == "$0.00"


def test_display_state_phase_wins():
    assert m.display_state(S(activity=WAITING)) == WAITING
    assert m.display_state(S(activity=IDLE, phase=PHASE_JOURNALING)) == PHASE_JOURNALING
    assert m.display_state(S(activity=UNKNOWN, phase=PHASE_LAUNCHING)) == PHASE_LAUNCHING
    assert m.display_state(S(activity="weird")) == UNKNOWN


def test_board_columns():
    assert m.board_column(S(activity=WAITING)) == WAITING
    assert m.board_column(S(activity=RUNNING)) == RUNNING
    assert m.board_column(S(activity=IDLE)) == IDLE
    assert m.board_column(S(activity=UNKNOWN)) == IDLE
    assert m.board_column(S(phase=PHASE_LAUNCHING)) == IDLE         # starting sits in Idle
    assert m.board_column(S(phase=PHASE_JOURNALING)) is None        # drop zone
    assert [c[1] for c in m.BOARD_COLUMNS] == ["❓ Needs you", "▶ Running", "· Idle"]


def test_busy_sessions_are_not_draggable():
    assert m.draggable(S(activity=IDLE)) and m.draggable(S(activity=WAITING))
    assert not m.draggable(S(phase=PHASE_LAUNCHING))
    assert not m.draggable(S(phase=PHASE_JOURNALING))


def test_meta_parts():
    s = S("claude-smartPanel", created=NOW - 3720, cost_usd=2.9, attached=True)
    assert m.meta_parts(s, NOW) == ["smartPanel", "1h02", "$2.90", "● attached"]
    s = S(created=NOW - 600, cost_usd=None)
    assert m.meta_parts(s, NOW) == ["a", "10m"]                       # cost hidden, not $0.00
    s = S(created=NOW, phase=PHASE_LAUNCHING, cost_usd=None)
    assert m.meta_parts(s, NOW) == ["a", "starting…"]


def test_board_detail():
    assert m.board_detail(S(phase=PHASE_LAUNCHING), NOW) == "starting…"
    assert m.board_detail(S(activity=WAITING, waiting_since=NOW - 240), NOW) == "4m waiting"
    assert m.board_detail(S(activity=WAITING), NOW) == "waiting"
    assert m.board_detail(S(activity=RUNNING, cost_usd=1.85), NOW) == "$1.85"
    assert m.board_detail(S(activity=IDLE), NOW) == ""


PROMPT = Prompt("Do you want to make this edit to alerts.py?",
                ["Yes", "Yes, allow all edits this session",
                 "No, tell Claude what to do differently"], cursor=1, fingerprint="fp")


def test_option_labels_are_numbered_and_distinct():
    assert m.option_labels(PROMPT) == ["1 · Yes", "2 · Allow all edits", "3 · No"]
    assert m.option_labels(None) == []


def test_option_labels_keep_a_whole_clause_without_a_comma():
    # SP-1305: the alert demo's ledgerly prompt read '1 · Down-migrate 0022…' and
    # '2 · Apply on top of the…'; the Cockpit row wraps, so the clause fits.
    p = Prompt("Which migration strategy should I use for 0023?",
               ["Down-migrate 0022 first", "Apply on top of the current schema",
                "Skip 0023 for now"])
    assert m.option_labels(p) == ["1 · Down-migrate 0022 first",
                                  "2 · Apply on top of the current schema",
                                  "3 · Skip 0023 for now"]
    # still cut at a word past COCKPIT_LABEL_MAX
    long = Prompt("?", ["Rewrite the whole migration history from scratch please", "No"])
    first = m.option_labels(long)[0]
    assert first.endswith("…") and len(first) <= len("1 · ") + m.COCKPIT_LABEL_MAX
    assert first == "1 · Rewrite the whole migration…"


def test_prompt_lines_mark_the_cursor():
    assert m.prompt_lines(PROMPT) == [("1. Yes", False),
                                      ("2. Yes, allow all edits this session", True),
                                      ("3. No, tell Claude what to do differently", False)]


def test_has_prompt():
    assert m.has_prompt(S(prompt=PROMPT))
    assert not m.has_prompt(S(prompt=None))
    assert not m.has_prompt(S(prompt=Prompt("q", [])))


def _states(steps):
    return [s.state for s in steps]


def test_journal_steps_progress():
    s = S("claude-smartPanel", phase=PHASE_JOURNALING, journal=JournalProgress(step=2))
    steps = m.journal_steps(s, "/home/u/dev/omdev/JOURNAL/2026-11.md")
    assert _states(steps) == ["done", "done", "now", "todo", "todo"]
    assert steps[1].text == "Type /journal smartPanel into the session"
    assert steps[2].text == "Skill appends to JOURNAL/2026-11.md"
    assert len(steps) == len(JOURNAL_STEPS)


def test_journal_steps_failure_and_headless():
    s = S(phase=PHASE_JOURNALING, journal=JournalProgress(step=2, failed=True, headless=True))
    steps = m.journal_steps(s)
    assert _states(steps) == ["done", "done", "fail", "todo", "todo"]
    assert steps[1].text == "Resume the session headlessly"
    assert steps[2].text.endswith(" · failed, closing anyway")


def test_journal_steps_without_progress_starts_at_zero():
    assert _states(m.journal_steps(S(phase=PHASE_JOURNALING))) == ["now"] + ["todo"] * 4


def test_confirm_copy():
    s = S("claude-smartPanel", title="Fix swap alert hysteresis", created=NOW - 3720)
    assert m.confirm_heading(s) == "Journal “Fix swap alert hysteresis” before closing?"
    assert m.confirm_age(s, NOW) == "1h02"
    assert m.confirm_age(S(created=NOW - 10), NOW) == "under a minute"
    assert m.journal_command(s) == "/journal smartPanel"
    assert m.journal_button_label(5) == "Journal & close (5)"
    assert m.journal_button_label(0) == "Journal & close"


def test_close_all_counts_like_the_controller():
    items = [S("claude-a", created=NOW - 3600), S("claude-b", created=NOW - 30),
             S("claude-c", created=NOW - 999, phase=PHASE_JOURNALING),
             S("claude-d", created=NOW, phase=PHASE_LAUNCHING), S("claude-e", created=NOW - 500)]
    assert m.close_all_counts(items, journal_on=True, min_age_s=120, now=NOW) == (3, 2)
    assert m.close_all_counts(items, journal_on=False, min_age_s=120, now=NOW) == (3, 0)


def test_close_all_copy():
    assert m.close_all_heading(4) == "Close all 4 sessions?"
    assert m.close_all_heading(1) == "Close all 1 session?"
    assert m.close_all_body(4, 3) == ("3 of them get a journal entry first, one at a time, "
                                      "because they share one monthly log. Running work is "
                                      "interrupted.")
    assert m.close_all_body(4, 0, journal_on=False) == (
        "Journaling is off, so they close straight away. Running work is interrupted.")
    assert "too recently" in m.close_all_body(2, 0)
    assert m.close_all_body(1, 1).startswith("It gets a journal entry first.")


def test_folder_picker_copy(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    assert m.crumbs("~/dev/omdev") == "~/dev/omdev /"
    assert m.crumbs("/home/u/dev/omdev") == "~/dev/omdev /"
    assert m.folder_hint("/home/u/dev/omdev/ledgerly", "terminator") == (
        "Opens Terminator running Claude inside tmux, in ~/dev/omdev/ledgerly.")
    assert m.folder_hint(None, "terminator") == "Pick a folder to start Claude in."
    assert m.launch_title("terminator") == "Opening Terminator"
    assert m.launch_detail("/home/u/dev/omdev/ledgerly") == "claude-tmux in ~/dev/omdev/ledgerly"
    assert m.starting_text("/usr/bin/kitty") == "Opening Kitty…"


def test_preselect_last_used_then_newest_session():
    folders = [{"name": "a", "path": "/d/a"}, {"name": "b", "path": "/d/b"},
               {"name": "c", "path": "/d/c"}]
    assert m.preselect(folders, "/d/b") == 1
    assert m.preselect(folders, "/elsewhere") is None
    items = [S("claude-a", cwd="/d/a", created=1), S("claude-c", cwd="/d/c/sub", created=5)]
    assert m.preselect(folders, None, items) == 2
    assert m.preselect(folders, "/d/a", items) == 0
    assert m.preselect([], "/d/a", items) is None


def test_close_all_progress():
    assert m.close_all_progress(None) == ""
    assert m.close_all_progress(Loading(1, 4)) == "Closing all · 1 of 4 done"
    assert m.close_all_progress(Loading(0, 0)) == ""
