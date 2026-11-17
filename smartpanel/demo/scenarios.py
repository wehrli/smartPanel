"""The prototype's demo scenarios as pure data (SP-0012).

Everything here mirrors `docs/design/prototype.html` — grep its JS for `busySessions`,
`SCEN=`, `PAST=`, `seedJournal`, `COST_H`, `PROMPT_EDIT`, `PROMPT_ASK`, `PROJECTS` —
so a screenshot of `smartpanel --demo <name>` can be held against the prototype.

`build_state(name, now=None)` is pure and deterministic for a given `now`: the
prototype's random histories come from a `random.Random` seeded by scenario + metric,
and every time (session ages, journal receipts, hourly buckets, day labels) is relative
to `now` in local time. Pass `DEMO_NOW` to get exactly the prototype's "Tue 17 Nov 14:32".
"""
from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from smartpanel import __version__
from smartpanel.alerts import Thresholds, raw_level
from smartpanel.state.model import (
    IDLE, OUTCOME_JOURNALED, OUTCOME_TIMEOUT, PHASE_JOURNALING, PHASE_LAUNCHING, RUNNING,
    UNKNOWN, WAITING,
    AppState, CpuState, DaySpend, DiskState, Forecast, GpuState, JournalEntry, JournalState,
    Loading, MemoryState, ModelSpend, NetState, PanelState, ProjectSpend, Prompt, Session,
    SessionsState, SettingsState, SpendState, SwapState, SystemState,
)

SCENARIOS: tuple[str, ...] = ("busy", "calm", "alert", "empty", "many",
                              "cold", "first", "notmux", "noswap")

# The prototype's control-panel copy, for `--demo` help text and the screenshot suite.
DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "busy": ("Busy", "Default afternoon: four sessions, one waiting on you, two running. "
                     "Swap at 12%."),
    "calm": ("Calm", "Nothing needs you. Every session is idle and spend is low."),
    "alert": ("Swap alert", "Memory pressure: swap at 88% (critical), two sessions waiting, "
                            "spend over budget."),
    "empty": ("No sessions", "tmux is fine but nothing is running. Shows every empty state."),
    "many": ("Many sessions", "Nine sessions, to stress the strip, menu, board and HUD."),
    "cold": ("Cold start", "First launch after login: the aggregator reads every transcript "
                           "once. Plays out over a few seconds."),
    "first": ("First run", "Claude Code has never run here. No transcripts, no sessions."),
    "notmux": ("tmux missing", "tmux isn’t installed, so sessions can’t be listed or started."),
    "noswap": ("No swap", "A machine with no swap configured. The gauge reads “—” and never "
                          "alerts."),
}

# The prototype's clock: "Tue 17 Nov 14:32" (local time).
DEMO_NOW: float = datetime(2026, 11, 17, 14, 32).timestamp()

PROJECTS: tuple[str, ...] = ("PromptKit", "JobScout", "tacomap", "wishlist",
                             "LeadPilot", "noteKeeper", "ledgerly", "giftswap",
                             "smartPanel", "netConfigs")
SESSIONS_DIR = "~/dev/omdev"
DEMO_BUDGET = 40.0          # the prototype's daily budget
SWAP_TOTAL_GB = 8.0
MEM_TOTAL_GB = 16.0
CORES = 8

PROMPT_EDIT = ("Do you want to make this edit to alerts.py?",
               ("Yes", "Yes, allow all edits this session",
                "No, tell Claude what to do differently"), "permission")
PROMPT_ASK = ("Which migration strategy should I use for 0023?",
              ("Down-migrate 0022 first", "Apply on top of the current schema",
               "Skip 0023 for now"), "question")

# Today's spend by local hour; the last entry is the current hour. Sums to 23.84.
COST_H: tuple[float, ...] = (0, 0, 0, 0, 0, 0, 0, 0, .4, 1.9, 3.4, 2.8, 3.1, 4.2, 3.3, 2.6,
                             1.4, .74)
BASE_SPEND = 23.84
BASE_TOKENS_M = 41.2
# Today by project (projSplit), at the base spend. Order = cost desc.
PROJECT_SPLIT: tuple[tuple[str, float], ...] = (("smartPanel", 9.20), ("tacomap", 7.45),
                                                ("JobScout", 5.10), ("ledgerly", 2.09))
