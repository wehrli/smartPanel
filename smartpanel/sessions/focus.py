"""Bring a session's terminal window to the front.

Clicking a row in the dashboard should put you in front of that Claude session.
Doing that from outside the compositor is the hard part, and how hard depends
entirely on the desktop:

**GNOME Wayland (the target desktop) forbids it.** A client cannot raise another
client's window, and every external back door is shut: `org.gnome.Shell.Eval`
returns `(false, '')` unless the shell is in unsafe mode, and
`org.gnome.Shell.Introspect.GetWindows` answers `AccessDenied` to callers that
aren't allowlisted. `wmctrl`/`xdotool` see nothing, because Terminator under
GNOME 46 is a native Wayland client, not XWayland. Verified on the dev box; this
is policy, not a missing package.

So this is a ladder, tried in order, and it always ends somewhere useful:

1. `SMARTPANEL_FOCUS_CMD` — an explicit escape hatch, with `{session}`,
   `{title}` and `{name}` placeholders. Whatever works on your desktop wins.
2. **Window Calls** (`window-calls@domandoman.xyz`), the GNOME extension that
   re-exposes window listing and activation on D-Bus. Installing it is what
   makes true raise-in-place work on Wayland; without it step 2 is a no-op.
3. `wmctrl` / `xdotool`, for X11 and XWayland sessions.
4. **Re-attach** — open a fresh terminal running `tmux attach -d` on that
   session. `-d` detaches the old client, whose `tmux attach` then exits and
   takes its window with it, so you end up with exactly one window per session
   again. tmux owns the screen and the scrollback, so nothing is lost: the new
   window shows the same content, at the same scroll position.

Step 4 is why clicking works on a stock Ubuntu desktop at all. It replaces a
window rather than raising one, so it is opt-out (`SMARTPANEL_FOCUS_REATTACH=0`)
for anyone whose steps 1-3 already work.

Every function returns rather than raises, and window/terminal tooling being
absent degrades to `FAILED`. Subprocess invocation sits behind injectable
runners so the ladder's decision logic is unit-testable with no desktop.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from typing import Callable

from smartpanel.sessions.launch import DEFAULT_TERMINAL, terminal_argv

log = logging.getLogger(__name__)

# How the focus was achieved — returned so the caller can tell the user why a
# window was replaced rather than raised.
RAISED = "raised"          # the real window was brought to the front
REATTACHED = "reattached"  # a fresh terminal took the session over
FAILED = "failed"

# Shell command run before anything else, if set. Placeholders: {session} (the
# tmux session name), {title} / {name} (the window title the panel pushed).
FOCUS_CMD = os.environ.get("SMARTPANEL_FOCUS_CMD", "")
# Set to 0/false to never replace a window when raising is impossible.
REATTACH_ENABLED = os.environ.get("SMARTPANEL_FOCUS_REATTACH", "1").strip().lower() \
    not in ("0", "false", "no", "off")

_WINDOW_CALLS = (
    "--session", "--dest", "org.gnome.Shell",
    "--object-path", "/org/gnome/Shell/Extensions/Windows",
)
_TIMEOUT_S = 3.0

# (returncode, stdout, stderr), same shape as tmux.Runner.
Runner = Callable[[list[str]], "tuple[int, str, str]"]


def _run(argv: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_S)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as e:  # noqa: BLE001 — a focus attempt must never crash the loop
        return 1, "", str(e)


def focus_session(name: str, title: str = "", *, run: Runner = _run,
                  spawn: Callable[[list[str]], bool] | None = None,
                  term: str = DEFAULT_TERMINAL,
                  reattach: bool | None = None) -> str:
    """Put the terminal running tmux session `name` in front. Returns a reason.

    `title` is the window title the panel pushed for this session
    (`uiformat.session_heading`) — the only handle the window managers give us.
    """
    title = title or name
    for attempt in (_via_env_cmd, _via_window_calls, _via_x11):
        if attempt(name, title, run):
            log.info("focused %s via %s", name, attempt.__name__)
            return RAISED
    allow = REATTACH_ENABLED if reattach is None else reattach
    if allow and _via_reattach(name, term, spawn or _spawn):
        return REATTACHED
    log.warning("could not focus session %s", name)
    return FAILED


# ---- the rungs ----

def _via_env_cmd(name: str, title: str, run: Runner) -> bool:
    if not FOCUS_CMD:
        return False
    cmd = FOCUS_CMD.format(session=name, title=title, name=title)
    rc, _, err = run(["sh", "-c", cmd])
    if rc != 0:
        log.debug("SMARTPANEL_FOCUS_CMD failed (rc=%d): %s", rc, err.strip())
    return rc == 0


def _via_window_calls(name: str, title: str, run: Runner) -> bool:
    """GNOME's Window Calls extension: list windows, activate the matching id."""
    rc, out, _ = run(["gdbus", "call", *_WINDOW_CALLS,
                      "--method", "org.gnome.Shell.Extensions.Windows.List"])
    if rc != 0 or not out.strip():
        return False
    win_id = pick_window(out, title)
    if win_id is None:
        return False
    rc, _, err = run(["gdbus", "call", *_WINDOW_CALLS,
                      "--method", "org.gnome.Shell.Extensions.Windows.Activate",
                      str(win_id)])
    if rc != 0:
        log.debug("Windows.Activate(%s) failed: %s", win_id, err.strip())
    return rc == 0


def _via_x11(name: str, title: str, run: Runner) -> bool:
    """wmctrl / xdotool — only ever succeeds on X11 or for XWayland windows."""
    if shutil.which("wmctrl"):
        rc, _, _ = run(["wmctrl", "-a", title])
        if rc == 0:
            return True
    if shutil.which("xdotool"):
        rc, out, _ = run(["xdotool", "search", "--name", title])
        win = out.split()[0] if out.split() else ""
        if rc == 0 and win:
            rc, _, _ = run(["xdotool", "windowactivate", win])
            return rc == 0
    return False


def _via_reattach(name: str, term: str, spawn: Callable[[list[str]], bool]) -> bool:
    """Open a new terminal owning the session; `-d` evicts the stale client.

    `-d` is what makes this a move rather than a duplicate: without it both
    windows stay attached and tmux shrinks the session to the smaller of the
    two, garbling the TUI in both.
    """
    inner = f"exec tmux attach-session -d -t {shlex.quote('=' + name)}"
    argv = terminal_argv(term=term, inner=inner)
    if argv is None:
        return False
    return spawn(argv)


def _spawn(argv: list[str]) -> bool:
    try:
        subprocess.Popen(
            argv,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError as e:
        log.warning("failed to spawn %s: %s", argv[0], e)
        return False


# ---- pure helpers ----

def pick_window(listing: str, title: str) -> int | None:
    """Best window id for `title` from Window Calls' JSON-in-a-GVariant output.

    `gdbus` wraps the extension's JSON in `('[{...}]',)`, so the payload is
    extracted by slicing to the outermost brackets rather than parsing GVariant.
    Exact title match wins; a substring match is the fallback, because the
    terminal may decorate the title it was given.
    """
    import json
    start, end = listing.find("["), listing.rfind("]")
    if start < 0 or end < start:
        return None
    try:
        windows = json.loads(listing[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(windows, list):
        return None

    exact, partial = None, None
    for w in windows:
        if not isinstance(w, dict):
            continue
        wid, wtitle = w.get("id"), (w.get("title") or "")
        if not isinstance(wid, int):
            continue
        if wtitle == title:
            exact = wid if exact is None else exact
        elif title and title in wtitle and partial is None:
            partial = wid
    return exact if exact is not None else partial
