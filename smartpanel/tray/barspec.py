"""Pure top-bar model: `PanelState` + bar settings -> `BarSpec`.

GTK-free and GLib-free, so every combination is unit-tested without a display.
`barrender` turns `BarSpec.icon` into a wide PNG, `tray` pushes `label`/`guide` over
the SNI `XAyatanaLabel` property. The composition mirrors the prototype's `barInner`:

    A6 strip   one block per session (waiting / running / idle / launching / journaling)
    A4 graphs  CPU histogram (last 10 samples), memory meter, swap meter
    A1 text    '12 · 4/1 · 23.84'   (swap · sessions[/waiting] · spend today)
    A5 ticker  one reading at a time with '●○○○' position dots, replaces A1

During the cold transcript scan the whole bar is a spinner + 'reading 812/1284'.

Theme precedence (SP-1904): when a theme carries its own `smartpanel.top_bar_format`,
that template renders the label instead of A1/A5 (see `theme_template`). The shipped
presets' templates and the loader's fallback count as "no template", so they get
the composition.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from smartpanel.state.model import (
    IDLE, LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING, PHASE_JOURNALING, PHASE_LAUNCHING,
    RUNNING, WAITING, PanelState,
)

# Block kinds of the A6 strip (also the effective display state of a session).
LAUNCHING = PHASE_LAUNCHING
JOURNALING = PHASE_JOURNALING
STRIP_KINDS = (WAITING, RUNNING, IDLE, LAUNCHING, JOURNALING)
STRIP_MAX = 6                 # blocks before '+N'
HIST_SAMPLES = 10             # CPU histogram length

NO_VALUE = "—"
WARN_GLYPH = "⚠"
TMUX_MISSING = "!"
DOT_ON, DOT_OFF = "●", "○"

# Fallback icon names (freedesktop / Adwaita symbolic names, present on stock Ubuntu).
ICON_PLAIN = "utilities-terminal-symbolic"
ICON_LOADING = "process-working-symbolic"

TICKER_KEYS = ("cost", "sessions", "swap", "cpu")

# Bar settings and their defaults (docs/architecture/v2.md "Settings keys").
BAR_DEFAULTS: dict[str, Any] = {
    "bar.strip": True, "bar.graphs": True, "bar.text": True, "bar.ticker": False,
    "bar.ticker_seconds": 5,
}


@dataclass(frozen=True)
class IconSpec:
    """Everything the cairo renderer needs; hashable, so it doubles as a cache key."""
    strip: tuple[str, ...] = ()           # STRIP_KINDS, display order, at most STRIP_MAX
    strip_more: int = 0                   # sessions beyond STRIP_MAX ('+N')
    graphs: bool = False
    cpu: tuple[float, ...] = ()           # last HIST_SAMPLES percents, oldest first
    mem_pct: float | None = None
    swap_pct: float | None = None         # None == no swap configured (empty meter)
    swap_level: int = LEVEL_OK

    @property
    def empty(self) -> bool:
        return not self.strip and not self.strip_more and not self.graphs


@dataclass(frozen=True)
class BarSpec:
    label: str = ""
    icon: IconSpec | None = None          # None -> show `icon_name`
    icon_name: str = ICON_PLAIN
    accessible_desc: str = "smartPanel"
    guide: str = ""
    ticker: bool = False                  # A5 is what the label shows
    ticker_pinned_by_alert: bool = False
    loading: bool = False


# ───────────────────────── small formatters (shared with menu.py) ─────────────────────────

def rnd(v: float) -> int:
    """Round half up (Python's round() is banker's: 12.5 -> 12)."""
    return int(math.floor(v + 0.5))


def pct(v: float | None) -> str:
    return NO_VALUE if v is None else f"{rnd(v)}%"


def money(v: float) -> str:
    return f"${v:,.2f}"


def session_state(s) -> str:
    """Effective display state: a panel-owned phase wins over the pane activity."""
    phase = getattr(s, "phase", None)
    if phase in (PHASE_LAUNCHING, PHASE_JOURNALING):
        return phase
    act = getattr(s, "activity", IDLE)
    return act if act in (WAITING, RUNNING) else IDLE


def counts(state: PanelState) -> dict[str, int]:
    c = {k: 0 for k in STRIP_KINDS}
    for s in state.sessions.items:
        c[session_state(s)] += 1
    return c


def bar_settings(config: Any) -> dict[str, Any]:
    """Accepts a `Config` (has .get), a plain mapping, or None."""
    out = dict(BAR_DEFAULTS)
    if config is None:
        return out
    getter = config.get if hasattr(config, "get") else None
    for key in BAR_DEFAULTS:
        try:
            v = getter(key) if getter else None
        except Exception:  # a broken config must never break the bar
            v = None
        if v is not None:
            out[key] = v
    return out


# ───────────────────────── segments ─────────────────────────

def swap_segment(state: PanelState) -> str:
    sw = state.system.swap
    text = NO_VALUE if sw.percent is None else str(rnd(sw.percent))
    if sw.level >= LEVEL_WARNING and sw.percent is not None:
        text = WARN_GLYPH + text
    return text


def sessions_segment(state: PanelState) -> str:
    if not state.sessions.tmux_ok:
        return TMUX_MISSING
    n = len(state.sessions.items)
    w = counts(state)[WAITING]
    return f"{n}/{w}" if w else str(n)


def spend_segment(state: PanelState) -> str:
    cost = state.spend.cost_today
    if state.app.first_run or cost is None:
        return NO_VALUE
    return f"{cost:.2f}"


def text_label(state: PanelState) -> str:
    """A1: '12 · 4/1 · 23.84'."""
    return f"{swap_segment(state)} · {sessions_segment(state)} · {spend_segment(state)}"


def ticker_items(state: PanelState) -> list[str]:
    """A5 readings, in rotation order (TICKER_KEYS)."""
    c = counts(state)
    cost = state.spend.cost_today
    if state.app.first_run or cost is None:
        cost_t = "No spend yet"
    else:
        cost_t = f"{money(cost)} today"
    if state.sessions.tmux_ok:
        n = len(state.sessions.items)
        sess_t = f"{n} session" + ("" if n == 1 else "s")
        if c[WAITING]:
            sess_t += f" · {c[WAITING]} waiting"
    else:
        sess_t = "tmux missing"
    swap_t = f"Swap {pct(state.system.swap.percent)}"
    cpu = state.system.cpu
    cpu_t = f"CPU {pct(cpu.percent)}"
    if cpu.temp_c is not None:
        cpu_t += f" · {rnd(cpu.temp_c)} °C"
    return [cost_t, sess_t, swap_t, cpu_t]


def ticker_alert(state: PanelState) -> str | None:
    """'⚠ Swap 88%' while the swap alert pins the ticker, else None."""
    sw = state.system.swap
    if sw.level >= LEVEL_WARNING and sw.percent is not None:
        return f"{WARN_GLYPH} Swap {rnd(sw.percent)}%"
    return None


def ticker_label(state: PanelState) -> str:
    alert = ticker_alert(state)
    if alert:
        return f"{alert} pinned by alert"
    items = ticker_items(state)
    i = state.app.ticker_index % len(items)
    dots = "".join(DOT_ON if j == i else DOT_OFF for j in range(len(items)))
    label = f"{items[i]} {dots}"
    if state.app.ticker_pinned:
        label += " pinned"
    return label


def loading_label(state: PanelState) -> str:
    ld = state.app.loading
    return f"reading {ld.done}/{ld.total}" if ld else ""


# ───────────────────────── width guide (SP-1903) ─────────────────────────

_DIGITS = re.compile(r"\d")


def _zero(s: str) -> str:
    """Every digit -> '0', so the guide doesn't move with the value itself."""
    return _DIGITS.sub("0", s)