MODEL_SPLIT: tuple[tuple[str, float], ...] = (("Opus", .822), ("Sonnet", .164),
                                              ("Haiku", .014))
PRAISE_RATE, PRAISE_ECHO_RATE = .003, .009
# Previous days, newest first; values per project in PROJECT_SPLIT order.
PAST: tuple[tuple[float, ...], ...] = (
    (12.4, 9.1, 6.0, 3.52), (2.9, 1.2, 0, 0), (0, 0, 0, 0), (10.2, 9, 5, 3.46),
    (9.1, 6.2, 5, 2.18), (13, 10, 6, 4), (11, 8, 7, 2), (15, 9, 4, 3), (3, 1, 0, .1),
    (0, 0, 0, 0), (12, 7, 6, 2.7), (13, 9, 6, 3), (8, 6, 5, 1),
)
NET_DOWN_MB = (.2, .4, .3, 1.2, 2.8, 2.1, .6, .4, .9, 2.2, 3.1, 2.4)

# Ordering used by every session list (prototype ORDER).
_ORDER = {WAITING: 0, RUNNING: 1, PHASE_JOURNALING: 2, PHASE_LAUNCHING: 3, IDLE: 4, UNKNOWN: 5}

# The receipts the journal page starts with (seedJournal), newest first.
SEED_JOURNAL = (
    dict(hhmm="12:02", title="Map clustering", project="tacomap", ok=True, dur=2 * 3600 + 30 * 60,
         line="Journaled tacomap to JOURNAL/2026-11.md. Map clustering moved to supercluster "
              "on the web map; server-side tiles rejected for now.",
         files=["web/src/components/Map.tsx", "web/src/lib/cluster.ts", "web/package.json"],
         cmds=["npm test -- map", "npm run build"],
         tools={"Read": 14, "Edit": 9, "Bash": 6, "Grep": 4}),
    dict(hhmm="11:24", title="Résumé parser", project="JobScout", ok=False, dur=3600 + 24 * 60,
         line="The /journal skill timed out after 300 s. The session was still closed; this "
              "receipt holds the Activity section only.",
         files=["src/lib/resume/parse.ts", "src/lib/resume/parse.test.ts"],
         cmds=["npm test -- resume"], tools={"Read": 11, "Edit": 6, "Bash": 4}),
    dict(hhmm="10:36", title="Pricing table refresh", project="smartPanel", ok=True,
         dur=2 * 3600 + 6 * 60,
         line="Journaled smartPanel to JOURNAL/2026-11.md. pricing.py carries the 5.5 family; "
              "unknown models still fall back to Opus rates.",
         files=["smartpanel/claude_stats/pricing.py", "tests/test_pricing.py"],
         cmds=["pytest tests/test_pricing.py"], tools={"Read": 8, "Edit": 5, "Bash": 3}),
)


# ───────────────────────── helpers (pure) ─────────────────────────

def fingerprint(question: str, options) -> str:
    """Stable hash of a prompt (SP-1302 shape): the UI passes it back when answering."""
    raw = question + "\x1f" + "\x1f".join(options)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def make_prompt(spec) -> Prompt:
    q, opts, kind = spec
    return Prompt(question=q, options=list(opts), cursor=0, kind=kind,
                  fingerprint=fingerprint(q, opts))


def sort_key(s: Session) -> int:
    if s.phase in (PHASE_JOURNALING, PHASE_LAUNCHING):
        return _ORDER[s.phase]
    return _ORDER.get(s.activity, 5)


def sort_sessions(items: list[Session]) -> list[Session]:
    """Display order: waiting, running, journaling, launching, idle (stable)."""
    return sorted(items, key=sort_key)


def unique_name(project: str, taken) -> str:
    """`claude-<project>`, then `claude-<project>-2`, ... like scripts/claude-tmux."""
    base = f"claude-{project}"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def history(seed: str, base: float, spread: float, n: int = 30) -> list[float]:
    """The prototype's `hist()`: a clamped random walk that settles near `base` at the end."""
    rng = random.Random(seed)
    out, v = [], base
    for i in range(n):
        v = min(100.0, max(0.0, v + (rng.random() - .5) * spread))
        out.append(round(base + (rng.random() - .5) * spread * .3, 1) if i > n - 4
                   else round(v, 1))
    return out


