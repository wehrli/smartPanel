"""The panel's whole observable state, as plain dataclasses.

This module is the CONTRACT between the back end (collectors, services) and every
surface (tray bar + menu, GTK4 dashboard, palette, D-Bus, HUD extension). Surfaces
only ever read it; collectors/services only ever write it through `Store`.

Rules:
- GTK-free, import-safe anywhere (tests, `--praise-report`, D-Bus).
- Every field has a default, so a fresh `PanelState()` is a valid "nothing known yet"
  state that every surface must render without errors.
- `None` means "unknown / not available" (never 0). Surfaces render it as "—" or hide
  the row, never as a number.
- Times are epoch seconds (float). Money is USD float. Sizes are GiB float.
- Lists that the UI shows are already in display order.

Adding a field: add it here with a default, document it, and keep `to_dict` working
(plain types only: str/int/float/bool/None/list/dict and nested dataclasses).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ───────────────────────── enums as plain strings (JSON-friendly) ─────────────────────────

# Session activity, read from the tmux pane (sessions/activity.py).
WAITING = "waiting"      # blocked on the user (permission prompt / question box)
RUNNING = "running"      # generating, running a tool, or waiting on background agents
IDLE = "idle"            # ready for a prompt
UNKNOWN = "unknown"
ACTIVITIES = (WAITING, RUNNING, IDLE, UNKNOWN)

# Panel-owned lifecycle phase, layered on top of activity.
PHASE_NONE = "none"
PHASE_LAUNCHING = "launching"    # the panel started it; tmux may not list it yet
PHASE_JOURNALING = "journaling"  # close-with-journal in progress (see JournalProgress)
PHASES = (PHASE_NONE, PHASE_LAUNCHING, PHASE_JOURNALING)

# Swap alert level — mirrors alerts.Level (IntEnum) as an int.
LEVEL_OK, LEVEL_WARNING, LEVEL_CRITICAL = 0, 1, 2

# Journal run steps, in order. JournalProgress.step indexes this tuple.
JOURNAL_STEPS = (
    "Wait for the session to go idle",
    "Type /journal {project} into the session",
    "Skill appends to the monthly journal",
    "Close the tmux session",
    "Write the receipt",
)

# Journal outcomes.
OUTCOME_JOURNALED = "journaled"
OUTCOME_SKILL_FAILED = "skill_failed"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_UNKNOWN = "unknown"

# Dashboard pages (routing keys).
PAGES = ("overview", "sessions", "spend", "system", "journal", "settings")

# Store sections (the unit of change notification).
SECTIONS = ("system", "spend", "sessions", "journal", "app", "settings")


# ───────────────────────── system ─────────────────────────

@dataclass
class CpuState:
    percent: float | None = None
    temp_c: float | None = None
    cores: int = 0
    history: list[float] = field(default_factory=list)   # percent, oldest first


@dataclass
class MemoryState:
    percent: float | None = None
    used_gb: float | None = None
    total_gb: float | None = None
    history: list[float] = field(default_factory=list)


@dataclass
class SwapState:
    percent: float | None = None        # None == no swap configured (NOT 0%)
    used_gb: float | None = None
    total_gb: float | None = None
    level: int = LEVEL_OK                # LEVEL_* from alerts.AlertTracker
    warn_at: float = 60.0
    crit_at: float = 85.0
    history: list[float] = field(default_factory=list)


@dataclass
class DiskState:
    mount: str = "/"
    percent: float | None = None
    used_gb: float | None = None
    total_gb: float | None = None


@dataclass
class NetState:
    down_bps: float | None = None        # bytes per second
    up_bps: float | None = None
    iface: str | None = None             # interface carrying the default route
    history_down: list[float] = field(default_factory=list)
    history_up: list[float] = field(default_factory=list)


@dataclass
class GpuState:
    available: bool = False
    vendor: str | None = None            # "nvidia" | "amd" | None
    percent: float | None = None
    vram_used_gb: float | None = None
    vram_total_gb: float | None = None
    temp_c: float | None = None
    history: list[float] = field(default_factory=list)


@dataclass
class SystemState:
    cpu: CpuState = field(default_factory=CpuState)
    memory: MemoryState = field(default_factory=MemoryState)
    swap: SwapState = field(default_factory=SwapState)
    disk: DiskState = field(default_factory=DiskState)
    net: NetState = field(default_factory=NetState)
    gpu: GpuState = field(default_factory=GpuState)
    updated_at: float | None = None


# ───────────────────────── Claude spend ─────────────────────────

@dataclass
class ProjectSpend:
    name: str
    cost_usd: float = 0.0
    tokens: int = 0


@dataclass
class ModelSpend:
    family: str                          # "Opus" | "Sonnet" | "Haiku" | "Other"
    cost_usd: float = 0.0
    tokens: int = 0


@dataclass
class DaySpend:
    date: str                            # ISO local date, "2026-11-17"
    label: str                           # "Today", "Yesterday", "Mon 16 Nov"
    cost_usd: float = 0.0
    tokens: int = 0
    projects: list[ProjectSpend] = field(default_factory=list)   # by cost desc
    models: list[ModelSpend] = field(default_factory=list)       # by cost desc
    praise_cost_usd: float = 0.0         # emitting compliments (output rate)
    praise_echo_cost_usd: float = 0.0    # re-reading them later (cache-read rate)


@dataclass
class Forecast:
    budget_usd: float | None = None      # None == no budget configured
    pct: float | None = None             # spent / budget * 100
    eta: str | None = None               # local "HH:MM" today when the budget runs out
    eta_reason: str | None = None        # "never" (no burn) | "tomorrow" | "over" | None
    over_by_usd: float = 0.0


@dataclass
class SpendState:
    cost_today: float | None = None
    cost_week: float | None = None
    cost_lifetime: float | None = None
    tokens_today: int = 0
    tokens_today_human: str = ""         # "41.2M"
    burn_per_hour: float | None = None   # USD over the last 60 min
    cache_hit_pct: float | None = None
    hourly_today: list[float] = field(default_factory=lambda: [0.0] * 24)  # local hours
    projects_today: list[ProjectSpend] = field(default_factory=list)       # by cost desc
    models_today: list[ModelSpend] = field(default_factory=list)
    praise_cost_today: float = 0.0
    praise_echo_today: float = 0.0
    days: list[DaySpend] = field(default_factory=list)   # newest first, 14 entries
    forecast: Forecast = field(default_factory=Forecast)
    updated_at: float | None = None


# ───────────────────────── sessions ─────────────────────────

@dataclass
class Prompt:
    question: str
    options: list[str] = field(default_factory=list)
    cursor: int = 0                      # index of the option under ❯
    kind: str = "permission"             # "permission" | "question"
    fingerprint: str = ""                # stable hash of question+options


@dataclass
class JournalProgress:
    step: int = 0                        # index into JOURNAL_STEPS currently running
    failed: bool = False                 # the skill failed; the session still closes
    outcome: str | None = None           # OUTCOME_* once finished


@dataclass
class Session:
    name: str                            # tmux session name, e.g. "claude-ledgerly" — the id
    project: str = ""
    title: str = ""                      # Claude's conversation title; "" until named
    cwd: str | None = None
    attached: bool = False
    created: float = 0.0                 # epoch seconds
    activity: str = UNKNOWN              # ACTIVITIES
    phase: str = PHASE_NONE              # PHASES
    doing: str | None = None             # running: "Bash · npm test", "Waiting for 2 background agents"
    prompt: Prompt | None = None         # waiting: the parsed question, if parseable
    waiting_since: float | None = None
    cost_usd: float | None = None        # None == unknown / ambiguous
    journal: JournalProgress | None = None

    @property
    def display_title(self) -> str:
        return self.title or self.project or self.name


@dataclass
class SessionsState:
    items: list[Session] = field(default_factory=list)   # display order (see services)
    tmux_ok: bool = True
    terminal_ok: bool = True
    updated_at: float | None = None

    def count(self, activity: str) -> int:
        return sum(1 for s in self.items if s.activity == activity)


# ───────────────────────── journal history ─────────────────────────

@dataclass
class JournalEntry:
    id: str                              # receipt file stem
    path: str
    time: float = 0.0
    title: str = ""
    project: str = ""
    outcome: str = OUTCOME_UNKNOWN
    confirmation: str = ""               # the /journal skill's confirmation line, or why not
    duration_s: int | None = None
    files: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    tools: dict[str, int] = field(default_factory=dict)
    readable: bool = True


@dataclass
class JournalState:
    entries: list[JournalEntry] = field(default_factory=list)   # newest first
    monthly_log_path: str | None = None  # e.g. ~/dev/omdev/JOURNAL/2026-11.md if known


# ───────────────────────── app / settings ─────────────────────────

@dataclass
class Loading:
    done: int = 0
    total: int = 0


@dataclass
class AppState:
    version: str = ""
    loading: Loading | None = None       # first aggregator scan in progress
    first_run: bool = False              # no Claude transcripts at all
    demo: str | None = None              # demo scenario name when running --demo
    ticker_pinned: bool = False
    ticker_index: int = 0


@dataclass
class SettingsState:
    values: dict = field(default_factory=dict)        # config key -> effective value
    overridden_by: dict = field(default_factory=dict) # config key -> env var name


@dataclass
class PanelState:
    system: SystemState = field(default_factory=SystemState)
    spend: SpendState = field(default_factory=SpendState)
    sessions: SessionsState = field(default_factory=SessionsState)
    journal: JournalState = field(default_factory=JournalState)
    app: AppState = field(default_factory=AppState)
    settings: SettingsState = field(default_factory=SettingsState)


# ───────────────────────── events (one-shot UI feedback) ─────────────────────────

@dataclass
class Event:
    """One-shot feedback that is not state: toasts, dialogs to open, navigation.

    kind:
      "toast"          data: {text, error: bool, action: {label, page} | None}
      "confirm_close"  data: {name}            -> UI opens the journal-confirm dialog
      "confirm_close_all" data: {}
      "open_dashboard" data: {page}            -> page may be "journal:<id>", "sessions:board"
      "open_palette"   data: {}
      "new_session"    data: {}                -> UI opens the folder picker
    """
    kind: str
    data: dict = field(default_factory=dict)
