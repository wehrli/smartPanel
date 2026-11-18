"""Pure presentation for the Overview and System pages (W2-overview-system).

Everything these pages *say* is decided here, from `PanelState` alone, so the copy
is unit-tested without a display and the GTK side only moves strings into labels.
The copy follows docs/design/prototype.html (`overviewHTML`, `metricsHTML`,
`sessionRow`, `systemHTML`) word for word, except where noted.

Note: this module lives under `ui/widgets/` for file-ownership reasons, so
importing it loads GTK through the package `__init__`; nothing here uses it.
"""
from __future__ import annotations

import json
import logging
import os
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from smartpanel.state.model import (LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING,
                                    PHASE_JOURNALING, PHASE_LAUNCHING, WAITING,
                                    DiskState, GpuState, MemoryState, NetState, Session,
                                    SpendState, SwapState)

log = logging.getLogger(__name__)

DASH = "—"

# ───────────────────────── numbers ─────────────────────────


def money(v: float | None) -> str:
    """'$23.84'; unknown is '—', never '$0.00'."""
    return DASH if v is None else f"${v:,.2f}"


def pct(v: float | None) -> str:
    return DASH if v is None else f"{v:.0f}%"


def gb(v: float | None) -> str:
    """Whole numbers stay whole ('8', '476'), the rest get one decimal ('10.9')."""
    if v is None:
        return "?"
    return f"{v:.0f}" if abs(v - round(v)) < 0.05 or v >= 100 else f"{v:.1f}"


def rate(bps: float | None) -> str:
    """Bytes per second in decimal units: '512 B/s', '180 KB/s', '2.4 MB/s'."""
    if bps is None:
        return DASH
    b = max(0.0, float(bps))
    if b >= 1e9:
        return f"{b / 1e9:.1f} GB/s"
    if b >= 1e6:
        return f"{b / 1e6:.1f} MB/s"
    if b >= 1e3:
        return f"{b / 1e3:.0f} KB/s"
    return f"{b:.0f} B/s"


def fmt_age(seconds: float | None) -> str:
    """The prototype's fmtAge: 'now', '38m', '3h10'."""
    if seconds is None:
        return ""
    m = round(max(0.0, seconds) / 60)
    if m < 1:
        return "now"
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}"


def fmt_waiting(since: float | None, now: float) -> str:
    """'4m waiting' (SP-1307); '' when unknown."""
    if not since:
        return ""
    secs = max(0, int(now - since))
    if secs < 60:
        return "just now"
    return f"{fmt_age(secs)} waiting"


# ───────────────────────── metric cards ─────────────────────────

@dataclass(frozen=True)
class Card:
    value: str
    sub: str
    level: int = LEVEL_OK
    na: bool = False          # dimmed "not available"


def cpu_card(cpu, *, detail: bool = False) -> Card:
    temp = f"{cpu.temp_c:.0f} °C" if cpu.temp_c is not None else ""
    if not detail:
        return Card(pct(cpu.percent), temp or "No temperature sensor")
    parts = [p for p in (temp, f"{cpu.cores} cores" if cpu.cores else "",
                         "15 min history") if p]
    return Card(pct(cpu.percent), " · ".join(parts))


def memory_card(mem: MemoryState, *, detail: bool = False) -> Card:
    if mem.used_gb is None or not mem.total_gb:
        return Card(pct(mem.percent), "")
    sub = f"{mem.used_gb:.1f} of {gb(mem.total_gb)} GB"
    return Card(pct(mem.percent), sub + (" used" if detail else ""))


def swap_configured(swap: SwapState) -> bool:
    return swap.percent is not None


