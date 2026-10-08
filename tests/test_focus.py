from __future__ import annotations

import json
import shlex

from smartpanel.sessions import focus
from smartpanel.sessions.focus import FAILED, RAISED, REATTACHED, pick_window

# What `gdbus call` prints for the Window Calls extension: the extension's JSON
# wrapped in a single-element GVariant tuple.
def _listing(windows) -> str:
    return "('%s',)" % json.dumps(windows).replace("'", "")


WINDOWS = [
    {"id": 11, "wm_class": "Terminator", "title": "Fix login bug — webapp"},
    {"id": 22, "wm_class": "Terminator", "title": "Panel work — smartPanel"},
    {"id": 33, "wm_class": "firefox", "title": "Panel work — smartPanel — Mozilla"},
]


def test_pick_window_prefers_an_exact_title_match():
    assert pick_window(_listing(WINDOWS), "Panel work — smartPanel") == 22


def test_pick_window_falls_back_to_a_substring_match():
    # The terminal may decorate the title it was handed. First substring hit
    # wins — there is nothing better to rank on once the exact match is gone.
    assert pick_window(_listing(WINDOWS), "smartPanel") == 22
    assert pick_window(_listing(WINDOWS), "Mozilla") == 33


def test_pick_window_returns_none_when_nothing_matches():
    assert pick_window(_listing(WINDOWS), "nothing like this") is None


def test_pick_window_survives_garbage():
    assert pick_window("", "x") is None
    assert pick_window("(false, '')", "x") is None
    assert pick_window("('[not json',)", "x") is None
    assert pick_window("('{\"id\": 1}',)", "x") is None  # object, not a list


def test_env_command_short_circuits_the_ladder(monkeypatch):
    seen = []
    monkeypatch.setattr(focus, "FOCUS_CMD", "myfocus {session} {title}")

    def run(argv):
        seen.append(argv)
        return 0, "", ""

    assert focus.focus_session("claude-x", "Fix login — x", run=run) == RAISED
    assert seen == [["sh", "-c", "myfocus claude-x Fix login — x"]]


def test_window_calls_lists_then_activates(monkeypatch):
    monkeypatch.setattr(focus, "FOCUS_CMD", "")
    seen = []

    def run(argv):
        seen.append(argv)
        if "org.gnome.Shell.Extensions.Windows.List" in argv:
            return 0, _listing(WINDOWS), ""
        return 0, "", ""

    assert focus.focus_session("claude-webapp", "Fix login bug — webapp",
                               run=run) == RAISED
    assert seen[-1][-2:] == ["org.gnome.Shell.Extensions.Windows.Activate", "11"]


def test_falls_through_to_reattach_when_nothing_can_raise(monkeypatch):
    """The GNOME Wayland case: every raise route refuses, so a fresh terminal
    takes the session over instead."""
    monkeypatch.setattr(focus, "FOCUS_CMD", "")
    spawned = []

    assert focus.focus_session(
        "claude-webapp", "Fix login bug — webapp",
        run=lambda argv: (1, "", "AccessDenied"),
        spawn=lambda argv: spawned.append(argv) or True,
        term="terminator",
    ) == REATTACHED
    [argv] = spawned
    # -d is load-bearing: without it both clients stay attached and tmux shrinks
    # the session to the smaller window, garbling the TUI in both.
    assert f"exec tmux attach-session -d -t {shlex.quote('=claude-webapp')}" in argv


def test_reattach_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(focus, "FOCUS_CMD", "")
    spawned = []
    assert focus.focus_session(
        "claude-x", "x",
        run=lambda argv: (1, "", ""),
        spawn=lambda argv: spawned.append(argv) or True,
        reattach=False,
    ) == FAILED
    assert spawned == []


def test_failed_spawn_reports_failure(monkeypatch):
    monkeypatch.setattr(focus, "FOCUS_CMD", "")
    assert focus.focus_session(
        "claude-x", "x",
        run=lambda argv: (1, "", ""),
        spawn=lambda argv: False,
        reattach=True,
    ) == FAILED
