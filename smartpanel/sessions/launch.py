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
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_TERMINAL = os.environ.get("SMARTPANEL_TERMINAL", "terminator")


def launcher_script() -> Path:
    """Absolute path to scripts/claude-tmux (repo layout: <root>/scripts)."""
    return Path(__file__).resolve().parent.parent.parent / "scripts" / "claude-tmux"


def terminal_available(term: str = DEFAULT_TERMINAL) -> bool:
    return shutil.which(term) is not None


def default_dir() -> Path:
    """Starting folder for the new-session picker.

    Honors `SMARTPANEL_SESSIONS_DIR`; otherwise defaults to `~/dev` if present,
    else the home directory.
    """
    override = os.environ.get("SMARTPANEL_SESSIONS_DIR")
    if override and Path(override).is_dir():
        return Path(override)
    dev = Path.home() / "dev"
    return dev if dev.is_dir() else Path.home()


def terminal_argv(term: str = DEFAULT_TERMINAL, inner: str = "",
                  workdir: str | None = None) -> list[str] | None:
    """Full argv that opens `term` running the shell snippet `inner`.

    Shared by the new-session launcher and the focus fallback in `focus.py`, so
    both inherit the same two hard-won workarounds. Returns None if `term` is
    not installed.
    """
    if shutil.which(term) is None:
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
    if shutil.which("systemd-run"):
        argv = ["systemd-run", "--user", "--scope", "--collect", "--quiet", *argv]
    return argv


def launch_session(cwd: str | os.PathLike | None = None,
                   term: str = DEFAULT_TERMINAL) -> bool:
    """Open `term` running a new managed Claude tmux session in `cwd`.

    Returns False (and logs) if the terminal isn't installed or the launcher
    script is missing — callers surface this to the user.
    """
    if shutil.which(term) is None:
        log.warning("terminal %r not found on PATH", term)
        return False

    script = launcher_script()
    if not script.exists():
        log.warning("launcher script not found: %s", script)
        return False

    workdir = str(cwd) if cwd else str(Path.home())
    argv = terminal_argv(term=term, inner=f"exec {shlex.quote(str(script))}",
                         workdir=workdir)
    if argv is None:
        return False

    try:
        subprocess.Popen(
            argv,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("launched %s session in %s", term, workdir)
        return True
    except OSError as e:
        log.warning("failed to launch %s: %s", term, e)
        return False