def swap_card(swap: SwapState, *, detail: bool = False) -> Card:
    if not swap_configured(swap):
        if detail:
            return Card(DASH, "This machine has no swap, so it never alerts. RAM full means "
                              "the OOM killer, not a slowdown.", LEVEL_OK, na=True)
        return Card(DASH, "No swap configured", LEVEL_OK)
    used = swap.used_gb if swap.used_gb is not None else (
        swap.percent / 100 * swap.total_gb if swap.total_gb else None)
    size = f"{used:.1f} of {gb(swap.total_gb)} GB" if used is not None and swap.total_gb else ""
    level = max(LEVEL_OK, min(int(swap.level), LEVEL_CRITICAL))
    if detail:
        th = f"warns at {swap.warn_at:.0f}%, critical at {swap.crit_at:.0f}%"
        return Card(pct(swap.percent), " · ".join(p for p in (size, th) if p), level)
    return Card(pct(swap.percent), size, level)


def swap_thresholds(swap: SwapState) -> list[tuple[float, str]]:
    return [(swap.warn_at, "warn"), (swap.crit_at, "crit")] if swap_configured(swap) else []


# Disk bar colour (SP-0403): display only, no notification.
DISK_WARN_AT, DISK_CRIT_AT = 90.0, 97.0


def disk_level(percent: float | None) -> int:
    if percent is None:
        return LEVEL_OK
    if percent > DISK_CRIT_AT:
        return LEVEL_CRITICAL
    if percent > DISK_WARN_AT:
        return LEVEL_WARNING
    return LEVEL_OK


def disk_card(disk: DiskState) -> Card:
    if disk.percent is None:
        return Card(DASH, f"{disk.mount} is not mounted or not readable", na=True)
    size = (f"{gb(disk.used_gb)} of {gb(disk.total_gb)} GB on {disk.mount}"
            if disk.used_gb is not None and disk.total_gb else f"on {disk.mount}")
    return Card(pct(disk.percent), size, disk_level(disk.percent))


def net_card(net: NetState) -> Card:
    if net.down_bps is None and net.up_bps is None:
        return Card(DASH, "No network interface found", na=True)
    up = f"↑ {rate(net.up_bps)}"
    return Card(f"↓ {rate(net.down_bps)}", f"{up} · {net.iface}" if net.iface else up)


def gpu_card(gpu: GpuState) -> Card:
    if not gpu.available:
        return Card(DASH, "No NVIDIA or AMD GPU found. The row hides itself in the menu.",
                    na=True)
    parts = []
    if gpu.vram_used_gb is not None and gpu.vram_total_gb:
        parts.append(f"VRAM {gpu.vram_used_gb:.1f} of {gb(gpu.vram_total_gb)} GB")
    if gpu.temp_c is not None:
        parts.append(f"{gpu.temp_c:.0f} °C")
    return Card(pct(gpu.percent), " · ".join(parts))


def today_card(spend: SpendState, first_run: bool) -> Card:
    if first_run:
        return Card(DASH, "No transcripts yet")
    if spend.cost_today is None:
        return Card(DASH, "Not read yet")
    tokens = spend.tokens_today_human or "0"
    burn = f"${spend.burn_per_hour:.2f}/h" if spend.burn_per_hour is not None else ""
    return Card(money(spend.cost_today),
                " · ".join(p for p in (f"{tokens} tokens", burn) if p))


def current_hour(now: float | None = None) -> int:
    return time.localtime(time.time() if now is None else now).tm_hour


def hourly_bars(spend: SpendState, now: float | None = None) -> tuple[list[float], int]:
    """Hours 0..now (the prototype stops at the current hour), highlight = last."""
    hours = list(spend.hourly_today or [])
    if not hours:
        return [], -1
    h = min(current_hour(now), len(hours) - 1)
    trimmed = hours[:h + 1]
    return trimmed, len(trimmed) - 1


# ───────────────────────── budget (SP-0903) ─────────────────────────

@dataclass(frozen=True)
class Budget:
    headline: str          # "$23.84 / $40.00"
    percent: float         # for the track; > 100 turns it red
    sub: str
    over: bool