def text_guide(state: PanelState) -> str:
    """Widest A1 label at the current magnitudes: swap '⚠100', sessions 'NN/NN'."""
    if state.sessions.tmux_ok:
        width = max(2, len(str(len(state.sessions.items))))
        sess = "0" * width + "/" + "0" * width
    else:
        sess = TMUX_MISSING
    spend = spend_segment(state)
    if spend != NO_VALUE:
        whole = spend.split(".")[0]
        spend = "0" * max(3, len(whole)) + ".00"
    return f"{WARN_GLYPH}100 · {sess} · {spend}"


def ticker_guide(state: PanelState) -> str:
    """Widest A5 label: the longest reading with all four dots and 'pinned'."""
    cands = [_zero(t) for t in ticker_items(state)]
    cands.append("Swap 100%")
    widest = max(cands, key=len)
    long_form = f"{widest} {DOT_ON * len(TICKER_KEYS)} pinned"
    alert = f"{WARN_GLYPH} Swap 100% pinned by alert"
    return max((long_form, alert), key=len)


def loading_guide(state: PanelState) -> str:
    ld = state.app.loading
    total = ld.total if ld else 0
    w = max(3, len(str(total)))
    return f"reading {'0' * w}/{'0' * w}"


# ───────────────────────── icon ─────────────────────────

