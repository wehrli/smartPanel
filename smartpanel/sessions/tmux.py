"""Control layer over `tmux` for managing Claude Code sessions.

Every Claude session managed by smartPanel runs inside its own tmux session,
launched by `scripts/claude-tmux` with a name prefixed `claude-`. That prefix is
the contract: smartPanel only ever lists or kills sessions matching it, so the
user's unrelated tmux work is never touched.

Subprocess invocation is isolated behind `_run` so the parsing logic
(`_parse_sessions`) and the kill-selection logic can be unit-tested with a fake
runner and no tmux installed. Functions never raise — tmux missing, no server
running, or a malformed line all degrade to an empty result, matching the
"sensors never crash the loop" rule in CLAUDE.md.
"""
from __future__ import annotations

import logging
import re
import socket
import subprocess
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

# Session-name prefix the launcher uses; also the management boundary.
SESSION_PREFIX = "claude-"

# Field order requested from tmux. Kept in sync with _parse_sessions.
# `pane_title` is the session's active pane title — Claude Code sets it (OSC 2)
# to its own AI-generated summary of the conversation, e.g. "✳ Fix login bug".
_LIST_FORMAT = ("#{session_name}\t#{session_created}\t#{session_attached}"
                "\t#{session_windows}\t#{pane_title}")

# Leading status glyphs Claude Code puts before the title (✳ idle, braille
# spinner frames while working) — anything that isn't a word character.
_TITLE_GLYPHS = re.compile(r"^\W+")
# Titles that carry no information: tmux's default (the hostname) and the
# placeholder Claude Code shows before it has summarised anything.
_PLACEHOLDER_TITLES = {"claude code", "claude"}

# A runner takes tmux args (without the leading "tmux") and returns
# (returncode, stdout, stderr). Swapped out in tests.
Runner = Callable[[list[str]], "tuple[int, str, str]"]

_TIMEOUT_S = 2.0


@dataclass(frozen=True, slots=True)
class TmuxSession:
    name: str
    created: int          # epoch seconds, 0 if unknown
    attached: bool        # at least one client attached
    windows: int          # window count, 0 if unknown
    title: str = ""       # Claude's conversation title, "" until it has one
    # What the session is doing: see sessions/activity.py. Not filled by
    # `list_sessions` — reading it costs one capture-pane per session, so
    # callers opt in via `activity.annotate()`.
    activity: str = "unknown"

    @property
    def project(self) -> str:
        """Best-effort project label: the session name minus the prefix."""
        if self.name.startswith(SESSION_PREFIX):
            return self.name[len(SESSION_PREFIX):] or self.name
        return self.name


