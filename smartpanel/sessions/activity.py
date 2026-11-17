"""What each Claude session is actually doing right now.

Three states, read off the tmux pane the session's TUI draws into:

    RUNNING  Claude is working — generating, running a tool, or waiting on a
             background agent. You can look away.
    WAITING  Claude is blocked on *you* — a permission prompt, a plan approval,
             an AskUserQuestion box. Nothing moves until you answer.
    IDLE     Nothing is happening; the input box is free.

**Why the pane and not the transcript.** `journal.classify_last_event` already
tells idle from mid-turn, but it cannot see WAITING at all: a permission prompt
is a TUI state, not an event — nothing is written to the transcript until after
you answer. Background-agent progress is invisible there too. The pane is the
only surface where the TUI's own state exists.

**The markers are structural, not phrasal.** An earlier cut keyed on "Do you
want…" and immediately mis-read an idle session whose last assistant line was
*"Do you want me to commit those two files?"* — prose, not a prompt. What
actually distinguishes a prompt is the modal footer Claude Code prints under
every one of them, `Enter to confirm · Esc to cancel`, and the shape of the
choice list above it: a cursor (`❯`) on a numbered option with siblings under
it. Either is enough on its own — the footer catches the folder-trust prompt,
whose options are unnumbered, and the numbered shape catches anything the
footer wording changes under.

**What is deliberately *not* a marker: a bare `❯` with indented lines below.**
That is also exactly how the TUI echoes a wrapped user message, and how the
input box renders — both would read as WAITING on a perfectly idle session.

Cost: one `tmux capture-pane` per session (~2.4ms), so a sweep of a typical 4-8
sessions stays well inside the 50ms main-loop budget in CLAUDE.md. Only the
bottom `TAIL_LINES` of the visible pane are scanned — a prompt box is always at
the bottom, and older output is exactly what produces false positives.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Iterable, Sequence

from smartpanel.sessions.tmux import Runner, TmuxSession, _run, capture_pane

IDLE = "idle"
RUNNING = "running"
WAITING = "waiting"
UNKNOWN = "unknown"

# Bottom slice of the pane we classify on. A permission box for a long command
# is ~20 rows; 30 clears it without reaching back into settled output.
TAIL_LINES = 30

# Claude Code prints one of these in the status line whenever work is in
# flight: the spinner's "(12s · ↑ 1.2k tokens · esc to interrupt)", the footer's
# "· esc to interrupt", or "✻ Waiting for 2 background agents to finish" when
# the main turn is done but agents are not.
_RUNNING_MARKERS = ("esc to interrupt", "background agent")
# The same idle-but-not-done line for other kinds of background work, e.g.
# "✻ Waiting for 1 dynamic workflow to finish" (real capture).
_WAITING_FOR = re.compile(r"waiting for \d+ [a-z][\w -]*? to finish")

# The hint line under any modal Claude Code puts up: "Enter to confirm · Esc to
# cancel" on a permission or trust prompt, "Enter to select · Tab/Arrow keys to
# navigate · Esc to cancel" on a question. Anchored to the start of a line and
# to the bottom of the pane: the words themselves can appear in ordinary output
# (a session quoting another session's screen does exactly that), but only a
# live modal puts them on their own line within a few rows of the input box.
_CONFIRM_FOOTER = re.compile(r"^[\s│|╎┃]*enter to (confirm|select)\b")
_FOOTER_WINDOW = 8
# A selection cursor with something selected. Required alongside the footer,
# because a modal always has one and quoted output rarely does — an idle input
# box renders a bare "❯" with nothing after it. Not sufficient on its own: this
# is also how the TUI echoes a user message back.
_OPTION_CURSOR = re.compile(r"^[\s│|╎┃]*[❯>]\s+\S")

# The highlighted option in a choice box: "│ ❯ 1. Yes                    │".
# Leading box-drawing and whitespace only — anchored so prose can't match.
_CHOICE_CURSOR = re.compile(r"^[\s│|╎┃]*[❯>]\s*(\d+)\.\s")
# Any option line in the same box, cursor or not.
_CHOICE_OPTION = re.compile(r"^[\s│|╎┃]*(?:[❯>]\s*)?(\d+)\.\s")
# Claude Code never offers a one-item choice; requiring a second option is what
# keeps a user who typed "1. foo" at the prompt from reading as WAITING.
_MIN_CHOICES = 2


def classify_pane(text: str, tail_lines: int = TAIL_LINES) -> str:
    """Classify a captured pane. Pure — the unit-tested half of this module.

    WAITING is checked first: a permission box can be on screen while the
    footer still says "esc to interrupt", and "it needs you" is the more
    actionable of the two.
    """
    if not text or not text.strip():
        return UNKNOWN
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()  # tmux usually trims these; a fake runner may not
    tail = lines[-tail_lines:]
    if _has_confirm_footer(tail) or _has_choice_box(tail):
        return WAITING
    lowered = "\n".join(tail).lower()
    if any(m in lowered for m in _RUNNING_MARKERS) or _WAITING_FOR.search(lowered):
        return RUNNING
    return IDLE


def _has_confirm_footer(lines: Sequence[str]) -> bool:
    """A modal hint near the bottom, with a selection cursor somewhere above it.

    The cursor can be far up — an AskUserQuestion box puts its options above a
    dozen lines of explanation — so it is looked for across the whole tail
    rather than in a fixed window above the footer.
    """
    if not any(_CONFIRM_FOOTER.match(line.lower())
               for line in lines[-_FOOTER_WINDOW:]):
        return False
    return any(_OPTION_CURSOR.match(line) for line in lines)


# A horizontal rule: the input box is drawn between two of these.
_RULE = re.compile(r"^\s*[─━═╌-]{10,}\s*$")
# The empty input box ("❯" alone) and an assistant bullet: neither is ever drawn
# *below* a live choice list, because a modal replaces the input area.
_BARE_PROMPT = re.compile(r"^\s*[❯>]\s*$")
_BULLET = re.compile(r"^[●⏺]\s")  # column 0: the agents list indents its own ●


def is_rule(line: str) -> bool:
    return bool(_RULE.match(line))


def _has_choice_box(lines: Sequence[str]) -> bool:
    cursor_at = -1
    numbers: set[str] = set()
    for i, line in enumerate(lines):
        if _CHOICE_CURSOR.match(line):
            cursor_at = i
        m = _CHOICE_OPTION.match(line)
        if m:
            numbers.add(m.group(1))
    if cursor_at < 0 or len(numbers) < _MIN_CHOICES:
        return False
    return not _is_user_text(lines, cursor_at)


def _is_user_text(lines: Sequence[str], cursor_at: int) -> bool:
    """True when a "❯ 1. …" list is the user's own words, not a choice box.

    Two shapes, both from real panes: a submitted message is echoed as
    "❯ 1. fix …" and Claude's answer plus a fresh input box follow it; a draft
    still in the input box sits directly under the box's top rule. A live modal
    replaces the input area, so neither can happen under a real prompt.
    """
    if cursor_at > 0 and is_rule(lines[cursor_at - 1]):
        return True
    return any(_BARE_PROMPT.match(line) or _BULLET.match(line)
               for line in lines[cursor_at + 1:])


def activity_of(name: str, run: Runner = _run) -> str:
    """Current state of one session. Never raises; UNKNOWN if tmux says nothing."""
    return classify_pane(capture_pane(name, run=run))


def annotate(sessions: Iterable[TmuxSession], run: Runner = _run) -> list[TmuxSession]:
    """Return copies of `sessions` with `.activity` filled in.

    Kept out of `list_sessions` on purpose: listing is one cheap tmux call that
    several callers make, while this is one call *per session*. Callers that
    want the states ask for them.
    """
    return [dataclasses.replace(s, activity=activity_of(s.name, run=run))
            for s in sessions]


def counts(sessions: Iterable[TmuxSession]) -> dict[str, int]:
    """{'running': 2, 'waiting': 1, 'idle': 1, 'unknown': 0} over a session list."""
    tally = {RUNNING: 0, WAITING: 0, IDLE: 0, UNKNOWN: 0}
    for s in sessions:
        state = getattr(s, "activity", UNKNOWN) or UNKNOWN
        tally[state] = tally.get(state, 0) + 1
    return tally
