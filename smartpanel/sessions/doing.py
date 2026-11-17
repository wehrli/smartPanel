"""What a RUNNING session is doing, as one short line: "Bash · npm test".

Read off the same pane capture `activity.classify_pane` uses, so the refresh
captures each pane once and hands the text to both. Pure and fixture-tested
(`tests/fixtures/panes/`).

The TUI draws each tool call as a bullet header, `● Bash(npm test)` or
`● Read(web/app/page.tsx)`. Newer builds also draw a Bash call as a prose
description with the command under it (`● Capturing the pane` /
`⎿  $ tmux capture-pane …`). Once the main turn is over but sub-agents are not,
it prints `✻ Waiting for 2 background agents to finish` instead.

The rule that keeps this honest: **only the latest bullet counts.** If Claude
is generating prose after a tool finished, the last bullet is prose, and the
answer is None — never the stale tool from three bullets up. None is also the
answer whenever nothing matches; the UI shows the state word instead of a guess.
"""
from __future__ import annotations

import re

from smartpanel.sessions.activity import TAIL_LINES, is_rule

MAX_DETAIL_CHARS = 60

_AGENTS = re.compile(r"waiting for (\d+) background agents?", re.IGNORECASE)
_BULLET = re.compile(r"^[●⏺]\s+(.*\S)\s*$")
# `Tool(args)`: one word (MCP tools: "server - tool (MCP)"), then the argument,
# whose closing paren may have wrapped onto the next row.
_TOOL = re.compile(
    r"^([A-Z][\w.:-]*(?: - [\w.:-]+)?|[\w.:-]+ - [\w.:-]+)(?: \(MCP\))?\((.*?)\)?$")
_DOLLAR = re.compile(r"^\s*⎿\s+\$\s+(.*\S)")
_INPUT_BOX = re.compile(r"^\s*[❯>]")


def describe_running(pane: str, tail_lines: int = TAIL_LINES) -> str | None:
    """'Bash · npm test', 'Read · page.tsx', 'Waiting for 2 background agents', or None."""
    if not pane or not pane.strip():
        return None
    lines = _conversation(pane.splitlines()[-tail_lines - 10:])[-tail_lines:]

    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        m = _AGENTS.search(line)
        if m:
            n = int(m.group(1))
            return f"Waiting for {n} background agent{'' if n == 1 else 's'}"
        b = _BULLET.match(line)
        if not b:
            continue
        # The latest bullet decides: a tool header, a described Bash call, or
        # prose (None — Claude is writing, not running a tool).
        t = _TOOL.match(b.group(1))
        if t:
            return _join(t.group(1), t.group(2))
        for below in lines[i + 1:]:
            d = _DOLLAR.match(below)
            if d:
                return _join("Bash", d.group(1))
        return None
    return None


def _conversation(lines: list[str]) -> list[str]:
    """Drop the input box and the status rows under it.

    The box is the last rule followed by a `❯` row; everything from that rule
    down is chrome (mode line, agents list) — the agents list even has its own
    `●` bullets, which must not be read as tool calls.
    """
    for i in range(len(lines) - 2, -1, -1):
        if is_rule(lines[i]) and _INPUT_BOX.match(lines[i + 1]):
            return lines[:i]
    return lines


def _join(tool: str, detail: str) -> str:
    detail = " ".join(detail.split())
    if len(detail) > MAX_DETAIL_CHARS:
        detail = detail[:MAX_DETAIL_CHARS - 1].rstrip() + "…"
    return f"{tool} · {detail}" if detail else tool