def hourly_today(scale: float, now: float) -> list[float]:
    """COST_H mapped onto local hours so its last entry is the current hour. Hours that
    would fall before midnight fold into hour 0, so the day's total is preserved."""
    hour = datetime.fromtimestamp(now).hour
    out = [0.0] * 24
    start = hour - (len(COST_H) - 1)
    for i, v in enumerate(COST_H):
        out[max(0, start + i)] += v * scale
    return [round(v, 4) for v in out]


def human_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}k"
    return str(n)


def day_label(day: datetime, today: datetime) -> str:
    delta = (today.date() - day.date()).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def compute_forecast(spent: float | None, burn: float | None, budget: float | None,
                     now: float) -> Forecast:
    """The prototype's `eta()`: when today's spend reaches the budget at the current burn."""
    if budget is None or budget <= 0:
        return Forecast()
    if spent is None:
        return Forecast(budget_usd=budget)
    pct = round(spent / budget * 100, 1)
    if spent > budget:
        return Forecast(budget, pct, None, "over", round(spent - budget, 2))
    if not burn or burn <= 0:
        return Forecast(budget, pct, None, "never")
    t = datetime.fromtimestamp(now)
    hours = t.hour + t.minute / 60 + (budget - spent) / burn
    if hours >= 24:
        return Forecast(budget, pct, None, "tomorrow")
    hh, mm = int(hours), round((hours - int(hours)) * 60)
    if mm == 60:
        hh, mm = hh + 1, 0
    if hh >= 24:
        return Forecast(budget, pct, None, "tomorrow")
    return Forecast(budget, pct, f"{hh:02d}:{mm:02d}", None)


# ───────────────────────── sessions ─────────────────────────

@dataclass
class _S:
    title: str
    project: str
    state: str
    age_min: float
    cost: float
    attached: bool = False
    tool: str | None = None
    prompt: tuple | None = None
    extra: dict = field(default_factory=dict)


def _busy() -> list[_S]:
    return [
        _S("Fix swap alert hysteresis", "smartPanel", WAITING, 62, 2.90, attached=True,
           prompt=PROMPT_EDIT),
        _S("Truck search filters", "tacomap", RUNNING, 38, 1.85,
           tool="Bash · npm test -- filters"),
        _S("Stripe webhook retries", "JobScout", RUNNING, 12, 1.88,
           tool="Waiting for 2 background agents"),
        _S("Migrate goose 0023", "ledgerly", IDLE, 190, 2.09),
    ]


def _many_extra() -> list[_S]:
    return [
        _S("Vendor onboarding copy", "tacomap", IDLE, 75, .92),
        _S("Advisor dashboard filters", "ledgerly", RUNNING, 22, 1.10,
           tool="Read · web/app/advisors/page.tsx"),
        _S("Gift list sharing links", "wishlist", WAITING, 8, .40, prompt=PROMPT_EDIT),
        _S("Lead scoring prompt", "LeadPilot", IDLE, 140, 1.75),
        _S("WireGuard peer rotation", "netConfigs", IDLE, 30, .21),
    ]


def _to_session(s: _S, now: float, taken: set[str]) -> Session:
    name = unique_name(s.project, taken)
    taken.add(name)
    prompt = make_prompt(s.prompt) if (s.state == WAITING and s.prompt) else None
    return Session(
        name=name, project=s.project, title=s.title,
        cwd=f"{SESSIONS_DIR}/{s.project}", attached=s.attached,
        created=now - s.age_min * 60, activity=s.state,
        doing=s.tool if s.state == RUNNING else None,
        prompt=prompt,
        waiting_since=(now - 4 * 60) if s.state == WAITING else None,   # "4m waiting"
        cost_usd=round(s.cost, 2),
    )


# ───────────────────────── sections ─────────────────────────

@dataclass
class _Knobs:
    """The prototype's scalar state (DEFAULTS()) after a scenario's `apply`."""
    cpu: float = 23
    mem: float = 68
    temp: float = 61
    down_mb: float = 2.4
    up_mb: float = .18
    swap: float | None = 12
    spend: float = BASE_SPEND
    week: float = 142.10
    life: float = 2318.55
    tokens_m: float = BASE_TOKENS_M
    burn: float = 3.10
    cache: float = 94
    hist_cpu: tuple = (23, 14)
    hist_mem: tuple = (68, 3)
    hist_swap: list | None = None


