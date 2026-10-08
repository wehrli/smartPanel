"""Presentation constants and pure label helpers shared by the tray menu
(`indicator.py`) and the centered dashboard window (`dashboard.py`).

Kept GTK-free so both importers avoid a circular dependency and so the helpers
stay unit-testable without a display server.
"""
from __future__ import annotations

import time

# Emoji + display name per metric category, used as section headers in both
# the tray menu and the dashboard window.
SECTION_HEADERS: dict[str, str] = {
    "CLAUDE": "💰  Claude · today",
    "CPU": "🖥   CPU",
    "MEMORY": "🧠  Memory",
    "SWAP": "🔃  Swap",
    "DISK": "💾  Disk",
    "NET": "🌐  Network",
    "GPU": "🎮  GPU",
    "BATTERY": "🔋  Battery",
    "SESSIONS": "🧩  Sessions",
}

# Metrics whose value is a " · "-joined string expanding into multiple rows.
MULTI_ROW_METRICS = {"PER_PROJECT", "BY_MODEL", "PRAISE_SUMMARY", "TOP_PRAISE"}


# Longest conversation title shown before it is cut with "…".
SESSION_TITLE_MAX_CHARS = 44

# Glyph, Pango colour and tooltip per activity state (see sessions/activity.py).
# The glyph leads the row because "which session needs me?" is the question you
# scan for, and the left edge is the only column that never moves.
ACTIVITY_STYLE: dict[str, tuple[str, str, str]] = {
    "waiting": ("❓", "#b9770e", "Waiting for your answer"),
    "running": ("▶", "#2874a6", "Working"),
    "idle":    ("·", "#7f8c8d", "Idle — ready for a prompt"),
    "unknown": (" ", "#7f8c8d", "State unknown"),
}


def activity_style(state: str | None) -> tuple[str, str, str]:
    """(glyph, colour, tooltip) for an activity state, falling back to unknown."""
    return ACTIVITY_STYLE.get(state or "unknown", ACTIVITY_STYLE["unknown"])


def sessions_status(sessions) -> str:
    """Compact top-bar summary: '🧩4', or '🧩4 ❓1 ▶2' once something is happening.

    Zero counts are omitted — the panel truncates from the right (see the
    top-bar gotcha in CLAUDE.md), so the badge only spends width when it has
    something to say.
    """
    sessions = list(sessions)
    parts = [f"🧩{len(sessions)}"]
    for state in ("waiting", "running"):
        n = sum(1 for s in sessions if getattr(s, "activity", "") == state)
        if n:
            parts.append(f"{ACTIVITY_STYLE[state][0]}{n}")
    return " ".join(parts)


def session_label(s, now: float | None = None) -> str:
    """Human label for one tmux session: '▶ Fix login bug — myproject ●   (1h01m)'.

    `s` is duck-typed: needs `.project`/`.name`, `.attached`, `.created`, and
    optionally `.title` (Claude's conversation title; without one the label is
    just the project) and `.activity`. The ● marks an attached session; the
    leading glyph is what it is currently doing. The caller adds its ✕.
    """
    now = time.time() if now is None else now
    head = session_heading(s)
    glyph = activity_style(getattr(s, "activity", None))[0]
    flag = " ●" if getattr(s, "attached", False) else ""
    age = format_age(getattr(s, "created", 0), now)
    age_part = f"   ({age})" if age else ""
    return f"{glyph} {head}{flag}{age_part}"


def session_heading(s) -> str:
    """'Fix login bug — myproject': the session label without flag or age.

    Also pushed verbatim as the session's terminal window title, so the
    terminal and the dashboard always read the same.
    """
    project = getattr(s, "project", getattr(s, "name", "?"))
    title = getattr(s, "title", "") or ""
    if len(title) > SESSION_TITLE_MAX_CHARS:
        title = title[:SESSION_TITLE_MAX_CHARS - 1].rstrip() + "…"
    return f"{title} — {project}" if title else project


def format_age(created_epoch: int, now: float) -> str:
    if not created_epoch or created_epoch <= 0:
        return ""
    secs = int(now - created_epoch)
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    rem = mins % 60
    if hours < 24:
        return f"{hours}h{rem:02d}m" if rem else f"{hours}h"
    days = hours // 24
    return f"{days}d"
