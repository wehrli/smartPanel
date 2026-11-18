"""Spawn a new Claude session in a terminal window.

A "new session" opens a terminal (Terminator by default) running the
`scripts/claude-tmux` launcher in the chosen directory. That launcher starts
`claude` inside a `claude-`prefixed tmux session, so the new session immediately
shows up in the panel and is killable from it.

The terminal command runs through a login shell (`bash -lc`) so the user's PATH
(e.g. `~/.local/bin/claude`) is available. The terminal is overridable via the
`SMARTPANEL_TERMINAL` env var.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

DEFAULT_TERMINAL = os.environ.get("SMARTPANEL_TERMINAL", "terminator")


def launcher_script() -> Path:
    """Absolute path to scripts/claude-tmux (repo layout: <root>/scripts)."""
    return Path(__file__).resolve().parent.parent.parent / "scripts" / "claude-tmux"


def terminal_argv(term: str = DEFAULT_TERMINAL, inner: str = "",
                  workdir: str | None = None,
                  which: Callable[[str], str | None] | None = None) -> list[str] | None:
    """Full argv that opens `term` running the shell snippet `inner`.

    Shared by the new-session launcher and the focus fallback in `focus.py`, so
    both inherit the same two hard-won workarounds. Returns None if `term` is
    not installed.
    """
    which = which or shutil.which
    if which(term) is None:
        log.warning("terminal %r not found on PATH", term)
        return None
    argv = [term]
    # Terminator is single-instance over DBus by default: a second invocation
    # hands off to the running instance and DROPS the `-x` command. `--no-dbus`
    # forces a standalone process that actually runs our command.
    if Path(term).name == "terminator":
        argv.append("--no-dbus")
    if workdir:
        argv.append(f"--working-directory={workdir}")
    # `-x` runs the rest of argv as the command; a login shell loads PATH so
    # `claude` (often in ~/.local/bin) and `tmux` resolve.
    argv += ["-x", "bash", "-lc", inner]
    # Under the systemd user service, children share smartpanel.service's cgroup
    # and KillMode=control-group kills them on every restart/stop, taking the
    # terminal, the tmux server and every Claude session with it. A transient
    # scope moves the terminal into its own cgroup so sessions outlive the panel.
    if which("systemd-run"):
        argv = ["systemd-run", "--user", "--scope", "--collect", "--quiet", *argv]
    return argv


# Launch outcomes (SP-1502). Strings so they travel over D-Bus unchanged.
STARTED = "started"
TERMINAL_MISSING = "terminal_missing"
TMUX_MISSING = "tmux_missing"
FAILED = "failed"


@dataclass(frozen=True)
class LaunchResult:
    status: str
    message: str = ""
    directory: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STARTED


def launch(cwd: str | os.PathLike | None = None, term: str = DEFAULT_TERMINAL, *,
           which: Callable[[str], str | None] | None = None,
           popen: Callable[..., object] | None = None,
           script: Path | None = None) -> LaunchResult:
    """Open `term` running a new managed Claude tmux session in `cwd`.

    Never raises: every way this can go wrong is a `LaunchResult` the UI can
    word properly. tmux is checked first, because without it the terminal would
    open, print an error and close — the worst way to find out.
    """
    which = which or shutil.which
    popen = popen or subprocess.Popen
    workdir = str(Path(cwd).expanduser()) if cwd else str(Path.home())
    if which("tmux") is None:
        return LaunchResult(TMUX_MISSING, "tmux is not installed", workdir)
    if which(term) is None:
        return LaunchResult(TERMINAL_MISSING, f"{term} is not on PATH", workdir)
    script = script or launcher_script()
    if not Path(script).exists():
        return LaunchResult(FAILED, f"launcher script not found: {script}", workdir)
    if not Path(workdir).is_dir():
        return LaunchResult(FAILED, f"not a folder: {workdir}", workdir)
    argv = terminal_argv(term=term, inner=f"exec {shlex.quote(str(script))}",
                         workdir=workdir, which=which)
    if argv is None:
        return LaunchResult(TERMINAL_MISSING, f"{term} is not on PATH", workdir)
    try:
        popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log.warning("failed to launch %s: %s", term, e)
        return LaunchResult(FAILED, str(e), workdir)
    log.info("launched %s session in %s", term, workdir)
    return LaunchResult(STARTED, "", workdir)