def icon_spec(state: PanelState, strip: bool, graphs: bool) -> IconSpec | None:
    kinds: tuple[str, ...] = ()
    more = 0
    sess = state.sessions
    if strip and sess.tmux_ok and sess.items:
        all_kinds = [session_state(s) for s in sess.items]
        kinds = tuple(all_kinds[:STRIP_MAX])
        more = max(0, len(all_kinds) - STRIP_MAX)
    spec = IconSpec(strip=kinds, strip_more=more, graphs=bool(graphs))
    if graphs:
        sysm = state.system
        hist = list(sysm.cpu.history[-HIST_SAMPLES:])
        if not hist and sysm.cpu.percent is not None:
            hist = [sysm.cpu.percent]
        spec = IconSpec(
            strip=kinds, strip_more=more, graphs=True,
            cpu=tuple(round(float(v), 1) for v in hist),
            mem_pct=None if sysm.memory.percent is None else round(sysm.memory.percent, 1),
            swap_pct=None if sysm.swap.percent is None else round(sysm.swap.percent, 1),
            swap_level=sysm.swap.level if sysm.swap.percent is not None else LEVEL_OK,
        )
    return None if spec.empty else spec


# ───────────────────────── accessible description ─────────────────────────

def accessible_desc(state: PanelState) -> str:
    parts: list[str] = []
    sw = state.system.swap
    if sw.percent is None:
        parts.append("no swap configured")
    else:
        level = {LEVEL_WARNING: " (high)", LEVEL_CRITICAL: " (critical)"}.get(sw.level, "")
        parts.append(f"swap {rnd(sw.percent)}%{level}")
    if state.sessions.tmux_ok:
        c = counts(state)
        n = len(state.sessions.items)
        s = f"{n} session" + ("" if n == 1 else "s")
        if c[WAITING]:
            s += f", {c[WAITING]} waiting for you"
        if c[RUNNING]:
            s += f", {c[RUNNING]} running"
        parts.append(s)
    else:
        parts.append("tmux is not installed")
    if state.app.loading:
        ld = state.app.loading
        parts.append(f"reading transcripts {ld.done} of {ld.total}")
    elif state.app.first_run or state.spend.cost_today is None:
        parts.append("no Claude spend yet")
    else:
        parts.append(f"{money(state.spend.cost_today)} spent today")
    if state.system.cpu.percent is not None:
        parts.append(f"CPU {rnd(state.system.cpu.percent)}%")
    if state.system.memory.percent is not None:
        parts.append(f"memory {rnd(state.system.memory.percent)}%")
    return "smartPanel: " + ", ".join(parts)


# ───────────────────────── composition ─────────────────────────

def compose_bar(state: PanelState, config: Any = None, *,
                template_label: str | None = None) -> BarSpec:
    """The whole top bar for one state. `config` is a Config, a mapping or None.

    `template_label` is a theme's already-rendered `top_bar_format` (SP-1904); when
    given it replaces the A1/A5 text but the strip and graphs still follow the toggles.
    """
    cfg = bar_settings(config)
    desc = accessible_desc(state)

    if state.app.loading is not None:
        return BarSpec(label=loading_label(state), icon=None, icon_name=ICON_LOADING,
                       accessible_desc=desc, guide=loading_guide(state), loading=True)

    icon = icon_spec(state, bool(cfg["bar.strip"]), bool(cfg["bar.graphs"]))
    ticker = False
    pinned_by_alert = False
    if template_label is not None:
        label, guide = template_label, template_label
    elif cfg["bar.ticker"]:
        ticker = True
        pinned_by_alert = ticker_alert(state) is not None
        label, guide = ticker_label(state), ticker_guide(state)
    elif cfg["bar.text"]:
        label, guide = text_label(state), text_guide(state)
    else:
        label, guide = "", ""
    return BarSpec(label=label, icon=icon, icon_name=ICON_PLAIN, accessible_desc=desc,
                   guide=guide, ticker=ticker, ticker_pinned_by_alert=pinned_by_alert)


# ───────────────────────── theme template (SP-1904) ─────────────────────────

# The loader's fallback when a theme has no `smartpanel.top_bar_format`.
LOADER_DEFAULT_FORMAT = "⚡{CPU.PERCENTAGE:.0f}%"

_preset_formats: frozenset[str] | None = None


