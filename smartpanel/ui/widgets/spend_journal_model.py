"""Pure presentation for the Spend and Journal pages (SP-0810, SP-0904, SP-1003..1006,
SP-1102, SP-1103, SP-1803..1805, SP-2602, SP-2604).

GTK-free, so every string the two pages show is unit-testable without a display.
The GTK side (`pages/spend.py`, `pages/journal.py`, `spend_journal_receipt.py`)
only lays these out.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime

from smartpanel.state.model import OUTCOME_JOURNALED, DaySpend, JournalEntry

# ───────────────────────── copy ─────────────────────────

ESTIMATE_NOTE = ("Estimated from the text length. Transcripts record tokens per message, "
                 "not per sentence.")
LOADING_TITLE = "Reading your transcripts"
FIRST_RUN_TITLE = "No Claude Code usage yet"
FIRST_RUN_TEXT = ("smartPanel reads the transcripts Claude Code writes to ~/.claude/projects/. "
                  "Start a session and the first receipt appears within 15 seconds.")
NO_DAYS_TITLE = "No spend figures yet"
NO_DAYS_TEXT = "smartPanel hasn’t finished reading your transcripts. Figures appear here shortly."
JOURNAL_EMPTY_TITLE = "No journal entries yet"
UNREADABLE = "Couldn’t read this receipt"
UNREADABLE_DETAIL = ("The receipt file exists but couldn’t be parsed. Open it to see what "
                     "it holds.")
BADGE_OK = "✓ JOURNALED"
BADGE_RECEIPT = "RECEIPT ONLY"
BADGE_UNREADABLE = "UNREADABLE"


# ───────────────────────── numbers ─────────────────────────

def money(v: float | None) -> str:
    """$12.40, or an em dash when unknown (never $0.00 for 'unknown')."""
    return "—" if v is None else f"${v:,.2f}"


def amount(v: float | None) -> str:
    """Receipt / day-list amount: no currency sign, two decimals."""
    return "—" if v is None else f"{v:,.2f}"


def percent(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f}%"


def human_tokens(n: int | None) -> str:
    n = int(n or 0)
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}k"
    return str(n)


def cents(v: float | None) -> int:
    return int(round((v or 0.0) * 100))


def thousands(n: int) -> str:
    return f"{int(n):,}"


# ───────────────────────── spend: loading ─────────────────────────

def loading_text(done: int, total: int) -> str:
    if total <= 0:
        return "Looking for session files in ~/.claude/projects…"
    return (f"{thousands(min(done, total))} of {thousands(total)} session files in "
            "~/.claude/projects. Later refreshes only re-read files that changed.")


def loading_fraction(done: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(done / total, 1.0))


# ───────────────────────── spend: day list ─────────────────────────

def _parse_date(iso: str) -> date | None:
    try:
        return date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def short_date(iso: str) -> str:
    """'2026-11-17' -> 'Tue 17 Nov' ('' when unparseable)."""
    d = _parse_date(iso)
    return f"{d:%a} {d.day} {d:%b}" if d else ""


@dataclass(frozen=True)
class DayRow:
    date: str
    label: str           # "Today", "Mon 16 Nov"
    sub: str             # the date next to "Today"/"Yesterday", else ""
    total: str           # "12.40"
    fraction: float      # bar width, scaled to the biggest day
    a11y: str


def day_rows(days: list[DaySpend]) -> list[DayRow]:
    biggest = max((d.cost_usd for d in days), default=0.0)
    rows = []
    for d in days:
        relative = d.label in ("Today", "Yesterday")
        sub = short_date(d.date) if relative else ""
        frac = (d.cost_usd / biggest) if biggest > 0 else 0.0
        name = f"{d.label} {sub}".strip()
        rows.append(DayRow(date=d.date, label=d.label, sub=sub, total=amount(d.cost_usd),
                           fraction=max(0.0, min(frac, 1.0)),
                           a11y=f"{name}, {money(d.cost_usd)}"))
    return rows


def pick_day(days: list[DaySpend], current: str | None) -> int:
    """Index of the selected day: keep `current` (an ISO date) if still listed,
    else Today (the first row)."""
    if current is not None:
        for i, d in enumerate(days):
            if d.date == current:
                return i
    return 0


# ───────────────────────── spend: receipt ─────────────────────────

@dataclass(frozen=True)
class ReceiptModel:
    date_line: str                                   # "TUE 17 NOV 2026 · 14:32"
    empty: bool = False                              # "NO USAGE THIS DAY"
    projects: tuple[tuple[str, str], ...] = ()       # (name, amount)
    models: tuple[tuple[str, str], ...] = ()
    total: str = "0.00"
    praise: str | None = None                        # incl. compliments
    echo: str | None = None                          # echo of them later
    budget: str | None = None                        # daily budget
    budget_rest: tuple[str, str] | None = None       # ("left" | "over by", amount)
    over: bool = False
    footer: str = ""                                 # "41.2M TOKENS · THANK YOU"

    @property
    def has_praise(self) -> bool:
        return self.praise is not None or self.echo is not None


def receipt_date_line(day: DaySpend, printed_at: float | None = None) -> str:
    d = _parse_date(day.date)
    head = f"{d:%a} {d.day} {d:%b} {d.year}".upper() if d else day.label.upper()
    if day.label == "Today" and printed_at:
        head += f" · {datetime.fromtimestamp(printed_at):%H:%M}"
    return head


def receipt_model(day: DaySpend, *, budget: float | None = None,
                  printed_at: float | None = None) -> ReceiptModel:
    date_line = receipt_date_line(day, printed_at)
    if cents(day.cost_usd) == 0 and not day.tokens:
        return ReceiptModel(date_line=date_line, empty=True)
    projects = tuple((p.name, amount(p.cost_usd)) for p in day.projects
                     if cents(p.cost_usd) or p.tokens)
    models = tuple((m.family, amount(m.cost_usd)) for m in day.models
                   if cents(m.cost_usd) or m.tokens)
    praise = amount(day.praise_cost_usd) if cents(day.praise_cost_usd) else None
    echo = amount(day.praise_echo_cost_usd) if cents(day.praise_echo_cost_usd) else None
    if echo is not None and praise is None:
        praise = amount(day.praise_cost_usd)   # the echo hangs off the compliments line
    budget_s = rest = None
    over = False
    if budget is not None and budget > 0:
        budget_s = amount(budget)
        diff = budget - day.cost_usd
        over = cents(diff) < 0
        rest = ("over by", amount(-diff)) if over else ("left", amount(diff))
    return ReceiptModel(
        date_line=date_line, projects=projects, models=models,
        total=amount(day.cost_usd), praise=praise, echo=echo,
        budget=budget_s, budget_rest=rest, over=over,
        footer=f"{human_tokens(day.tokens)} TOKENS · THANK YOU")


def budget_for(state) -> float | None:
    """The daily budget the receipts compare against: the forecast's (what the
    spend service uses), else the setting."""
    b = state.spend.forecast.budget_usd
    if b is None:
        b = (state.settings.values or {}).get("budget.daily_usd")
    try:
        return float(b) if b is not None and float(b) > 0 else None
    except (TypeError, ValueError):
        return None


# ───────────────────────── journal ─────────────────────────

def duration(seconds: int | None) -> str:
    """9000 -> '2h30m', 1500 -> '25m', 40 -> '40s', None -> '—'."""
    if seconds is None:
        return "—"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h, r = divmod(m, 60)
    return f"{h}h{r:02d}m"


def clock(t: float) -> str:
    return f"{datetime.fromtimestamp(t):%H:%M}" if t else "--:--"


def day_word(t: float, now: float) -> str:
    """'today', 'yesterday', or 'Mon 16 Nov'."""
    if not t:
        return ""
    d, today = datetime.fromtimestamp(t).date(), datetime.fromtimestamp(now).date()
    delta = (today - d).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    return short_date(d.isoformat())


def tools_text(tools: dict[str, int]) -> str:
    if not tools:
        return "None"
    ranked = sorted(tools.items(), key=lambda kv: (-kv[1], kv[0]))
    return " · ".join(f"{k}\u00a0{v}" for k, v in ranked)   # no break inside a pair


def is_ok(e: JournalEntry) -> bool:
    return e.readable and e.outcome == OUTCOME_JOURNALED


def badge(e: JournalEntry) -> tuple[str, str]:
    """(text, css suffix 'ok' | 'fail')."""
    if not e.readable:
        return BADGE_UNREADABLE, "fail"
    return (BADGE_OK, "ok") if is_ok(e) else (BADGE_RECEIPT, "fail")


def entry_title(e: JournalEntry) -> str:
    if not e.readable and not e.title:
        return UNREADABLE
    return e.title or e.project or e.id


@dataclass(frozen=True)
class JournalRow:
    id: str
    title: str
    badge: str
    badge_css: str
    meta: str           # "12:02 · tacomap · 2h30m"
    a11y: str


def journal_row(e: JournalEntry) -> JournalRow:
    text, css = badge(e)
    if e.readable:
        parts = [clock(e.time), e.project, duration(e.duration_s)]
        meta = " · ".join(p for p in parts if p and p != "—")
    else:
        meta = UNREADABLE
    title = entry_title(e)
    return JournalRow(id=e.id, title=title, badge=text, badge_css=css, meta=meta,
                      a11y=f"{title}, {text.replace('✓ ', '').lower()}, {meta}")


@dataclass(frozen=True)
class JournalDetail:
    id: str
    cap: str                    # "tacomap · today 12:02"
    title: str
    quote: str
    failed: bool
    readable: bool
    activity: tuple[tuple[str, str], ...] = ()
    path: str = ""              # home-shortened receipt path
    receipt_file: str = ""      # expanded path, for opening
    log_name: str | None = None  # "JOURNAL/2026-11.md"
    log_file: str | None = None


def home_short(path: str) -> str:
    home = os.path.expanduser("~")
    p = os.path.expanduser(path)
    if p == home:
        return "~"
    if p.startswith(home + os.sep):
        return "~" + p[len(home):]
    return path


def log_name(path: str | None) -> str | None:
    """'~/dev/omdev/JOURNAL/2026-11.md' -> 'JOURNAL/2026-11.md'."""
    if not path:
        return None
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    return "/".join(parts[-2:]) if parts else None


def journal_detail(e: JournalEntry, now: float, monthly_log: str | None = None) -> JournalDetail:
    when = " ".join(p for p in (day_word(e.time, now), clock(e.time) if e.time else "") if p)
    cap = " · ".join(p for p in (e.project, when) if p)
    if not e.readable:
        quote = UNREADABLE_DETAIL
        activity: tuple[tuple[str, str], ...] = ()
    else:
        quote = e.confirmation or "No confirmation line was recorded."
        activity = (
            ("Duration", duration(e.duration_s)),
            ("Files", "\n".join(e.files) if e.files else "None"),
            ("Commands", "\n".join(e.commands) if e.commands else "None"),
            ("Tools", tools_text(e.tools)),
        )
    return JournalDetail(
        id=e.id, cap=cap, title=entry_title(e), quote=quote,
        failed=not is_ok(e), readable=e.readable, activity=activity,
        path=home_short(e.path) if e.path else "",
        receipt_file=os.path.expanduser(e.path) if e.path else "",
        log_name=log_name(monthly_log),
        log_file=os.path.expanduser(monthly_log) if monthly_log else None)


def min_age_phrase(seconds) -> str:
    """120 -> '2 minutes', 60 -> '1 minute', 90 -> '90 seconds'."""
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        s = 120
    s = max(0, s)
    if s and s % 60 == 0:
        m = s // 60
        return f"{m} minute" + ("" if m == 1 else "s")
    return f"{s} second" + ("" if s == 1 else "s")


def journal_empty_text(min_age_s, journal_on: bool = True) -> str:
    if not journal_on:
        return ("Journaling before closing is off. Turn it on in Settings and smartPanel "
                "writes an entry here each time you close a session.")
    try:
        zero = int(float(min_age_s)) <= 0
    except (TypeError, ValueError):
        zero = False
    if zero:
        return "Close a session and smartPanel writes one here."
    return (f"Close a session that has been open for more than {min_age_phrase(min_age_s)} "
            "and smartPanel writes one here.")


def setting(state, config, key: str, default=None):
    """Effective setting: the store's mirrored settings first, then the Config."""
    values = getattr(getattr(state, "settings", None), "values", None) or {}
    if key in values and values[key] is not None:
        return values[key]
    if config is not None:
        try:
            v = config.get(key)
            if v is not None:
                return v
        except Exception:  # noqa: BLE001 — a missing key is just the default
            pass
    return default