def _system(name: str, k: _Knobs, now: float, thresholds: Thresholds) -> SystemState:
    hist_swap = k.hist_swap if k.hist_swap is not None else history(f"{name}:swap", 12, 1)
    swap = (SwapState(warn_at=float(thresholds.warning), crit_at=float(thresholds.critical))
            if k.swap is None else
            SwapState(percent=float(k.swap), used_gb=round(k.swap / 100 * SWAP_TOTAL_GB, 2),
                      total_gb=SWAP_TOTAL_GB, level=int(raw_level(k.swap, thresholds)),
                      warn_at=float(thresholds.warning), crit_at=float(thresholds.critical),
                      history=list(hist_swap)))
    return SystemState(
        cpu=CpuState(percent=float(k.cpu), temp_c=float(k.temp), cores=CORES,
                     history=history(f"{name}:cpu", *k.hist_cpu)),
        memory=MemoryState(percent=float(k.mem), used_gb=round(k.mem / 100 * MEM_TOTAL_GB, 2),
                           total_gb=MEM_TOTAL_GB, history=history(f"{name}:mem", *k.hist_mem)),
        swap=swap,
        disk=DiskState(mount="/", percent=41.0, used_gb=196.0, total_gb=476.0),
        net=NetState(down_bps=k.down_mb * 1e6, up_bps=k.up_mb * 1e6, iface="wlp2s0",
                     history_down=[v * 1e6 for v in NET_DOWN_MB],
                     history_up=[round(v * k.up_mb / k.down_mb * 1e6, 1) for v in NET_DOWN_MB]),
        gpu=GpuState(available=False),
        updated_at=now,
    )


def _split(total: float, tokens: int, parts) -> list[tuple[str, float, int]]:
    return [(n, round(total * share, 2), int(round(tokens * share))) for n, share in parts]


def _spend(k: _Knobs, now: float, budget: float | None) -> SpendState:
    scale = k.spend / BASE_SPEND
    tokens = int(round(k.tokens_m * 1e6))
    projects = [ProjectSpend(n, round(c * scale, 2), int(round(tokens * c / BASE_SPEND)))
                for n, c in PROJECT_SPLIT]
    models = [ModelSpend(n, c, t) for n, c, t in _split(k.spend, tokens, MODEL_SPLIT)]
    today = datetime.fromtimestamp(now)
    days = [DaySpend(date=today.date().isoformat(), label="Today", cost_usd=round(k.spend, 2),
                     tokens=tokens, projects=projects, models=models,
                     praise_cost_usd=round(k.spend * PRAISE_RATE, 2),
                     praise_echo_cost_usd=round(k.spend * PRAISE_ECHO_RATE, 2))]
    for i, per_project in enumerate(PAST, start=1):
        d = today - timedelta(days=i)
        total = round(sum(per_project), 2)
        d_tokens = int(round(total / BASE_SPEND * BASE_TOKENS_M * 1e6))
        projs = sorted((ProjectSpend(n, c, int(round(d_tokens * c / total)))
                        for (n, _), c in zip(PROJECT_SPLIT, per_project) if c > 0),
                       key=lambda p: -p.cost_usd)
        days.append(DaySpend(
            date=d.date().isoformat(), label=day_label(d, today), cost_usd=total,
            tokens=d_tokens, projects=projs,
            models=[ModelSpend(n, c, t) for n, c, t in _split(total, d_tokens, MODEL_SPLIT)]
            if total else [],
            praise_cost_usd=round(total * PRAISE_RATE, 2),
            praise_echo_cost_usd=round(total * PRAISE_ECHO_RATE, 2)))
    return SpendState(
        cost_today=round(k.spend, 2), cost_week=k.week, cost_lifetime=k.life,
        tokens_today=tokens, tokens_today_human=human_tokens(tokens),
        burn_per_hour=k.burn, cache_hit_pct=float(k.cache),
        hourly_today=hourly_today(scale, now),
        projects_today=projects, models_today=models,
        praise_cost_today=round(k.spend * PRAISE_RATE, 2),
        praise_echo_today=round(k.spend * PRAISE_ECHO_RATE, 2),
        days=days, forecast=compute_forecast(k.spend, k.burn, budget, now),
        updated_at=now,
    )