def _run(args: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "tmux not found"
    except subprocess.TimeoutExpired:
        log.warning("tmux %s timed out", " ".join(args))
        return 124, "", "timeout"
    except OSError as e:
        log.warning("tmux %s failed: %s", " ".join(args), e)
        return 1, "", str(e)


def tmux_available(run: Runner = _run) -> bool:
    rc, _, _ = run(["-V"])
    return rc == 0


def list_sessions(prefix: str = SESSION_PREFIX, run: Runner = _run,
                  hostname: str | None = None) -> list[TmuxSession]:
    """List managed tmux sessions, newest first.

    Returns [] when tmux is absent or no server is running (rc != 0). Only
    sessions whose name starts with `prefix` are returned; pass "" to list all. `hostname` (default: this box's) is
    the placeholder pane title that `clean_title` drops.
    """
    rc, out, err = run(["list-sessions", "-F", _LIST_FORMAT])
    if rc != 0:
        # "no server running on ..." is the normal idle case, not an error.
        if err and "no server running" not in err:
            log.debug("tmux list-sessions rc=%d: %s", rc, err.strip())
        return []
    host = socket.gethostname() if hostname is None else hostname
    sessions = [s for s in _parse_sessions(out, hostname=host)
                if s.name.startswith(prefix)]
    sessions.sort(key=lambda s: s.created, reverse=True)
    return sessions


def session_cwd(name: str, run: Runner = _run) -> str | None:
    """Current working directory of a session's active pane, or None.

    Used to locate the session's Claude transcript before it is killed.

    The target is a *pane* target, not a session target: the trailing `:` is
    what scopes it to the session's active pane. Without it tmux parses
    `=<name>` as a window/pane lookup, fails to resolve it, and — the nasty
    part — still exits 0 with an empty format, so every lookup silently
    returned None. The `=` still forces exact-name matching.
    """
    rc, out, _ = run(["display-message", "-p", "-t", f"={name}:", "#{pane_current_path}"])
    if rc != 0:
        return None
    path = out.strip()
    if not path:
        log.warning("no cwd for tmux session %s (target did not resolve)", name)
        return None
    return path


def capture_pane(name: str, run: Runner = _run) -> str:
    """Text of the session's visible active pane, or "" if it can't be read.

    No `-S`: we want the screen as drawn, not scrollback. `-p` prints to stdout,
    and the same pane target (`=<name>:`) as send-keys applies. Escape sequences
    are stripped by tmux, so the result is plain text with box-drawing glyphs.
    """
    rc, out, _ = run(["capture-pane", "-p", "-t", f"={name}:"])
    return out if rc == 0 else ""


def has_session(name: str, run: Runner = _run) -> bool:
    """True if a session with this exact name is alive."""
    rc, _, _ = run(["has-session", "-t", f"={name}"])
    return rc == 0


def send_text(name: str, text: str, run: Runner = _run) -> bool:
    """Type `text` into the session's active pane, without submitting it.

    `-l` sends the string literally; without it tmux would interpret words like
    `Enter` or `C-c` in the payload as key names.
    """
    rc, _, err = run(["send-keys", "-t", f"={name}:", "-l", text])
    if rc != 0:
        log.warning("send-keys to %s failed: %s", name, err.strip())
    return rc == 0


def send_enter(name: str, run: Runner = _run) -> bool:
    """Press Enter in the session's active pane."""
    rc, _, err = run(["send-keys", "-t", f"={name}:", "Enter"])
    if rc != 0:
        log.warning("send-keys Enter to %s failed: %s", name, err.strip())
    return rc == 0


def send_keys(name: str, keys: list[str], run: Runner = _run) -> bool:
    """Press named keys (`Up`, `Down`, `Enter`…) in the session's active pane.

    Unlike `send_text` there is no `-l`: these are tmux key names, not text.
    One call for the whole sequence, so nothing can land between the keys.
    """
    if not keys:
        return True
    rc, _, err = run(["send-keys", "-t", f"={name}:", *keys])
    if rc != 0:
        log.warning("send-keys %s to %s failed: %s", keys, name, err.strip())
    return rc == 0


def set_terminal_title(name: str, text: str, run: Runner = _run) -> bool:
    """Set the terminal window title tmux shows while this session is attached.

    Turns `set-titles` on as well, so sessions started before the launcher did
    that still get a title. `#` is doubled because tmux expands
    `set-titles-string` as a format and a conversation title may contain one.
    Same pane target as send-keys: set-option rejects a bare `=name`.
    """
    target = f"={name}:"
    rc, _, err = run(["set-option", "-t", target, "set-titles", "on"])
    if rc == 0:
        rc, _, err = run(["set-option", "-t", target, "set-titles-string",
                          text.replace("#", "##")])
    if rc != 0:
        log.warning("setting terminal title for %s failed: %s", name, err.strip())
    return rc == 0


def kill_session(name: str, run: Runner = _run) -> bool:
    """Kill a single session by exact name. Returns True on success."""
    rc, _, err = run(["kill-session", "-t", f"={name}"])
    if rc != 0:
        log.warning("failed to kill session %s: %s", name, err.strip())
    return rc == 0


def kill_all(prefix: str = SESSION_PREFIX, run: Runner = _run) -> int:
    """Kill every managed session. Returns the count successfully killed."""
    killed = 0
    for s in list_sessions(prefix=prefix, run=run):
        if kill_session(s.name, run=run):
            killed += 1
    return killed


def clean_title(raw: str, hostname: str = "") -> str:
    """Turn a raw pane title into a display title, or "" if it says nothing.

    Strips Claude Code's leading status glyph and drops placeholders: tmux's
    default pane title is the hostname, and a fresh session shows "Claude Code"
    until its first exchange has been summarised.
    """
    title = _TITLE_GLYPHS.sub("", raw or "").strip()
    if not title or title.casefold() in _PLACEHOLDER_TITLES:
        return ""
    if hostname and title == hostname:
        return ""
    return title


def _parse_sessions(stdout: str, hostname: str = "") -> list[TmuxSession]:
    """Parse the tab-delimited output of `tmux list-sessions -F _LIST_FORMAT`.

    One session per line: name<TAB>created<TAB>attached<TAB>windows<TAB>title.
    The title is optional and may itself contain tabs. Malformed lines are
    skipped rather than raising.
    """
    sessions: list[TmuxSession] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 4)
        if len(parts) < 4:
            log.debug("skipping malformed tmux line: %r", line)
            continue
        name, created, attached, windows = parts[0], parts[1], parts[2], parts[3]
        if not name:
            continue
        sessions.append(
            TmuxSession(
                name=name,
                created=_as_int(created),
                attached=_as_int(attached) > 0,
                windows=_as_int(windows),
                title=clean_title(parts[4], hostname) if len(parts) > 4 else "",
            )
        )
    return sessions


def _as_int(value: str) -> int:
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return 0