def _shipped_preset_formats() -> frozenset[str]:
    """Templates of the presets we ship: those switch to the composition."""
    global _preset_formats
    if _preset_formats is None:
        found: set[str] = set()
        try:
            from pathlib import Path

            import yaml
            presets = Path(__file__).resolve().parent.parent / "theme" / "presets"
            for p in presets.glob("*.yaml"):
                data = yaml.safe_load(p.read_text()) or {}
                fmt = (data.get("smartpanel") or {}).get("top_bar_format")
                if fmt:
                    found.add(str(fmt))
        except Exception:
            pass
        _preset_formats = frozenset(found)
    return _preset_formats


def theme_template(theme: Any) -> str | None:
    """The theme's own top-bar template, or None when the composition should win."""
    if theme is None:
        return None
    explicit = getattr(theme, "top_bar_format_explicit", None)
    fmt = getattr(theme, "top_bar_format", None)
    if not fmt:
        return None
    if explicit is False:
        return None
    if explicit is None and (fmt == LOADER_DEFAULT_FORMAT or fmt in _shipped_preset_formats()):
        return None
    return str(fmt)


def legacy_values(state: PanelState, now: float | None = None) -> dict[str, dict[str, Any]]:
    """Map the v2 state back to the v1 `{CATEGORY: {METRIC: value}}` namespace that
    `render.format_top_bar` templates use (covers every key the shipped presets and
    the v1 sensors exposed for the top bar)."""
    from smartpanel import alerts, uiformat
    from smartpanel.render import humanize_tokens

    s = state.system
    try:
        level = alerts.Level(int(s.swap.level))
    except ValueError:
        level = alerts.Level.OK
    swap_status = alerts.swap_status(level, s.swap.percent)
    swap_badge = alerts.swap_badge(level, s.swap.percent)
    mb = 1024 * 1024
    sp = state.spend
    c = counts(state)
    items = state.sessions.items
    return {
        "CPU": {"PERCENTAGE": s.cpu.percent, "TEMPERATURE": s.cpu.temp_c},
        "MEMORY": {"PERCENTAGE": s.memory.percent, "USED": s.memory.used_gb,
                   "TOTAL": s.memory.total_gb, "SWAP_PERCENT": s.swap.percent},
        "SWAP": {"PERCENTAGE": s.swap.percent, "USED": s.swap.used_gb,
                 "TOTAL": s.swap.total_gb, "STATUS": swap_status, "ALERT": swap_badge,
                 "LEVEL": int(level)},
        "DISK": {"USED_PERCENT": s.disk.percent, "USED": s.disk.used_gb,
                 "TOTAL": s.disk.total_gb},
        "NET": {"DOWNLOAD": None if s.net.down_bps is None else s.net.down_bps / mb,
                "UPLOAD": None if s.net.up_bps is None else s.net.up_bps / mb},
        "GPU": {"PERCENTAGE": s.gpu.percent, "TEMPERATURE": s.gpu.temp_c},
        "CLAUDE": {"COST_TODAY": sp.cost_today, "COST_WEEK": sp.cost_week,
                   "COST_LIFETIME": sp.cost_lifetime, "BURN_RATE": sp.burn_per_hour,
                   "TOKENS_TODAY": sp.tokens_today,
                   "TOKENS_TODAY_HUMAN": sp.tokens_today_human or humanize_tokens(sp.tokens_today),
                   "CACHE_HIT_RATIO": sp.cache_hit_pct,
                   "PRAISE_COST_TODAY": sp.praise_cost_today},
        "SESSIONS": {"COUNT": len(items), "WAITING": c[WAITING], "RUNNING": c[RUNNING],
                     "STATUS": uiformat.sessions_status(items) if state.sessions.tmux_ok else "🧩!",
                     "NAMES": ", ".join(x.display_title for x in items)},
    }


def render_template(theme: Any, state: PanelState) -> str | None:
    """Render the theme's own template against the state, or None (composition wins)."""
    template = theme_template(theme)
    if template is None:
        return None
    from smartpanel.render import format_top_bar
    try:
        values = legacy_values(state)
        text = format_top_bar(template, values)
    except Exception:
        return "smartPanel"
    # v1 `_with_alert_badge`: a theme that doesn't place the swap gauge itself still
    # gets the alert, prefixed because the panel truncates from the right.
    badge = values["SWAP"]["ALERT"]
    if badge and "{SWAP." not in template:
        text = f"{badge} {text}" if text else badge
    return text