def _first_run_spend(now: float, budget: float | None) -> SpendState:
    return SpendState(cost_today=0.0, cost_week=0.0, cost_lifetime=0.0, tokens_today=0,
                      tokens_today_human="0", burn_per_hour=0.0, cache_hit_pct=None,
                      forecast=compute_forecast(0.0, 0.0, budget, now), updated_at=now)


def receipt_id(when: float, project: str) -> str:
    t = datetime.fromtimestamp(when)
    return f"{t:%Y-%m-%d-%H%M}-{project}"


def receipt_path(entry_id: str) -> str:
    return f"~/.claude/smartpanel/journals/{entry_id}.md"


def monthly_log_path(now: float) -> str:
    return f"{SESSIONS_DIR}/JOURNAL/{datetime.fromtimestamp(now):%Y-%m}.md"


def _journal(now: float) -> JournalState:
    entries = []
    for e in SEED_JOURNAL:
        hh, mm = map(int, e["hhmm"].split(":"))
        # Same distance before `now` as before the prototype's 14:32, so a morning `now`
        # never yields receipts from the future.
        when = now - ((14 * 60 + 32) - (hh * 60 + mm)) * 60
        rid = receipt_id(when, e["project"])
        entries.append(JournalEntry(
            id=rid, path=receipt_path(rid), time=when, title=e["title"], project=e["project"],
            outcome=OUTCOME_JOURNALED if e["ok"] else OUTCOME_TIMEOUT,
            confirmation=e["line"], duration_s=e["dur"], files=list(e["files"]),
            commands=list(e["cmds"]), tools=dict(e["tools"])))
    return JournalState(entries=entries, monthly_log_path=monthly_log_path(now))


def _settings() -> SettingsState:
    from smartpanel.config import DEFAULTS
    return SettingsState(values=dict(DEFAULTS), overridden_by={})


# ───────────────────────── build ─────────────────────────

def build_state(name: str, now: float | None = None, *, budget: float | None = DEMO_BUDGET,
                thresholds: Thresholds | None = None) -> PanelState:
    """The prototype's state for scenario `name`. Pure: same inputs, same state."""
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; choose from {', '.join(SCENARIOS)}")
    now = time.time() if now is None else float(now)
    th = thresholds or Thresholds()
    k = _Knobs()
    raw = _busy()
    tmux_ok, first_run, loading = True, False, None

    match name:
        case "calm":
            for s in raw:
                s.state, s.prompt, s.tool = IDLE, None, None
            k.spend, k.cpu, k.mem, k.burn, k.tokens_m = 8.10, 6, 41, .4, 14.1
            k.hist_cpu, k.hist_mem = (6, 6), (41, 2)
        case "alert":
            raw[3].state, raw[3].prompt = WAITING, PROMPT_ASK
            k.spend, k.burn, k.tokens_m, k.cpu, k.mem = 41.20, 7.8, 72.4, 92, 97
            k.hist_cpu, k.hist_mem = (88, 10), (96, 2)
            k.hist_swap = history(f"{name}:swap", 80, 6, 28) + [86.0, 88.0]
            k.swap = 88
        case "empty":
            raw = []
        case "many":
            raw = raw + _many_extra()
        case "cold":
            loading = Loading(done=0, total=1284)
        case "first":
            first_run, raw = True, []
        case "notmux":
            tmux_ok, raw = False, []
        case "noswap":
            k.swap = None

    taken: set[str] = set()
    sessions = sort_sessions([_to_session(s, now, taken) for s in raw])
    if first_run:
        spend = _first_run_spend(now, budget)
    elif loading is not None:
        spend = SpendState()            # unknown until the first scan finishes
    else:
        spend = _spend(k, now, budget)
    return PanelState(
        system=_system(name, k, now, th),
        spend=spend,
        sessions=SessionsState(items=sessions, tmux_ok=tmux_ok, terminal_ok=True,
                               updated_at=now),
        journal=JournalState(monthly_log_path=monthly_log_path(now)) if first_run
        else _journal(now),
        app=AppState(version=__version__, loading=loading, first_run=first_run, demo=name),
        settings=_settings(),
    )


def loaded_spend(now: float | None = None, budget: float | None = DEMO_BUDGET) -> SpendState:
    """What the cold scenario's spend becomes once its simulated scan completes."""
    now = time.time() if now is None else float(now)
    return _spend(_Knobs(), now, budget)
