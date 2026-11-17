"""GTK-free copy and decisions for the Sessions page and its dialogs (W2-sessions).

Everything the Cockpit, the Board and the session dialogs *say* is decided here,
from plain `Session` records, so it is unit-tested without a display. Copy is
from `docs/design/prototype.html` (`ccard`, `bcard`, `stepsHTML`, `sessionsHTML`,
`dialogHTML`).

Note: importing this module through the `smartpanel.ui.widgets` package loads
GTK (the package `__init__` imports the widget kit), but nothing here needs a
display.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from smartpanel.state.model import (IDLE, JOURNAL_STEPS, PHASE_JOURNALING, PHASE_LAUNCHING,
                                    RUNNING, UNKNOWN, WAITING)
from smartpanel.uiformat import (format_waiting, home_path, journal_step_text,
                                 option_button_label, session_title,
                                 terminal_display_name)

# ───────────────────────── state words ─────────────────────────

COCKPIT, BOARD = "cockpit", "board"
VIEWS = (COCKPIT, BOARD)

# Board columns: (key, heading, glyph css). Starting and unknown sessions sit in
# Idle; journaling ones leave the columns for the drop zone.
BOARD_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (WAITING, "❓ Needs you", "waiting"),
    (RUNNING, "▶ Running", "running"),
    (IDLE, "· Idle", "idle"),
)

TMUX_INSTALL = "sudo apt install tmux"
ERROR_TITLE = "tmux isn’t installed"
ERROR_TEXT = ("smartPanel manages Claude sessions that run inside tmux. "
              "Install it, then check again.")
EMPTY_TITLE = "No Claude sessions running"
EMPTY_MARKUP = ("Sessions you start here run inside tmux as <tt>claude‑&lt;project&gt;</tt>, "
                "so the panel can see what they’re doing, focus them, and journal them "
                "on close.")
NOTHING_HERE = "Nothing here"
DROP_HINT_MARKUP = "Drop a card here to <b>journal &amp; close</b> it"
WAITING_UNPARSED = "This session is waiting for you"
DRAG_TOOLTIP = "Drag to the drop zone to journal & close"


def display_state(s) -> str:
    """The state a card shows: a panel-owned phase wins over the tmux activity."""
    phase = getattr(s, "phase", None)
    if phase in (PHASE_JOURNALING, PHASE_LAUNCHING):
        return phase
    act = getattr(s, "activity", UNKNOWN) or UNKNOWN
    return act if act in (WAITING, RUNNING, IDLE) else UNKNOWN


def busy(s) -> bool:
    """Starting and journaling sessions take no actions (SP-1703)."""
    return display_state(s) in (PHASE_JOURNALING, PHASE_LAUNCHING)


def board_column(s) -> str | None:
    """WAITING / RUNNING / IDLE, or None for a journaling card (drop zone)."""
    st = display_state(s)
    if st == PHASE_JOURNALING:
        return None
    if st in (WAITING, RUNNING):
        return st
    return IDLE


def draggable(s) -> bool:
    return not busy(s)


# ───────────────────────── numbers ─────────────────────────

def fmt_age(created: float | None, now: float | None = None) -> str:
    """The prototype's fmtAge: 'now', '4m', '1h02'. '' when unknown."""
    if not created or created <= 0:
        return ""
    now = time.time() if now is None else now
    mins = int(max(0.0, now - created) // 60)
    if mins < 1:
        return "now"
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h{mins % 60:02d}"


def money(v: float | None) -> str:
    """'$2.90'; '' when unknown (never '$0.00' for a cost we don't know)."""
    if v is None:
        return ""
    return f"${v:,.2f}"


def meta_parts(s, now: float | None = None) -> list[str]:
    """Cockpit meta line: project, age (or 'starting…'), cost, '● attached'."""
    parts = [getattr(s, "project", "") or getattr(s, "name", "")]
    if display_state(s) == PHASE_LAUNCHING:
        parts.append("starting…")
    else:
        age = fmt_age(getattr(s, "created", 0.0), now)
        if age:
            parts.append(age)
    cost = money(getattr(s, "cost_usd", None))
    if cost:
        parts.append(cost)
    if getattr(s, "attached", False):
        parts.append("● attached")
    return parts


def board_detail(s, now: float | None = None) -> str:
    """Right side of a Board card: 'starting…', '4m waiting', or the cost."""
    st = display_state(s)
    if st == PHASE_LAUNCHING:
        return "starting…"
    if st == WAITING:
        return format_waiting(getattr(s, "waiting_since", None), now) or "waiting"
    return money(getattr(s, "cost_usd", None))


# ───────────────────────── prompt ─────────────────────────

def option_labels(prompt) -> list[str]:
    """Answer buttons: '1 · Yes', '2 · Yes' (cut at the first comma), …"""
    if prompt is None:
        return []
    return [option_button_label(i, o) for i, o in enumerate(prompt.options)]


def prompt_lines(prompt) -> list[tuple[str, bool]]:
    """Option rows of the mono block: ('1. Yes', True) for the row under ❯."""
    if prompt is None:
        return []
    cursor = getattr(prompt, "cursor", 0)
    return [(f"{i + 1}. {o}", i == cursor) for i, o in enumerate(prompt.options)]


def has_prompt(s) -> bool:
    p = getattr(s, "prompt", None)
    return p is not None and bool(getattr(p, "options", None))


# ───────────────────────── journal checklist ─────────────────────────

DONE, NOW, TODO, FAIL = "done", "now", "todo", "fail"
STEP_ICON = {DONE: "✓", TODO: "○", FAIL: "✕", NOW: ""}
STEP_WORD = {DONE: "done", NOW: "in progress", TODO: "to do", FAIL: "failed"}


@dataclass(frozen=True)
class Step:
    state: str
    text: str


def journal_steps(s, monthly_log: str | None = None) -> list[Step]:
    """The five steps with their state (done ✓ / now spinner / todo ○ / fail ✕)."""
    jp = getattr(s, "journal", None)
    step = getattr(jp, "step", 0) if jp else 0
    failed = bool(getattr(jp, "failed", False)) if jp else False
    finished = bool(getattr(jp, "outcome", None)) if jp else False
    headless = bool(getattr(jp, "headless", False)) if jp else False
    project = getattr(s, "project", "") or getattr(s, "name", "")
    out = []
    for i in range(len(JOURNAL_STEPS)):
        text = journal_step_text(i, project, headless=headless, monthly_log=monthly_log)
        if i == step and failed:
            out.append(Step(FAIL, f"{text} · failed, closing anyway"))
        elif i < step or (i == step and finished):
            out.append(Step(DONE, text))
        elif i == step:
            out.append(Step(NOW, text))
        else:
            out.append(Step(TODO, text))
    return out


# ───────────────────────── dialogs ─────────────────────────

def confirm_heading(s) -> str:
    return f"Journal “{session_title(s)}” before closing?"


def confirm_age(s, now: float | None = None) -> str:
    age = fmt_age(getattr(s, "created", 0.0), now)
    if age == "now":
        return "under a minute"
    return age or "a while"


def journal_command(s) -> str:
    return f"/journal {getattr(s, 'project', '') or getattr(s, 'name', '')}"


def journal_button_label(seconds_left: int) -> str:
    return f"Journal & close ({seconds_left})" if seconds_left > 0 else "Journal & close"


def close_all_counts(items, *, journal_on: bool, min_age_s: float,
                     now: float | None = None) -> tuple[int, int]:
    """(count, journaled) the way the controller counts them: sessions already
    journaling or still starting are not part of "close all"."""
    now = time.time() if now is None else now
    todo = [s for s in items if not busy(s)]
    if not journal_on:
        return len(todo), 0
    journaled = sum(1 for s in todo
                    if getattr(s, "created", 0) and now - s.created >= min_age_s)
    return len(todo), journaled


def close_all_heading(count: int) -> str:
    return f"Close all {count} session{'s' if count != 1 else ''}?"


def close_all_body(count: int, journaled: int, journal_on: bool = True) -> str:
    if not journal_on:
        lead = "Journaling is off, so they close straight away. "
    elif journaled <= 0:
        lead = ("They were all opened too recently to journal, so they close straight "
                "away. " if count != 1 else
                "It was opened too recently to journal, so it closes straight away. ")
    elif count == 1:
        lead = "It gets a journal entry first. "
    else:
        lead = (f"{journaled} of them get a journal entry first, one at a time, because "
                "they share one monthly log. ")
    return lead + "Running work is interrupted."


def folder_hint(path: str | None, terminal: str | None) -> str:
    if not path:
        return "Pick a folder to start Claude in."
    return (f"Opens {terminal_display_name(terminal or 'terminator')} running Claude inside "
            f"tmux, in {home_path(path)}.")


def crumbs(base: str | None) -> str:
    return f"{home_path(str(base or '~'))} /"


def launch_title(terminal: str | None) -> str:
    return f"Opening {terminal_display_name(terminal or 'terminator')}"


def launch_detail(path: str) -> str:
    return f"claude-tmux in {home_path(path)}"


def starting_text(terminal: str | None) -> str:
    return f"Opening {terminal_display_name(terminal or 'terminator')}…"


def preselect(folders: list[dict], last_used: str | None, items=()) -> int | None:
    """Index of the folder to preselect: the last one opened from the picker,
    else the folder of the newest session, else nothing."""
    paths = [str(f.get("path", "")) for f in folders]
    if last_used and last_used in paths:
        return paths.index(last_used)
    newest = sorted((s for s in items if getattr(s, "cwd", None)),
                    key=lambda s: getattr(s, "created", 0.0), reverse=True)
    for s in newest:
        for i, p in enumerate(paths):
            if p and (s.cwd == p or s.cwd.startswith(p.rstrip("/") + "/")):
                return i
    return None


def close_all_progress(loading) -> str:
    """Toolbar text while "Close all" runs: 'Closing all · 1 of 4 done'."""
    if loading is None or not getattr(loading, "total", 0):
        return ""
    done = max(0, min(loading.done, loading.total))
    return f"Closing all · {done} of {loading.total} done"
