"""The question a WAITING session is asking, and answering it safely.

`parse_prompt(pane)` turns a captured pane into a `state.Prompt` (question,
options, cursor, kind, fingerprint). It is built on the same *structural*
markers as `activity.classify_pane` and only runs once that says WAITING, so
every false-positive guard there (prose questions, a user's own "1. … 2. …",
quoted footers, stale scrollback) applies here for free. Phrases are never
markers: the question text is whatever sits above the options.

`answer(session, option, fingerprint)` types into someone's live session, so it
is defensive by construction:

1. re-capture the pane (the panel's copy may be two seconds old);
2. refuse unless it is still WAITING **and** the prompt's fingerprint is the
   one the user was looking at — otherwise the keystroke could land on a
   different question that appeared in the meantime;
3. send the option's digit (one key, no Enter: Claude Code acts on the digit,
   and a stray Enter would hit whatever comes next), or arrow keys + Enter for
   options without numbers (the folder-trust prompt);
4. confirm the prompt went away within `confirm_s`.

Step 4 sleeps, so `answer` must run on a worker thread (the controller does).
"""
from __future__ import annotations

import enum
import hashlib
import re
import time
from dataclasses import dataclass

from smartpanel.sessions import tmux
from smartpanel.sessions.activity import (
    _CHOICE_CURSOR,
    _CHOICE_OPTION,
    _CONFIRM_FOOTER,
    _OPTION_CURSOR,
    TAIL_LINES,
    WAITING,
    classify_pane,
    is_rule,
)
from smartpanel.sessions.tmux import Runner, _run
from smartpanel.state.model import Prompt

KIND_PERMISSION = "permission"
KIND_QUESTION = "question"