def budget(spend: SpendState) -> Budget | None:
    """None when no budget is configured (the card offers a Settings link instead)."""
    f = spend.forecast
    if f.budget_usd is None or f.budget_usd <= 0:
        return None
    spent = spend.cost_today
    if spent is None:
        return Budget(f"{DASH} / {money(f.budget_usd)}", 0.0, "Waiting for today’s spend", False)
    p = f.pct if f.pct is not None else spent / f.budget_usd * 100
    over = f.eta_reason == "over" or spent > f.budget_usd
    head = f"{money(spent)} / {money(f.budget_usd)}"
    if over:
        by = f.over_by_usd or (spent - f.budget_usd)
        return Budget(head, max(p, 100.01), f"Over budget by {money(by)}", True)
    burn = spend.burn_per_hour
    at = f"At ${burn:.2f}/h" if burn is not None else "At the current rate"
    if f.eta:
        sub = f"{at} you reach it at {f.eta}"
    elif f.eta_reason == "tomorrow":
        sub = f"{at} you reach it tomorrow"
    elif f.eta_reason == "never" or not burn:
        sub = f"{at} you won’t reach it today"
    else:
        sub = ""
    return Budget(head, p, sub, False)


# ───────────────────────── today by project (SP-0807) ─────────────────────────

# Fixed palette; the first four are the prototype's projSplit colours. Every
# entry reads as a bar on both the light card (#fff) and the dark one (#363636).
PROJECT_PALETTE: tuple[str, ...] = (
    "#e95420", "#f6a05c", "#62a0ea", "#9a7fd1",
    "#2ec27e", "#e5a50a", "#c061cb", "#1c9fa8",
)
OTHERS_COLOR = "#9a9996"
MAX_PROJECT_ROWS = 6


def project_color(name: str) -> str:
    """Stable across runs, pages and days: CRC32 of the name into PROJECT_PALETTE.

    (Python's `hash()` is salted per process, so it can't be used here.)
    """
    return PROJECT_PALETTE[_slot(name)]


def _slot(name: str) -> int:
    return zlib.crc32(name.encode("utf-8")) % len(PROJECT_PALETTE)


class ProjectColors:
    """Sticky name -> palette slot assignments (SP-0807).

    A project's colour must not depend on which other projects spent money that
    day, so a slot, once given, is kept for good. A new name starts at its CRC32
    slot and linear-probes past slots that already belong to *any* known
    project; once every slot is taken, past the ones held by the projects shown
    alongside it; failing both it keeps its CRC32 slot. Names new in one call
    are assigned in sorted order, so the result doesn't depend on ranking.

    `path` (optional) persists the map as JSON so colours also survive a
    restart; it is read once and rewritten only when a new name is assigned.
    Never raises on a bad or unwritable file.
    """

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self._slots: dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        if self.path is None:
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        n = len(PROJECT_PALETTE)
        return {str(k): v for k, v in data.items()
                if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n}

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._slots, sort_keys=True))
            tmp.replace(self.path)
        except OSError:
            log.warning("could not save project colours to %s", self.path, exc_info=True)

    def _pick(self, name: str, shown: set[str]) -> int:
        n = len(PROJECT_PALETTE)
        start = _slot(name)
        for avoid in (set(self._slots.values()),
                      {self._slots[x] for x in shown if x in self._slots}):
            if len(avoid) < n:
                slot = start
                while slot in avoid:
                    slot = (slot + 1) % n
                return slot
        return start

    def colors(self, names) -> dict[str, str]:
        names = set(names)
        new = sorted(x for x in names if x not in self._slots)
        for name in new:
            self._slots[name] = self._pick(name, names)
        if new:
            self._save()
        return {x: PROJECT_PALETTE[self._slots[x]] for x in names}


_PROJECT_COLORS = ProjectColors()


def use_project_colors(registry: ProjectColors) -> None:
    """Swap the process-wide registry (e.g. for one persisted in the state dir)."""
    global _PROJECT_COLORS
    _PROJECT_COLORS = registry


