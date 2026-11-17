"""Presentation constants and pure label helpers shared by the tray
(`tray/barspec.py`), the session services and the dashboard's pages / widgets
(`ui/`).

Kept GTK-free so every importer avoids a circular dependency and so the helpers
stay unit-testable without a display server.
"""
from __future__ import annotations

import time

# Emoji + display name per metric category (section headers of the v1 menu and
# window; kept for themes and tests that still name them).
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


# ───────────────────────── v2 helpers (GTK-free copy shared by every surface) ─────────────────────────

# Glyphs for the panel-owned phases, alongside ACTIVITY_STYLE's activity states.
PHASE_STYLE: dict[str, tuple[str, str, str]] = {
    "journaling": ("⏳", "#7d3c98", "Journaling, then closing"),
    "launching": ("◌", "#7f8c8d", "Starting"),
}


def session_title(s) -> str:
    """What to call a session anywhere (SP-1405): Claude's title, else the project.

    Never empty and never "Claude Code": `tmux.clean_title` already turns
    placeholders into "", so the project (then the tmux name) stands in.
    """
    title = (getattr(s, "title", "") or "").strip()
    return title or getattr(s, "project", "") or getattr(s, "name", "") or "session"


def format_minutes(seconds: float) -> str:
    """120 -> '2 min', 90 -> '90 s' (for "Open under 2 min, so no journal.")."""
    seconds = int(seconds or 0)
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def format_waiting(since: float | None, now: float | None = None) -> str:
    """'4m waiting', 'just now' style, for Board cards and Needs-you rows."""
    if not since:
        return ""
    now = time.time() if now is None else now
    secs = max(0, int(now - since))
    if secs < 60:
        return f"{secs}s waiting"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m waiting"
    return f"{mins // 60}h{mins % 60:02d}m waiting"


def option_button_label(index: int, option: str) -> str:
    """'2 · Yes' for 'Yes, allow all edits this session' (labels cut at the first
    comma). One option alone; `option_labels` keeps a whole prompt's distinct."""
    return f"{index + 1} · {option.split(',')[0].strip()}"


OPTION_LABEL_MAX = 22
OPTION_CLAUSE_WORDS = 3
_LEADING_FILLER = ("yes", "and", "no", "but", "or", "then")


def _cap(text: str, limit: int = OPTION_LABEL_MAX) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rstrip(" ,;:-")
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space].rstrip(" ,;:-")
    return cut + "…"


def _clause_after_comma(option: str) -> str:
    """'Yes, allow all edits this session' -> 'Allow all edits'."""
    _, _, rest = option.partition(",")
    words = rest.replace(",", " ").split()
    while words and words[0].lower().strip(".,;:") in _LEADING_FILLER:
        words.pop(0)
    clause = " ".join(words[:OPTION_CLAUSE_WORDS])
    return clause[:1].upper() + clause[1:]


def option_labels(options) -> list[str]:
    """Short, *distinct* answer-button labels for a prompt's options.

    Each option is cut at its first comma ('No, tell Claude…' -> 'No') unless that
    repeats an earlier label; then the clause after the comma stands in, minus a
    leading 'yes'/'and', capitalised and trimmed to three words
    ('Yes, allow all edits this session' -> 'Allow all edits'). Labels are capped
    at ~22 chars with an ellipsis. The full option text belongs in the tooltip.
    """
    out: list[str] = []
    seen: set[str] = set()
    for i, option in enumerate(options or ()):
        option = " ".join(str(option or "").split())
        candidates = [option.split(",")[0].strip()]
        if "," in option:
            candidates.append(_clause_after_comma(option))
        candidates.append(option)
        label = ""
        for cand in candidates:
            cand = _cap(cand)
            if cand and cand.lower() not in seen:
                label = cand
                break
        if not label:
            label = _cap(f"{candidates[0] or 'Option'} ({i + 1})")
        seen.add(label.lower())
        out.append(label)
    return out


def journal_step_text(step: int, project: str, *, headless: bool = False,
                      monthly_log: str | None = None) -> str:
    """One of state.JOURNAL_STEPS, filled in for this session."""
    from smartpanel.state.model import JOURNAL_STEPS
    step = max(0, min(step, len(JOURNAL_STEPS) - 1))
    if step == 1 and headless:
        return "Resume the session headlessly"
    if step == 2 and monthly_log:
        parts = monthly_log.rstrip("/").split("/")
        short = "/".join(parts[-2:]) if len(parts) >= 2 else monthly_log
        return f"Skill appends to {short}"
    return JOURNAL_STEPS[step].format(project=project)


def focus_message(result: str, title: str, reattach: bool = True) -> tuple[str, bool, dict | None]:
    """(toast text, is_error, action) for a focus attempt (SP-1702)."""
    if result == "raised":
        return f"Raised the terminal for “{title}”", False, None
    if result == "reattached":
        return (f"Re-attached “{title}” in a new terminal. The old window closed.",
                False, None)
    settings = {"label": "Settings", "page": "settings"}
    if reattach:
        return (f"Couldn’t open a terminal for “{title}”. Install it, or set "
                "SMARTPANEL_TERMINAL.", True, settings)
    return "Couldn’t raise the terminal. Turn on re-attach in Settings.", True, settings


def terminal_display_name(term: str) -> str:
    """'terminator' -> 'Terminator' for user-facing copy."""
    base = (term or "terminal").rsplit("/", 1)[-1]
    return base[:1].upper() + base[1:]


def home_path(path: str) -> str:
    """'/home/user/dev/omdev/ledgerly' -> '~/dev/omdev/ledgerly'."""
    import os
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path
