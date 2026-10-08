from __future__ import annotations

from smartpanel.sessions.tmux import TmuxSession
from smartpanel.uiformat import (
    SESSION_TITLE_MAX_CHARS,
    session_heading,
    session_label,
    sessions_status,
)


def test_session_heading_is_the_label_without_flag_or_age():
    s = TmuxSession("claude-landing", created=1000, attached=True, windows=1,
                    title="Hero section redesign", activity="idle")
    assert session_heading(s) == "Hero section redesign — landing"
    assert session_label(s, now=1000 + 120).startswith("· " + session_heading(s) + " ●")


def test_session_heading_without_title_is_the_project():
    assert session_heading(TmuxSession("claude-landing", 0, False, 1)) == "landing"


def test_session_label_leads_with_claude_title():
    s = TmuxSession("claude-webapp-2", created=0, attached=True, windows=1,
                    title="Admin page order management", activity="idle")
    assert session_label(s, now=0) == "· Admin page order management — webapp-2 ●"


def test_session_label_without_title_is_just_the_project():
    s = TmuxSession("claude-webapp", created=1000, attached=False, windows=1,
                    activity="idle")
    assert session_label(s, now=1000 + 3660) == "· webapp   (1h01m)"


def test_session_label_truncates_long_titles():
    s = TmuxSession("claude-x", 0, False, 1, title="word " * 30, activity="idle")
    title = session_label(s, now=0).split(" — ")[0].removeprefix("· ")
    assert len(title) <= SESSION_TITLE_MAX_CHARS
    assert title.endswith("…")


def test_session_label_leads_with_the_activity_glyph():
    waiting = TmuxSession("claude-x", 0, False, 1, title="T", activity="waiting")
    running = TmuxSession("claude-x", 0, False, 1, title="T", activity="running")
    assert session_label(waiting, now=0).startswith("❓ ")
    assert session_label(running, now=0).startswith("▶ ")
    # An unannotated session reserves the column rather than shifting the text.
    assert session_label(TmuxSession("claude-x", 0, False, 1), now=0) == "  x"


def test_sessions_status_omits_zero_counts():
    idle = TmuxSession("claude-a", 0, False, 1, activity="idle")
    assert sessions_status([idle, idle]) == "🧩2"
    assert sessions_status([]) == "🧩0"


def test_sessions_status_badges_waiting_and_running():
    sessions = [
        TmuxSession("claude-a", 0, False, 1, activity="waiting"),
        TmuxSession("claude-b", 0, False, 1, activity="running"),
        TmuxSession("claude-c", 0, False, 1, activity="running"),
        TmuxSession("claude-d", 0, False, 1, activity="idle"),
    ]
    assert sessions_status(sessions) == "🧩4 ❓1 ▶2"
