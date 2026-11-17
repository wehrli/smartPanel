from __future__ import annotations

from smartpanel.sessions import launch
from smartpanel.sessions.launch import (
    FAILED, STARTED, TERMINAL_MISSING, TMUX_MISSING, LaunchResult)


def _which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


class Spawn:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, argv, **kw):
        if self.error:
            raise self.error
        self.calls.append(argv)


def _script(tmp_path):
    s = tmp_path / "claude-tmux"
    s.write_text("#!/bin/sh\n")
    return s


def test_started_spawns_the_terminal_in_its_own_scope(tmp_path):
    spawn = Spawn()
    r = launch.launch(tmp_path, "terminator", which=_which("tmux", "terminator", "systemd-run"),
                      popen=spawn, script=_script(tmp_path))
    assert r == LaunchResult(STARTED, "", str(tmp_path))
    assert r.ok
    [argv] = spawn.calls
    assert argv[:2] == ["systemd-run", "--user"]
    assert "--no-dbus" in argv
    assert f"--working-directory={tmp_path}" in argv


def test_tmux_missing_is_checked_first(tmp_path):
    spawn = Spawn()
    r = launch.launch(tmp_path, "terminator", which=_which(), popen=spawn,
                      script=_script(tmp_path))
    assert r.status == TMUX_MISSING
    assert spawn.calls == []


def test_terminal_missing(tmp_path):
    spawn = Spawn()
    r = launch.launch(tmp_path, "terminator", which=_which("tmux"), popen=spawn,
                      script=_script(tmp_path))
    assert r.status == TERMINAL_MISSING
    assert not r.ok and spawn.calls == []


def test_missing_script_or_folder_fails_with_a_message(tmp_path):
    r = launch.launch(tmp_path, "terminator", which=_which("tmux", "terminator"),
                      popen=Spawn(), script=tmp_path / "nope")
    assert r.status == FAILED and "launcher script" in r.message
    r = launch.launch(tmp_path / "missing", "terminator",
                      which=_which("tmux", "terminator"), popen=Spawn(),
                      script=_script(tmp_path))
    assert r.status == FAILED and "not a folder" in r.message


def test_spawn_error_is_failed_not_raised(tmp_path):
    r = launch.launch(tmp_path, "terminator", which=_which("tmux", "terminator"),
                      popen=Spawn(OSError("exec format error")), script=_script(tmp_path))
    assert r.status == FAILED and "exec format error" in r.message
