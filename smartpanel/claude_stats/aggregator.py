"""Sum Claude Code usage into today / week / lifetime / per-day / per-hour buckets.

Day boundaries are **local** midnights (SP-0802): "today" in Paris starts at 00:00
Paris time, not at 00:00 UTC. The timezone is injectable (`tz=`) so tests can pin one;
`None` means the system's local zone, resolved per instant (DST-correct, honours TZ).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Callable

from smartpanel.claude_stats.parser import (
    UsageEvent,
    load_usage_events,
    project_display_name,
)
from smartpanel.claude_stats.pricing import cache_read_cost, estimate_cost, output_cost

log = logging.getLogger(__name__)

# Substring → display name for model families. Order matters (first match wins).
_MODEL_FAMILIES = [
    ("opus", "Opus"),
    ("sonnet", "Sonnet"),
    ("haiku", "Haiku"),
]


# Per-day buckets kept in the snapshot (the window shows 14; older on demand).
DAYS_KEPT = 90

ProgressFn = Callable[[int, int], None]


def model_family(model_id: str) -> str:
    low = (model_id or "").lower()
    for needle, name in _MODEL_FAMILIES:
        if needle in low:
            return name
    return "Other"


_model_family = model_family   # legacy name


@dataclass
class Bucket:
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_cache_write: int = 0
    tokens_cache_read: int = 0
    cost_usd: float = 0.0
    # Gratification ("Good question", "You're absolutely right") — see flattery.py.
    praise_phrases: int = 0
    praise_tokens: int = 0
    praise_cost_usd: float = 0.0        # emitting it, at output rates
    praise_echo_cost_usd: float = 0.0   # re-reading it on every later turn
    praise_by_label: dict[str, int] = field(default_factory=dict)

    @property
    def tokens_total(self) -> int:
        return (self.tokens_input + self.tokens_output
                + self.tokens_cache_write + self.tokens_cache_read)

    @property
    def cache_hit_ratio(self) -> float:
        read = self.tokens_cache_read
        denom = read + self.tokens_input + self.tokens_cache_write
        return (read / denom * 100.0) if denom else 0.0

    @property
    def praise_total_cost(self) -> float:
        """What deleting every compliment would have saved."""
        return self.praise_cost_usd + self.praise_echo_cost_usd

    @property
    def praise_share(self) -> float:
        """Gratification as a percentage of output tokens."""
        return (self.praise_tokens / self.tokens_output * 100.0) if self.tokens_output else 0.0

    @property
    def praise_cost_share(self) -> float:
        """Gratification as a percentage of total spend."""
        return (self.praise_total_cost / self.cost_usd * 100.0) if self.cost_usd else 0.0

    def add(self, ev: UsageEvent) -> None:
        self.tokens_input += ev.input_tokens
        self.tokens_output += ev.output_tokens
        self.tokens_cache_write += ev.cache_write_tokens
        self.tokens_cache_read += ev.cache_read_tokens
        self.cost_usd += estimate_cost(
            ev.model, ev.input_tokens, ev.output_tokens,
            ev.cache_write_tokens, ev.cache_read_tokens,
        )
        if not ev.praise_phrases:
            return
        self.praise_phrases += ev.praise_phrases
        self.praise_tokens += ev.praise_tokens
        self.praise_cost_usd += output_cost(ev.model, ev.praise_tokens)
        # Echo: those tokens sit in the prompt for every remaining turn of the
        # session. Priced at the cache-read rate — the cheap, conservative
        # assumption; an uncached re-send costs 10x that.
        self.praise_echo_cost_usd += cache_read_cost(
            ev.model, ev.praise_tokens * ev.later_requests
        )
        for label, n in ev.praise_by_label:
            self.praise_by_label[label] = self.praise_by_label.get(label, 0) + n


@dataclass
class DayBucket:
    """One local calendar day: the total plus its project and model splits."""
    total: Bucket = field(default_factory=Bucket)
    per_project: dict[str, Bucket] = field(default_factory=dict)
    per_model: dict[str, Bucket] = field(default_factory=dict)

    def add(self, ev: UsageEvent, family: str) -> None:
        self.total.add(ev)
        self.per_project.setdefault(ev.project, Bucket()).add(ev)
        self.per_model.setdefault(family, Bucket()).add(ev)


@dataclass
class Snapshot:
    today: Bucket = field(default_factory=Bucket)
    week: Bucket = field(default_factory=Bucket)
    lifetime: Bucket = field(default_factory=Bucket)
    per_project_today: dict[str, Bucket] = field(default_factory=dict)
    per_project_lifetime: dict[str, Bucket] = field(default_factory=dict)
    per_model_today: dict[str, Bucket] = field(default_factory=dict)
    burn_rate_per_hour: float = 0.0   # USD/hr over the last 60 min of wall time
    sessions_today: int = 0           # distinct .jsonl files with any event today
    # ── v2 additions ──
    now: datetime | None = None       # the local "now" this snapshot was computed at
    hourly_today: list[float] = field(default_factory=lambda: [0.0] * 24)  # USD, local hours
    # Local date -> DayBucket, for the last DAYS_KEPT days (today included). Days
    # without usage are absent here; the state layer fills them with zeros.
    per_day: dict[date, DayBucket] = field(default_factory=dict)
    # Transcript path (str, as scanned) -> USD charged to that file after the
    # cross-file message-id dedupe. What `SpendService.session_cost` answers from.
    per_file_cost: dict[str, float] = field(default_factory=dict)
    files_total: int = 0              # *.jsonl files seen (0 == first run)

    @property
    def today_date(self) -> date | None:
        return self.now.date() if self.now else None


class Aggregator:
    """Walks ~/.claude/projects/*/ and accumulates usage events.

    Caches per-file (path -> last mtime, [events]) so repeat scans only
    re-parse changed files. Not thread-safe: one `refresh()` at a time — the
    v2 `SpendService` runs it on a worker thread and never overlaps two.
    """

    def __init__(self, claude_root: Path | None = None,
                 now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 tz: tzinfo | None = None):
        self.root = Path(claude_root) if claude_root else Path.home() / ".claude" / "projects"
        self._now = now_fn
        self.tz = tz
        # path -> (mtime_ns, list[UsageEvent])
        self._file_cache: dict[Path, tuple[int, list[UsageEvent]]] = {}

    # ── local time ──
    def to_local(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self.tz) if self.tz is not None else dt.astimezone()

    def local_midnight(self, d: date) -> datetime:
        if self.tz is not None:
            return datetime.combine(d, time(), tzinfo=self.tz)
        return datetime.combine(d, time()).astimezone()   # naive == system local

    def local_now(self) -> datetime:
        return self.to_local(self._now())

    # ── scanning ──
    def list_files(self) -> list[tuple[str, Path, int]]:
        """Every transcript as (project display name, path, mtime_ns).

        Ordered oldest-modified first, so that when a resumed session copies the
        earlier conversation into a new file, the *original* transcript is the
        "first file" and keeps the charge for the copied messages.
        """
        files: list[tuple[str, Path, int]] = []
        try:
            project_dirs = sorted(self.root.iterdir())
        except OSError:
            log.debug("no claude projects dir at %s", self.root)
            return files
        for project_dir in project_dirs:
            if not project_dir.is_dir():
                continue
            project = project_display_name(project_dir.name)
            for jsonl in project_dir.glob("*.jsonl"):
                try:
                    mtime_ns = jsonl.stat().st_mtime_ns
                except OSError:
                    continue
                files.append((project, jsonl, mtime_ns))
        files.sort(key=lambda f: (f[2], str(f[1])))
        return files

    def refresh(self, progress: ProgressFn | None = None) -> Snapshot:
        """Scan (re-parsing only changed files) and summarise.

        `progress(done, total)` is called after each file — from the calling
        thread; the caller marshals it to the UI.
        """
        files = self.list_files()
        total = len(files)
        per_file: list[tuple[Path, list[UsageEvent]]] = []
        live: set[Path] = set()
        for done, (project, jsonl, mtime_ns) in enumerate(files, 1):
            live.add(jsonl)
            cached = self._file_cache.get(jsonl)
            if cached and cached[0] == mtime_ns:
                file_events = cached[1]
            else:
                fallback = datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc)
                file_events = load_usage_events(jsonl, project, fallback_ts=fallback)
                self._file_cache[jsonl] = (mtime_ns, file_events)
            per_file.append((jsonl, file_events))
            if progress is not None:
                try:
                    progress(done, total)
                except Exception:  # noqa: BLE001 — a UI hiccup must not stop the scan
                    log.debug("progress callback failed", exc_info=True)
        # Forget deleted transcripts so the cache can't grow without bound.
        for gone in set(self._file_cache) - live:
            del self._file_cache[gone]
        snap = self._summarize_files(per_file)
        snap.files_total = total
        return snap

    def _summarize(self, events: list[UsageEvent], sessions_today: int) -> Snapshot:
        """Legacy entry point: summarise a flat event list (no per-file costs)."""
        snap = self._summarize_files([(None, events)])
        snap.sessions_today = sessions_today
        return snap

    def _summarize_files(self, per_file) -> Snapshot:
        now = self.local_now()
        today = now.date()
        today_start = self.local_midnight(today)
        week_start = self.local_midnight(today - timedelta(days=6))
        days_start = self.local_midnight(today - timedelta(days=DAYS_KEPT - 1))
        burn_window_start = now - timedelta(hours=1)
        snap = Snapshot(now=now)

        per_proj_today: dict[str, Bucket] = defaultdict(Bucket)
        per_proj_life: dict[str, Bucket] = defaultdict(Bucket)
        per_model_today: dict[str, Bucket] = defaultdict(Bucket)
        per_day: dict[date, DayBucket] = {}
        hourly = [0.0] * 24
        burn_total = 0.0
        sessions_today = 0
        # Resuming a session copies the prior conversation into the new
        # transcript, so the same request can appear in several files. Charge
        # each `message.id` once — first file wins. Events without an id
        # (older transcripts) are always distinct.
        seen_ids: set[str] = set()

        for path, events in per_file:
            file_cost = 0.0
            touched_today = False
            for ev in events:
                if ev.message_id:
                    if ev.message_id in seen_ids:
                        continue
                    seen_ids.add(ev.message_id)
                cost = estimate_cost(
                    ev.model, ev.input_tokens, ev.output_tokens,
                    ev.cache_write_tokens, ev.cache_read_tokens,
                )
                file_cost += cost
                snap.lifetime.add(ev)
                per_proj_life[ev.project].add(ev)
                ts = ev.timestamp
                if ts < days_start:
                    continue
                local = self.to_local(ts)
                family = model_family(ev.model)
                day = per_day.get(local.date())
                if day is None:
                    day = per_day[local.date()] = DayBucket()
                day.add(ev, family)
                if ts >= week_start:
                    snap.week.add(ev)
                if ts >= today_start and local.date() == today:
                    touched_today = True
                    snap.today.add(ev)
                    per_proj_today[ev.project].add(ev)
                    per_model_today[family].add(ev)
                    hourly[local.hour] += cost
                if ts >= burn_window_start:
                    burn_total += cost
            if path is not None:
                snap.per_file_cost[str(path)] = file_cost
            if touched_today:
                sessions_today += 1

        snap.per_project_today = dict(per_proj_today)
        snap.per_project_lifetime = dict(per_proj_life)
        snap.per_model_today = dict(per_model_today)
        snap.per_day = per_day
        snap.hourly_today = hourly
        snap.burn_rate_per_hour = burn_total
        snap.sessions_today = sessions_today
        return snap

    def _today_start(self) -> datetime:
        return self.local_midnight(self.local_now().date())
