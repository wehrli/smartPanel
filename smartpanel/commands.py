"""Command palette registry and fuzzy matching (B5: SP-2201, SP-2202, SP-0111).

GTK-free and pure: `build_commands(state, config)` turns one `PanelState` into the
palette's action list, `match(query, title)` scores one title, `filter_commands`
ranks the list for a query, and `status_line(state)` is the palette footer.

Order mirrors the prototype's `paletteItems`:

    ❓ Answer · <title>             waiting sessions only, first, because they need you
    ▶ Focus · <title>               every session that isn't starting or journaling
    ✎ Journal & close · <title>     same set -> actions.request_close (the UI confirms)
    ＋ New session…
    ✕ Close all sessions…           only when there is something to close
    → Go to <page>                  every dashboard page
    ◐ Show / Hide desktop HUD
    ↻ Start / Stop rotating the top bar
    📄 Open JOURNAL/2026-11.md       when the monthly log is known

Starting (launching) and journaling sessions get no actions: there is nothing to
focus yet, and a second close would race the journal already running.

Matching is the query's letters in order, case-insensitive. A substring match ranks
above a scattered one, then the registry order breaks ties, so typing never
reshuffles rows that match equally well.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from smartpanel.state.model import PAGES, WAITING, PanelState
from smartpanel.tray.barspec import (JOURNALING, LAUNCHING, NO_VALUE, money, pct,
                                     session_state)

MAX_RESULTS = 9

PAGE_TITLES = {"overview": "Overview", "sessions": "Sessions", "spend": "Spend",
               "system": "System", "journal": "Journal", "settings": "Settings"}

# Leading glyph per effective session state (prototype META).
STATE_GLYPHS = {"waiting": "❓", "running": "▶", "idle": "·",
                LAUNCHING: "◌", JOURNALING: "⏳"}

GLYPH_ANSWER = "❓"
GLYPH_JOURNAL = "✎"
GLYPH_NEW = "＋"
GLYPH_CLOSE_ALL = "✕"
GLYPH_PAGE = "→"
GLYPH_HUD = "◐"
GLYPH_TICKER = "↻"
GLYPH_LOG = "📄"

KEYS_HINT = "↑↓ move · ⏎ run"

Runner = Callable[[Any], None]


@dataclass(frozen=True)
class Command:
    id: str
    title: str
    subtitle: str = ""
    glyph: str = ""
    run: Runner = lambda _actions: None     # noqa: E731 — called with the Actions object


# ───────────────────────── registry (SP-2201) ─────────────────────────

def _setting(state: PanelState, config: Any, key: str, default: Any) -> Any:
    """Effective value: the config if it answers, else the store mirror, else default."""
    if config is not None:
        try:
            getter = config.get if hasattr(config, "get") else None
            v = getter(key) if getter else None
        except Exception:  # noqa: BLE001 — a broken config must never break the palette
            v = None
        if v is not None:
            return v
    values = getattr(state.settings, "values", None) or {}
    v = values.get(key) if isinstance(values, Mapping) else None
    return default if v is None else v


def log_display_name(path: str | None) -> str | None:
    """'~/dev/omdev/JOURNAL/2026-11.md' -> 'JOURNAL/2026-11.md'."""
    if not path:
        return None
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    if not parts:
        return None
    return "/".join(parts[-2:])


def _open_log(path: str) -> Runner:
    def run(actions: Any) -> None:
        opener = getattr(actions, "open_path", None)
        if callable(opener):
            opener(path)
            return
        # No Actions verb for "open a file" yet (see the WP report): hand the file to
        # the desktop's default editor, and fall back to the Journal page.
        if not _launch_default(path):
            actions.open_dashboard("journal")
    return run


def _launch_default(path: str) -> bool:
    import os
    full = os.path.expanduser(path)
    if not os.path.exists(full):
        return False
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        return bool(Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(full).get_uri(),
                                                       None))
    except Exception:  # noqa: BLE001
        return False


def build_commands(state: PanelState, config: Any = None) -> list[Command]:
    """Every action for one state, in prototype order (see the module docstring)."""
    out: list[Command] = []
    items = list(state.sessions.items)
    actionable = [s for s in items if session_state(s) not in (LAUNCHING, JOURNALING)]

    for s in items:
        if session_state(s) == WAITING:
            out.append(Command(f"answer:{s.name}", f"Answer · {s.display_title}", s.project,
                               GLYPH_ANSWER,
                               lambda a, n=s.name: a.open_dashboard("sessions")))
    for s in actionable:
        out.append(Command(f"focus:{s.name}", f"Focus · {s.display_title}", s.project,
                           STATE_GLYPHS.get(session_state(s), "·"),
                           lambda a, n=s.name: a.focus_session(n)))
    for s in actionable:
        out.append(Command(f"close:{s.name}", f"Journal & close · {s.display_title}",
                           s.project, GLYPH_JOURNAL,
                           lambda a, n=s.name: a.request_close(n)))

    out.append(Command("new-session", "New session…", "", GLYPH_NEW,
                       lambda a: a.request_new_session()))
    if items:
        out.append(Command("close-all", "Close all sessions…", "", GLYPH_CLOSE_ALL,
                           lambda a: a.request_close_all()))
    for page in PAGES:
        out.append(Command(f"page:{page}", f"Go to {PAGE_TITLES.get(page, page.title())}",
                           "", GLYPH_PAGE, lambda a, p=page: a.open_dashboard(p)))

    hud = bool(_setting(state, config, "hud.enabled", False))
    out.append(Command("hud", f"{'Hide' if hud else 'Show'} desktop HUD", "", GLYPH_HUD,
                       lambda a, on=not hud: a.set_hud(on)))
    ticker = bool(_setting(state, config, "bar.ticker", False))
    out.append(Command("ticker", f"{'Stop' if ticker else 'Start'} rotating the top bar", "",
                       GLYPH_TICKER,
                       lambda a, on=not ticker: a.set_setting("bar.ticker", on)))

    log_name = log_display_name(state.journal.monthly_log_path)
    if log_name:
        out.append(Command("journal-log", f"Open {log_name}", "", GLYPH_LOG,
                           _open_log(str(state.journal.monthly_log_path))))
    return out


# ───────────────────────── matching (SP-2202) ─────────────────────────

SCORE_SUBSTRING = 2
SCORE_SCATTERED = 1


def _word_start(text: str, i: int) -> bool:
    return i == 0 or not text[i - 1].isalnum()


def _merge(indices: Iterable[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for i in indices:
        if ranges and ranges[-1][1] == i:
            ranges[-1] = (ranges[-1][0], i + 1)
        else:
            ranges.append((i, i + 1))
    return ranges


def _is_subsequence(q: str, t: str, start: int) -> bool:
    it = iter(t[start:])
    return all(ch in it for ch in q)


def match(query: str, title: str) -> tuple[int, list[tuple[int, int]]] | None:
    """Score `title` for `query`: (score, [(start, end), ...]) or None if no match.

    An empty query matches everything with score 0 and no ranges. A substring match
    scores `SCORE_SUBSTRING` with one range; otherwise the query's letters in order
    score `SCORE_SCATTERED`, preferring letters that start a word ('fsah' lights up
    the F, s, a, h of "Fix swap alert hysteresis"). Ranges index into `title`.
    """
    q = (query or "").strip().lower()
    if not q:
        return 0, []
    t = title.lower()
    if len(t) != len(title):        # .lower() changed length: match on the original
        t = title
    i = t.find(q)
    if i >= 0:
        return SCORE_SUBSTRING, [(i, i + len(q))]
    picked: list[int] = []
    pos = 0
    for k, ch in enumerate(q):
        rest = q[k + 1:]
        first = t.find(ch, pos)
        if first < 0:
            return None
        chosen = first
        j = first
        while j >= 0:                # the earliest word-start that keeps the rest matchable
            if _word_start(t, j) and _is_subsequence(rest, t, j + 1):
                chosen = j
                break
            j = t.find(ch, j + 1)
        picked.append(chosen)
        pos = chosen + 1
    return SCORE_SCATTERED, _merge(picked)


def filter_commands(commands: list[Command], query: str, limit: int | None = MAX_RESULTS
                    ) -> list[tuple[Command, list[tuple[int, int]]]]:
    """Matching commands with their highlight ranges, best first, registry order on ties."""
    scored = []
    for idx, cmd in enumerate(commands):
        m = match(query, cmd.title)
        if m is not None:
            scored.append((-m[0], idx, cmd, m[1]))
    scored.sort(key=lambda x: (x[0], x[1]))
    rows = [(cmd, ranges) for _s, _i, cmd, ranges in scored]
    return rows if limit is None else rows[:limit]


# ───────────────────────── footer (SP-0111) ─────────────────────────

def status_parts(state: PanelState) -> list[str]:
    """['Swap 12%', '4 sessions · 1 waiting', '$23.84 today'] for the palette footer."""
    parts = [f"Swap {pct(state.system.swap.percent)}"]
    sess = state.sessions
    if not sess.tmux_ok:
        parts.append("tmux not found")
    else:
        n = len(sess.items)
        text = f"{n} session{'' if n == 1 else 's'}"
        waiting = sum(1 for s in sess.items if session_state(s) == WAITING)
        if waiting:
            text += f" · {waiting} waiting"
        parts.append(text)
    spend = state.spend
    if state.app.first_run:
        parts.append("no spend yet")
    elif spend.cost_today is None:
        parts.append("reading transcripts…" if state.app.loading is not None
                     else f"{NO_VALUE} today")
    else:
        parts.append(f"{money(spend.cost_today)} today")
    return parts


def status_line(state: PanelState, *, with_keys: bool = True) -> str:
    """'Swap 12% · 4 sessions · 1 waiting · $23.84 today · ↑↓ move · ⏎ run'."""
    parts = status_parts(state)
    if with_keys:
        parts.append(KEYS_HINT)
    return " · ".join(parts)