# Box-drawing borders around a prompt, stripped from both ends of a row.
_BORDER = "│|╎┃ \t"
_BOX_ONLY = re.compile(r"^[\s│|╎┃╭╮╰╯─━═╌┌┐└┘├┤]*$")
_SELECT_FOOTER = re.compile(r"^[\s│|╎┃]*enter to select\b")
# The AskUserQuestion tab strip: "←  ☐ Cache scope  ☐ Expiry  ✔ Submit  →".
_TAB_STRIP = re.compile(r"[☐☒✔]\s+\S")
# Key hints Claude Code appends to an option: "(esc)", "(shift+tab)".
_KEY_HINT = re.compile(r"\s*\((?:esc|tab|shift\+tab|ctrl\+\w+)\)\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"^(?:[❯>]\s*)?\d+\.\s+")
_CURSOR_MARK = re.compile(r"^[❯>]\s*")
# AskUserQuestion always ends with free-text escape hatches. The panel can't
# type free text, so they are not offered; they are last, so dropping them
# never shifts the digit of a real option.
_META_OPTIONS = {"type something", "chat about this"}
# How far above the options a question can sit (the trust prompt puts a
# "Security guide" link between the two).
_QUESTION_REACH = 14


@dataclass(frozen=True)
class _Parsed:
    prompt: Prompt
    numbered: bool


def parse_prompt(pane: str, tail_lines: int = TAIL_LINES) -> Prompt | None:
    """The prompt on screen, or None if the pane isn't WAITING or can't be read."""
    parsed = _parse(pane, tail_lines)
    return parsed.prompt if parsed else None


def fingerprint(question: str, options: list[str], kind: str = KIND_PERMISSION) -> str:
    """Stable id of a prompt: same question and options => same fingerprint.

    The cursor is deliberately left out: moving it with the arrow keys doesn't
    make it a different question.
    """
    blob = "\x1e".join([kind, question, "\x1f".join(options)])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _parse(pane: str, tail_lines: int = TAIL_LINES) -> _Parsed | None:
    if not pane or classify_pane(pane, tail_lines) != WAITING:
        return None
    lines = pane.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    lines = lines[-tail_lines:]
    kind = (KIND_QUESTION
            if any(_SELECT_FOOTER.match(l.lower()) for l in lines)
            or any(_TAB_STRIP.search(l) for l in lines)
            else KIND_PERMISSION)

    block = _numbered_block(lines)
    numbered = block is not None
    if block is None:
        block = _unnumbered_block(lines)
    if block is None:
        return None
    indexes, cursor = block

    options = [_option_text(lines[i], numbered) for i in indexes]
    if numbered and kind == KIND_PERMISSION:
        options = [_with_continuation(lines, i, text, indexes)
                   for i, text in zip(indexes, options)]
    if kind == KIND_QUESTION:
        while options and options[-1].rstrip(".").casefold() in _META_OPTIONS:
            options.pop()
    if not options or any(not o for o in options):
        return None
    cursor = min(cursor, len(options) - 1)

    question = _question(lines, indexes[0])
    return _Parsed(Prompt(question=question, options=options, cursor=cursor,
                          kind=kind, fingerprint=fingerprint(question, options, kind)),
                   numbered)


def _numbered_block(lines: list[str]) -> tuple[list[int], int] | None:
    """Row indexes of options 1..n around the cursor, and the cursor's index."""
    found = [(i, int(m.group(1))) for i, l in enumerate(lines)
             if (m := _CHOICE_OPTION.match(l))]
    cursors = [i for i, l in enumerate(lines) if _CHOICE_CURSOR.match(l)]
    if not cursors:
        return None
    at = cursors[-1]
    number = dict(found)[at]
    rows = {at: number}
    # Upwards: number-1 down to 1, nearest first.
    want = number - 1
    for i, n in reversed([f for f in found if f[0] < at]):
        if n == want:
            rows[i] = n
            want -= 1
        if want == 0:
            break
    if want != 0:
        return None
    # Downwards: number+1 onwards, stopping at the modal footer.
    want = number + 1
    for i, n in [f for f in found if f[0] > at]:
        if _CONFIRM_FOOTER.match(lines[i].lower()):
            break
        if n == want:
            rows[i] = n
            want += 1
    ordered = sorted(rows)
    if len(ordered) < 2:
        return None
    return ordered, number - 1


def _unnumbered_block(lines: list[str]) -> tuple[list[int], int] | None:
    """Options without digits: the cursor row and its siblings at its text column."""
    cursors = [i for i, l in enumerate(lines) if _OPTION_CURSOR.match(l)]
    if not cursors:
        return None
    at = cursors[-1]
    col = _text_col(lines[at], cursor_row=True)
    rows = [at]
    i = at - 1
    while i >= 0 and _sibling(lines[i], col):
        rows.insert(0, i)
        i -= 1
    i = at + 1
    while i < len(lines) and _sibling(lines[i], col):
        rows.append(i)
        i += 1
    if len(rows) < 2:
        return None
    return rows, rows.index(at)


def _sibling(line: str, col: int) -> bool:
    body = line.rstrip(_BORDER)
    if not body.strip() or _BOX_ONLY.match(body) or _CONFIRM_FOOTER.match(body.lower()):
        return False
    return _text_col(line, cursor_row=False) == col


def _text_col(line: str, cursor_row: bool) -> int:
    """Column where an option's words start (after border, cursor and digit)."""
    i = 0
    while i < len(line) and line[i] in _BORDER:
        i += 1
    if cursor_row and i < len(line) and line[i] in "❯>":
        i += 1
        while i < len(line) and line[i] == " ":
            i += 1
    return i


def _option_text(line: str, numbered: bool) -> str:
    text = line.strip(_BORDER)
    text = _NUMBER.sub("", text) if numbered else _CURSOR_MARK.sub("", text)
    text = " ".join(text.split())
    return _KEY_HINT.sub("", text).strip()


def _with_continuation(lines: list[str], at: int, text: str, indexes: list[int]) -> str:
    """Append rows a long permission option wrapped onto.

    Only for permission prompts: in a question box the rows under an option are
    its description, not more of its label.
    """
    m = _CHOICE_OPTION.match(lines[at])
    if not m:
        return text
    col = m.end()
    nxt = next((j for j in indexes if j > at), len(lines))
    parts = [text]
    for j in range(at + 1, nxt):
        row = lines[j].rstrip(_BORDER + "─╯╰")
        if not row.strip() or _BOX_ONLY.match(row) or is_rule(row):
            break
        if len(row) - len(row.lstrip(_BORDER)) != col:
            break
        parts.append(" ".join(row.strip(_BORDER).split()))
    return _KEY_HINT.sub("", " ".join(parts)).strip()


def _question(lines: list[str], first_option: int) -> str:
    """The paragraph above the options that asks something.

    Nearest paragraph containing a "?" wins, cut just after its last "?" (the
    trust prompt carries a parenthetical and a second sentence after it).
    Without any "?", the nearest paragraph is the question.
    """
    paragraphs: list[list[str]] = []
    current: list[str] = []
    lo = max(0, first_option - _QUESTION_REACH)
    for i in range(first_option - 1, lo - 1, -1):
        row = lines[i].strip(_BORDER)
        if not row or _BOX_ONLY.match(row) or is_rule(row) or _TAB_STRIP.search(row):
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.insert(0, " ".join(row.split()))
    if current:
        paragraphs.append(current)
    if not paragraphs:
        return ""
    for para in paragraphs:
        text = " ".join(para)
        if "?" in text:
            return text[:text.rindex("?") + 1]
    return " ".join(paragraphs[0])


# ───────────────────────── answering ─────────────────────────

class AnswerResult(str, enum.Enum):
    SENT = "sent"                       # keys went in and the prompt went away
    PROMPT_CHANGED = "prompt_changed"   # a different (or unreadable) prompt is up
    NOT_WAITING = "not_waiting"         # nothing is asking any more
    FAILED = "failed"                   # tmux refused, or the prompt didn't move


CONFIRM_S = 2.0
_CONFIRM_POLL_S = 0.2


def answer(name: str, option: int, expected: str, *, run: Runner = _run,
           sleep=time.sleep, clock=time.monotonic,
           confirm_s: float = CONFIRM_S) -> AnswerResult:
    """Pick `option` (0-based) on session `name`'s prompt, if it is still `expected`.

    Blocks up to `confirm_s` — call it off the main loop. Never raises.
    """
    try:
        pane = tmux.capture_pane(name, run=run)
        if classify_pane(pane) != WAITING:
            return AnswerResult.NOT_WAITING
        parsed = _parse(pane)
        if parsed is None or parsed.prompt.fingerprint != expected:
            return AnswerResult.PROMPT_CHANGED
        prompt = parsed.prompt
        if not 0 <= option < len(prompt.options):
            return AnswerResult.FAILED

        if parsed.numbered:
            ok = tmux.send_text(name, str(option + 1), run=run)
        else:
            delta = option - prompt.cursor
            keys = (["Down"] * delta if delta > 0 else ["Up"] * -delta) + ["Enter"]
            ok = tmux.send_keys(name, keys, run=run)
        if not ok:
            return AnswerResult.FAILED

        deadline = clock() + confirm_s
        while True:
            sleep(_CONFIRM_POLL_S)
            after = _parse(tmux.capture_pane(name, run=run))
            if after is None or after.prompt.fingerprint != expected:
                return AnswerResult.SENT
            if clock() >= deadline:
                return AnswerResult.FAILED
    except Exception:  # noqa: BLE001 — answering must never take the panel down
        return AnswerResult.FAILED
