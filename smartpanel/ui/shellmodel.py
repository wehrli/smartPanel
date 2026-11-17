"""GTK-free presentation logic for the dashboard shell and the widget kit.

Everything the window decides from `PanelState` (nav badges, banner copy, footer
text, page routing, remembered geometry) and every word a widget shows for a
state (activity labels, level chips) lives here, so it is unit-tested without a
display and shared by the window, the pages and the palette.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from smartpanel.state.model import (IDLE, LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING, PAGES,
                                    PHASE_JOURNALING, PHASE_LAUNCHING, RUNNING, UNKNOWN,
                                    WAITING, PanelState)

log = logging.getLogger(__name__)

# ───────────────────────── pages ─────────────────────────

PAGE_TITLES = {
    "overview": "Overview", "sessions": "Sessions", "spend": "Spend",
    "system": "System", "journal": "Journal", "settings": "Settings",
}
PAGE_ICONS = {
    "overview": "go-home-symbolic",
    "sessions": "utilities-terminal-symbolic",
    "spend": "accessories-calculator-symbolic",
    "system": "computer-symbolic",
    "journal": "accessories-text-editor-symbolic",
    "settings": "emblem-system-symbolic",
}
DEFAULT_PAGE = "overview"


def parse_page(target: str | None) -> tuple[str, str | None]:
    """'sessions:board' -> ('sessions', 'board'); unknown or empty -> overview.

    The detail is everything after the first ':' so a journal id may itself
    contain colons.
    """
    raw = (target or "").strip()
    page, sep, detail = raw.partition(":")
    page = page.strip().lower()
    if page not in PAGES:
        return DEFAULT_PAGE, None
    return page, (detail.strip() or None) if sep else None


# ───────────────────────── activity / levels ─────────────────────────

@dataclass(frozen=True)
class ActivityMeta:
    glyph: str
    label: str        # the word on pills and the accessible name
    css: str          # css suffix: sp-glyph-<css>, sp-pill-<css>


ACTIVITY_META: dict[str, ActivityMeta] = {
    WAITING: ActivityMeta("❓", "Needs you", "waiting"),
    RUNNING: ActivityMeta("▶", "Running", "running"),
    IDLE: ActivityMeta("·", "Idle", "idle"),
    PHASE_LAUNCHING: ActivityMeta("◌", "Starting", "launching"),
    PHASE_JOURNALING: ActivityMeta("⏳", "Journaling", "journaling"),
    UNKNOWN: ActivityMeta("?", "Unknown", "idle"),
}


def activity_meta(state: str | None) -> ActivityMeta:
    return ACTIVITY_META.get(state or UNKNOWN, ACTIVITY_META[UNKNOWN])


def display_state(session) -> str:
    """The state a session row shows: a panel-owned phase wins over activity."""
    phase = getattr(session, "phase", None)
    if phase in (PHASE_JOURNALING, PHASE_LAUNCHING):
        return phase
    return getattr(session, "activity", UNKNOWN) or UNKNOWN


LEVEL_CSS = {LEVEL_OK: "ok", LEVEL_WARNING: "warn", LEVEL_CRITICAL: "crit"}
LEVEL_WORDS = {LEVEL_OK: "Fine", LEVEL_WARNING: "Warning", LEVEL_CRITICAL: "Critical"}


def level_chip(level: int | None, *, na_text: str = "Not configured") -> tuple[str, str]:
    """(text, css suffix) for a LevelChip. `None` level = not available."""
    if level is None:
        return na_text, "na"
    level = max(LEVEL_OK, min(int(level), LEVEL_CRITICAL))
    return LEVEL_WORDS[level], LEVEL_CSS[level]


# ───────────────────────── nav badges ─────────────────────────

@dataclass(frozen=True)
class Badge:
    text: str
    css: str          # "amber" | "red"
    a11y: str         # spoken description, so the badge is never colour-only


def nav_badge(page: str, state: PanelState) -> Badge | None:
    """Badge for one sidebar row (SP-2103, SP-0109, SP-1212)."""
    if page == "sessions":
        if not state.sessions.tmux_ok:
            return Badge("!", "red", "tmux is not installed")
        n = state.sessions.count(WAITING)
        if n:
            return Badge(str(n), "amber",
                         f"{n} session{'s' if n != 1 else ''} need{'s' if n == 1 else ''} you")
        return None
    if page == "system":
        swap = state.system.swap
        if swap.percent is None:
            return None
        if swap.level >= LEVEL_CRITICAL:
            return Badge("!", "red", "Swap is critically full")
        if swap.level == LEVEL_WARNING:
            return Badge("·", "amber", "Swap is filling up")
    return None


# ───────────────────────── swap banner ─────────────────────────

@dataclass(frozen=True)
class BannerModel:
    level: int
    headline: str
    text: str          # full sentence including the headline
    show_action: bool  # 'Open System' button


def _gb(v: float | None) -> str:
    if v is None:
        return "?"
    return f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def swap_banner(state: PanelState, page: str) -> BannerModel | None:
    """SP-0106: amber/red window banner; hidden at OK or with no swap."""
    swap = state.system.swap
    if swap.percent is None or swap.level <= LEVEL_OK:
        return None
    crit = swap.level >= LEVEL_CRITICAL
    headline = "Swap is critically full" if crit else "Swap is filling up"
    size = ""
    if swap.used_gb is not None and swap.total_gb:
        size = f" ({swap.used_gb:.1f} of {_gb(swap.total_gb)} GB)"
    text = (f"{headline} · {swap.percent:.0f}% used{size}. "
            f"Close something heavy before the system stalls.")
    return BannerModel(LEVEL_CRITICAL if crit else LEVEL_WARNING, headline, text,
                       show_action=(page != "system"))


# ───────────────────────── sidebar footer ─────────────────────────

def last_update(state: PanelState) -> float | None:
    stamps = [state.system.updated_at, state.spend.updated_at, state.sessions.updated_at]
    known = [t for t in stamps if t]
    return max(known) if known else None


def format_ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s ago"
    m = s // 60
    if m < 60:
        return f"{m} min ago"
    return f"{m // 60} h ago"


def footer_status(state: PanelState, now: float | None = None) -> str:
    """'Updated 3 s ago' / 'Reading transcripts… 120/1,284' / 'Starting…'."""
    loading = state.app.loading
    if loading is not None:
        if loading.total:
            return f"Reading transcripts… {loading.done:,}/{loading.total:,}"
        return "Reading transcripts…"
    stamp = last_update(state)
    if stamp is None:
        return "Waiting for data…" if not state.app.demo else "Demo data"
    now = time.time() if now is None else now
    return f"Updated {format_ago(now - stamp)}"


def os_label(os_release: Path = Path("/etc/os-release")) -> str:
    """'Ubuntu 26.04' from os-release; '' if unreadable."""
    try:
        fields = {}
        for line in os_release.read_text().splitlines():
            k, _, v = line.partition("=")
            fields[k.strip()] = v.strip().strip('"')
        name, ver = fields.get("NAME", ""), fields.get("VERSION_ID", "")
        return f"{name} {ver}".strip()
    except OSError:
        return ""


def footer_version(state: PanelState, os_name: str = "") -> str:
    """'v0.1.0 · Ubuntu 26.04' (+ ' · demo busy' in demo mode)."""
    parts = []
    if state.app.version:
        parts.append(f"v{state.app.version}")
    if os_name:
        parts.append(os_name)
    if state.app.demo:
        parts.append(f"demo {state.app.demo}")
    return " · ".join(parts)


# ───────────────────────── toasts ─────────────────────────

TOAST_SECONDS = 4
TOAST_ERROR_SECONDS = 6


@dataclass(frozen=True)
class ToastModel:
    text: str
    error: bool
    timeout: int
    action_label: str | None
    action_page: str | None


def toast_model(data: dict | None) -> ToastModel | None:
    """Validate an Event('toast') payload; None if there is nothing to say."""
    data = data or {}
    text = str(data.get("text") or "").strip()
    if not text:
        return None
    error = bool(data.get("error"))
    action = data.get("action") or None
    label = page = None
    if isinstance(action, dict) and action.get("label"):
        label = str(action["label"])
        page = str(action.get("page") or "") or None
    return ToastModel(text, error, TOAST_ERROR_SECONDS if error else TOAST_SECONDS,
                      label, page)


# ───────────────────────── remembered window state ─────────────────────────

MIN_W, MIN_H = 360, 400
DEFAULT_SIZE = (900, 680)


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "smartpanel"


def window_state_path() -> Path:
    return state_dir() / "window.json"


@dataclass
class WindowState:
    width: int = DEFAULT_SIZE[0]
    height: int = DEFAULT_SIZE[1]
    maximized: bool = False
    page: str = DEFAULT_PAGE


def load_window_state(path: Path | None) -> WindowState:
    """Never raises; garbage falls back to defaults field by field."""
    ws = WindowState()
    if path is None:
        return ws
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return ws
    if not isinstance(data, dict):
        return ws
    try:
        w, h = int(data.get("width", ws.width)), int(data.get("height", ws.height))
        if w >= MIN_W and h >= MIN_H:
            ws.width, ws.height = min(w, 8000), min(h, 8000)
    except (TypeError, ValueError):
        pass
    ws.maximized = bool(data.get("maximized", False))
    page = data.get("page")
    if isinstance(page, str) and page in PAGES:
        ws.page = page
    return ws


def save_window_state(path: Path | None, ws: WindowState) -> bool:
    """Atomic write; returns False (and logs) on failure."""
    if path is None:
        return False
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"width": ws.width, "height": ws.height,
                                   "maximized": ws.maximized, "page": ws.page}))
        os.replace(tmp, path)
        return True
    except OSError as e:
        log.warning("could not save window state to %s: %s", path, e)
        return False
