from __future__ import annotations

from smartpanel.sessions import tmux
from smartpanel.sessions.tmux import TmuxSession, _parse_sessions

_FMT = "{name}\t{created}\t{attached}\t{windows}"


def _line(name, created=0, attached=0, windows=1) -> str:
    return _FMT.format(name=name, created=created, attached=attached, windows=windows)


def test_parse_sessions_basic():
    out = "\n".join([
        _line("claude-webapp", created=1000, attached=1, windows=2),
        _line("claude-api", created=2000, attached=0, windows=1),
    ])
    sessions = _parse_sessions(out)
    assert [s.name for s in sessions] == ["claude-webapp", "claude-api"]
    assert sessions[0].created == 1000
    assert sessions[0].attached is True
    assert sessions[0].windows == 2
    assert sessions[1].attached is False


def test_parse_sessions_skips_malformed_and_blank():
    out = "\n".join(["", "claude-x\t100", _line("claude-ok", created=5), "   "])
    sessions = _parse_sessions(out)
    assert [s.name for s in sessions] == ["claude-ok"]


def test_parse_sessions_non_numeric_fields_default_to_zero():
    [s] = _parse_sessions(_line("claude-x", created="bad", attached="", windows="x"))
    assert s.created == 0
    assert s.attached is False
    assert s.windows == 0


def test_parse_sessions_reads_claude_pane_title():
    [s] = _parse_sessions(_line("claude-x") + "\t✳ Add Resend to email wrapper")
    assert s.title == "Add Resend to email wrapper"


def test_parse_sessions_title_optional_and_may_contain_tabs():
    [old] = _parse_sessions(_line("claude-x"))
    assert old.title == ""
    [tabbed] = _parse_sessions(_line("claude-y") + "\tFix\tlogin")
    assert tabbed.title == "Fix\tlogin"


def test_clean_title_strips_status_glyphs():
    assert tmux.clean_title("✳ Fix login bug") == "Fix login bug"
    assert tmux.clean_title("⠂ Fix login bug") == "Fix login bug"  # spinner frame


def test_clean_title_drops_placeholders():
    assert tmux.clean_title("") == ""
    assert tmux.clean_title("✳ Claude Code") == ""
    assert tmux.clean_title("devbox", hostname="devbox") == ""  # tmux's default title
    assert tmux.clean_title("devbox") == "devbox"


def test_project_strips_prefix():
    assert TmuxSession("claude-webapp", 0, False, 0).project == "webapp"
    # A non-prefixed name falls back to the full name.
    assert TmuxSession("scratch", 0, False, 0).project == "scratch"


def _fake_runner(sessions_out: str, *, list_rc: int = 0,
                 list_err: str = "", kill_ok: bool = True):
    calls: list[list[str]] = []

    def run(args: list[str]) -> tuple[int, str, str]:
        calls.append(args)
        if args[:1] == ["list-sessions"]:
            return list_rc, sessions_out, list_err
        if args[:1] == ["kill-session"]:
            return (0, "", "") if kill_ok else (1, "", "no such session")
        return 0, "", ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_list_sessions_filters_by_prefix_and_sorts_newest_first():
    out = "\n".join([
        _line("claude-a", created=100),
        _line("notes", created=999),          # filtered out (no prefix)
        _line("claude-b", created=300),
    ])
    run = _fake_runner(out)
    sessions = tmux.list_sessions(run=run)
    assert [s.name for s in sessions] == ["claude-b", "claude-a"]


def test_list_sessions_empty_when_no_server():
    run = _fake_runner("", list_rc=1, list_err="no server running on /tmp/tmux-1000/default")
    assert tmux.list_sessions(run=run) == []


def test_kill_session_targets_exact_name():
    run = _fake_runner("")
    assert tmux.kill_session("claude-x", run=run) is True
    assert run.calls == [["kill-session", "-t", "=claude-x"]]  # type: ignore[attr-defined]


def test_set_terminal_title_targets_pane_and_escapes_hash():
    run = _fake_runner("")
    assert tmux.set_terminal_title("claude-x", "Fix #42 — x", run=run) is True
    assert run.calls == [  # type: ignore[attr-defined]
        ["set-option", "-t", "=claude-x:", "set-titles", "on"],
        ["set-option", "-t", "=claude-x:", "set-titles-string", "Fix ##42 — x"],
    ]


def test_set_terminal_title_stops_when_session_is_gone():
    run = _cwd_runner("", rc=1)
    assert tmux.set_terminal_title("claude-gone", "gone", run=run) is False
    assert len(run.calls) == 1  # type: ignore[attr-defined]


def _cwd_runner(out: str, rc: int = 0):
    calls: list[list[str]] = []

    def run(args: list[str]) -> tuple[int, str, str]:
        calls.append(args)
        return rc, out, ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_session_cwd_targets_the_session_pane():
    """The trailing `:` is load-bearing — `=name` alone resolves to nothing and
    tmux still exits 0, which silently broke journal-on-close."""
    run = _cwd_runner("/home/user/dev/proj\n")
    assert tmux.session_cwd("claude-proj", run=run) == "/home/user/dev/proj"
    assert run.calls[0][:4] == [  # type: ignore[attr-defined]
        "display-message", "-p", "-t", "=claude-proj:",
    ]


def test_session_cwd_none_when_target_does_not_resolve():
    assert tmux.session_cwd("claude-gone", run=_cwd_runner("\n")) is None


def test_session_cwd_none_on_error():
    assert tmux.session_cwd("claude-gone", run=_cwd_runner("", rc=1)) is None