def project_colors(names, registry: ProjectColors | None = None) -> dict[str, str]:
    """Colours for the projects shown together: distinct while the palette
    lasts, and each one stable across pages and days (see ProjectColors). Only
    the Overview colours projects today; any page that starts colouring them
    should go through this too."""
    return (registry or _PROJECT_COLORS).colors(names)


@dataclass(frozen=True)
class ProjectBar:
    name: str
    cost: float
    color: str
    fraction: float        # of the largest row


def project_bars(projects, limit: int = MAX_PROJECT_ROWS,
                 registry: ProjectColors | None = None) -> list[ProjectBar]:
    """Largest first, at most `limit` rows plus one 'others' row for the rest."""
    items = sorted((p for p in projects if p.cost_usd > 0), key=lambda p: -p.cost_usd)
    shown = items[:limit]
    colors = project_colors((p.name for p in shown), registry)
    rows = [(p.name, p.cost_usd, colors[p.name]) for p in shown]
    rest = items[limit:]
    if rest:
        rows.append((f"{len(rest)} others", sum(p.cost_usd for p in rest), OTHERS_COLOR))
    top = max((c for _, c, _ in rows), default=0) or 1.0
    return [ProjectBar(n, c, col, c / top) for n, c, col in rows]


# ───────────────────────── sessions ─────────────────────────

def display_state(s: Session) -> str:
    if s.phase in (PHASE_JOURNALING, PHASE_LAUNCHING):
        return s.phase
    return s.activity


@dataclass(frozen=True)
class SessionRow:
    state: str
    title: str
    sub: str               # "project · ● attached · $2.90", or the journal step
    right: str             # age, "starting…", or "⏳ 3/5" while journaling
    journaling: bool
    busy: bool             # journaling or launching: ⟳ / ✕ / focus disabled
    failed: bool = False   # the journal skill failed; the session closes anyway


def session_row(s: Session, now: float, monthly_log: str | None = None) -> SessionRow:
    """A journaling row puts the step text in the (wide) sub-line and only a
    short '⏳ 3/5' in the right slot, so the title keeps its room (SP-1605)."""
    from smartpanel.state.model import JOURNAL_STEPS
    from smartpanel.uiformat import journal_step_text
    state = display_state(s)
    journaling = state == PHASE_JOURNALING
    launching = state == PHASE_LAUNCHING
    failed = False
    if journaling:
        j = s.journal
        index = max(0, min(j.step if j else 0, len(JOURNAL_STEPS) - 1))
        step = journal_step_text(index, s.project or s.name,
                                 headless=bool(j and j.headless), monthly_log=monthly_log)
        failed = bool(j and j.failed)
        progress = f"{index + 1}/{len(JOURNAL_STEPS)}"
        if failed:
            sub, right = f"✕ {step} · failed, closing anyway", f"✕ {progress}"
        else:
            sub, right = f"{step}…", f"⏳ {progress}"
        return SessionRow(state, s.display_title, sub, right, True, True, failed)
    parts = [s.project or s.name]
    if s.attached:
        parts.append("● attached")
    if s.cost_usd is not None:                             # SP-1403: hidden when unknown
        parts.append(money(s.cost_usd))
    if launching:
        right = "starting…"
    else:
        right = fmt_age(now - s.created) if s.created else ""
    return SessionRow(state, s.display_title, " · ".join(parts), right, False, launching)


@dataclass(frozen=True)
class NeedsRow:
    title: str
    question: str          # “Do you want …?” · 4m waiting


def needs_rows(items, now: float) -> list[tuple[str, NeedsRow]]:
    out = []
    for s in items:
        if display_state(s) != WAITING:
            continue
        q = (s.prompt.question if s.prompt and s.prompt.question else "Waiting for input")
        waited = fmt_waiting(s.waiting_since, now)
        line = f"“{q}”" + (f" · {waited}" if waited else "")
        out.append((s.name, NeedsRow(s.display_title, line)))
    return out

