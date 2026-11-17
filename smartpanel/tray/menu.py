"""Pure bar-menu model: `PanelState` -> rows -> a `dbusmenu.MenuNode` tree.

Mirrors the prototype's `menuHTML` (docs/design/prototype.html):

    SYSTEM              CPU / Memory / Swap (high|critical|none) / GPU (only if present)
    CLAUDE · TODAY      Spent, Burn rate | Reading transcripts… n/total | No transcripts yet
    SESSIONS · 4 · 1 waiting
                        first 4 in display order 'glyph title  age' (click focuses),
                        '+N more…' (sessions page), 'No sessions', 'tmux isn't installed'
    New session…, Command palette… <shortcut>, Open dashboard,
    Pin current reading / Resume rotation (only with the ticker on), ✓ Desktop HUD
    Quit smartPanel

SP-2002: the tree has a FIXED set of rows with stable ids (`ROW_KEYS`); rows that
don't apply are hidden rather than removed, so state changes only ever produce
property updates and an open menu never gets its layout re-sent.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from smartpanel.state.model import LEVEL_CRITICAL, LEVEL_WARNING, PanelState
from smartpanel.tray import dbusmenu
from smartpanel.tray.barspec import (
    IDLE, JOURNALING, LAUNCHING, RUNNING, WAITING, counts, money, pct, rnd, session_state,
)

SESSION_ROWS = 4
TITLE_MAX = 40

GLYPHS = {WAITING: "❓", RUNNING: "▶", IDLE: "·", LAUNCHING: "◌", JOURNALING: "⏳"}

# Every row the menu can ever show, in order. Index + 1 is the dbusmenu id.
ROW_KEYS: tuple[str, ...] = (
    "sys.header", "sys.cpu", "sys.memory", "sys.swap", "sys.gpu", "sys.vram",
    "sep.system",
    "claude.header", "claude.loading", "claude.first_run", "claude.spent", "claude.burn",
    "sep.claude",
    "sessions.header",
    *(f"sessions.{i}" for i in range(SESSION_ROWS)),
    "sessions.more", "sessions.none", "sessions.tmux",
    "sep.sessions",
    "act.new", "act.palette", "act.dashboard", "act.pin", "act.hud",
    "sep.actions",
    "act.quit",
)
ROW_IDS: dict[str, int] = {k: i + 1 for i, k in enumerate(ROW_KEYS)}
KEY_BY_ID: dict[int, str] = {v: k for k, v in ROW_IDS.items()}


@dataclass(frozen=True)
class MenuRow:
    key: str
    label: str = ""
    enabled: bool = True
    visible: bool = True
    separator: bool = False
    toggle: bool | None = None          # None = plain item, else a checkmark item
    action: tuple | None = None         # see `invoke`

    @property
    def id(self) -> int:
        return ROW_IDS[self.key]


# ───────────────────────── formatting ─────────────────────────

def fmt_age(created: float, now: float) -> str:
    """Prototype fmtAge: 'now', '38m', '1h02'."""
    if not created:
        return ""
    m = rnd(max(0.0, now - created) / 60)
    if m < 1:
        return "now"
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}"


def fmt_shortcut(accel: str | None) -> str:
    """GTK accelerator '<Ctrl><Alt>space' -> 'Ctrl+Alt+Space'."""
    if not accel:
        return ""
    mods = re.findall(r"<([^>]+)>", accel)
    key = re.sub(r"<[^>]+>", "", accel).strip()
    names = {"primary": "Ctrl", "control": "Ctrl", "ctrl": "Ctrl", "alt": "Alt",
             "shift": "Shift", "super": "Super", "meta": "Meta"}
    parts = [names.get(m.lower(), m.capitalize()) for m in mods]
    if key:
        parts.append(key if len(key) == 1 and not key.isalpha() else key[:1].upper() + key[1:])
    return "+".join(parts)


def truncate(text: str, n: int = TITLE_MAX) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _setting(state: PanelState, config: Any, key: str, default: Any) -> Any:
    vals = state.settings.values or {}
    if key in vals and vals[key] is not None:
        return vals[key]
    if config is not None and hasattr(config, "get"):
        try:
            v = config.get(key)
        except Exception:
            v = None
        if v is not None:
            return v
    return default


# ───────────────────────── rows ─────────────────────────

def swap_row_label(state: PanelState) -> str:
    """SP-0105: 'Swap 12%', 'Swap 66% high', 'Swap 88% critical', 'Swap none'."""
    sw = state.system.swap
    if sw.percent is None:
        return "Swap none"
    suffix = {LEVEL_WARNING: " high", LEVEL_CRITICAL: " critical"}.get(sw.level, "")
    if sw.level > LEVEL_CRITICAL:
        suffix = " critical"
    return f"Swap {rnd(sw.percent)}%{suffix}"


def sessions_header(state: PanelState) -> str:
    if not state.sessions.tmux_ok:
        return "Sessions · tmux missing"
    n = len(state.sessions.items)
    w = counts(state)[WAITING]
    return f"Sessions · {n}" + (f" · {w} waiting" if w else "")


def session_row_label(s, now: float) -> str:
    st = session_state(s)
    age = "starting…" if st == LAUNCHING else fmt_age(s.created, now)
    label = f"{GLYPHS[st]} {truncate(s.display_title)}"
    return f"{label}  {age}" if age else label


def build_rows(state: PanelState, config: Any = None, now: float | None = None) -> list[MenuRow]:
    now = time.time() if now is None else now
    sysm, sp, app, sess = state.system, state.spend, state.app, state.sessions
    rows: list[MenuRow] = []
    add = rows.append

    # System
    add(MenuRow("sys.header", "System", enabled=False))
    add(MenuRow("sys.cpu", f"CPU {pct(sysm.cpu.percent)}", action=("page", "system")))
    add(MenuRow("sys.memory", f"Memory {pct(sysm.memory.percent)}", action=("page", "system")))
    add(MenuRow("sys.swap", swap_row_label(state), action=("page", "system")))
    gpu = sysm.gpu
    gpu_label = f"GPU {pct(gpu.percent)}"
    if gpu.temp_c is not None:
        gpu_label += f" · {rnd(gpu.temp_c)} °C"
    add(MenuRow("sys.gpu", gpu_label, visible=gpu.available, action=("page", "system")))
    vram_ok = gpu.available and gpu.vram_used_gb is not None and gpu.vram_total_gb
    add(MenuRow("sys.vram",
                f"VRAM {gpu.vram_used_gb:.1f} of {gpu.vram_total_gb:.0f} GB" if vram_ok else "VRAM",
                visible=bool(vram_ok), action=("page", "system")))
    add(MenuRow("sep.system", separator=True))

    # Claude · today
    loading = app.loading is not None
    first = app.first_run and not loading
    normal = not loading and not first
    add(MenuRow("claude.header", "Claude · today", enabled=False))
    ld = app.loading
    add(MenuRow("claude.loading",
                f"Reading transcripts… {ld.done}/{ld.total}" if ld else "Reading transcripts…",
                enabled=False, visible=loading))
    add(MenuRow("claude.first_run", "No transcripts yet", enabled=False, visible=first))
    add(MenuRow("claude.spent",
                "Spent " + (money(sp.cost_today) if sp.cost_today is not None else "—"),
                visible=normal, action=("page", "spend")))
    add(MenuRow("claude.burn",
                "Burn rate " + (f"{money(sp.burn_per_hour)}/h" if sp.burn_per_hour is not None else "—"),
                visible=normal, action=("page", "spend")))
    add(MenuRow("sep.claude", separator=True))

    # Sessions
    add(MenuRow("sessions.header", sessions_header(state), enabled=False))
    items = sess.items if sess.tmux_ok else []
    for i in range(SESSION_ROWS):
        key = f"sessions.{i}"
        if i < len(items):
            s = items[i]
            st = session_state(s)
            add(MenuRow(key, session_row_label(s, now),
                        enabled=st not in (LAUNCHING, JOURNALING),
                        action=("focus", s.name)))
        else:
            add(MenuRow(key, "", visible=False))
    extra = len(items) - SESSION_ROWS
    add(MenuRow("sessions.more", f"+{extra} more…" if extra > 0 else "",
                visible=extra > 0, action=("page", "sessions")))
    add(MenuRow("sessions.none", "No sessions", enabled=False,
                visible=sess.tmux_ok and not items))
    add(MenuRow("sessions.tmux", "tmux isn't installed — how to fix…",
                visible=not sess.tmux_ok, action=("page", "sessions")))
    add(MenuRow("sep.sessions", separator=True))

    # Actions
    add(MenuRow("act.new", "New session…", enabled=sess.tmux_ok, action=("new",)))
    shortcut = fmt_shortcut(_setting(state, config, "palette.shortcut", "<Ctrl><Alt>space"))
    add(MenuRow("act.palette", "Command palette…" + (f"   {shortcut}" if shortcut else ""),
                action=("palette",)))
    add(MenuRow("act.dashboard", "Open dashboard", action=("dashboard",)))
    ticker_on = bool(_setting(state, config, "bar.ticker", False))
    pinned = bool(app.ticker_pinned)
    add(MenuRow("act.pin", "Resume rotation" if pinned else "Pin current reading",
                visible=ticker_on, action=("pin", not pinned)))
    hud_on = bool(_setting(state, config, "hud.enabled", False))
    add(MenuRow("act.hud", "Desktop HUD", toggle=hud_on, action=("hud", not hud_on)))
    add(MenuRow("sep.actions", separator=True))
    add(MenuRow("act.quit", "Quit smartPanel", action=("quit",)))

    assert [r.key for r in rows] == list(ROW_KEYS), "menu rows must match ROW_KEYS"
    return rows


# ───────────────────────── tree + dispatch ─────────────────────────

def to_node(rows: list[MenuRow]) -> dbusmenu.MenuNode:
    children = []
    for r in rows:
        if r.separator:
            children.append(dbusmenu.separator(r.id, r.visible))
            continue
        props: dict[str, Any] = {
            "label": dbusmenu.escape_label(r.label),
            "enabled": r.enabled,
            "visible": r.visible,
        }
        if r.toggle is not None:
            props["toggle-type"] = "checkmark"
            props["toggle-state"] = 1 if r.toggle else 0
        children.append(dbusmenu.MenuNode(r.id, props))
    return dbusmenu.root(children)


def build_menu(state: PanelState, config: Any = None, now: float | None = None) -> dbusmenu.MenuNode:
    return to_node(build_rows(state, config, now))


def invoke(actions: Any, action: tuple | None) -> bool:
    """Run a row's action on an `Actions` implementation. True if something ran."""
    if not action:
        return False
    verb, *args = action
    if verb == "focus":
        actions.focus_session(args[0])
    elif verb == "page":
        actions.open_dashboard(args[0])
    elif verb == "new":
        actions.request_new_session()
    elif verb == "palette":
        actions.open_palette()
    elif verb == "dashboard":
        actions.open_dashboard("")
    elif verb == "pin":
        actions.pin_ticker(bool(args[0]))
    elif verb == "hud":
        actions.set_hud(bool(args[0]))
    elif verb == "quit":
        actions.quit()
    else:
        return False
    return True
