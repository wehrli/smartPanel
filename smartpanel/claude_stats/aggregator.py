from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def _model_family(model_id: str) -> str:
    low = (model_id or "").lower()
    for needle, name in _MODEL_FAMILIES:
        if needle in low:
            return name
    return "Other"


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
class Snapshot:
    today: Bucket = field(default_factory=Bucket)
    week: Bucket = field(default_factory=Bucket)
    lifetime: Bucket = field(default_factory=Bucket)
    per_project_today: dict[str, Bucket] = field(default_factory=dict)
    per_project_lifetime: dict[str, Bucket] = field(default_factory=dict)
    per_model_today: dict[str, Bucket] = field(default_factory=dict)
    burn_rate_per_hour: float = 0.0   # USD/hr over the last 60 min of wall time
    sessions_today: int = 0           # distinct .jsonl files with any event today


class Aggregator:
    """Walks ~/.claude/projects/*/ and accumulates usage events.

    Caches per-file (path -> last mtime, [events]) so repeat scans only
    re-parse changed files. Designed to be called on a slow tick (every 30s)
    from the main loop — first call is the expensive one.
    """

    def __init__(self, claude_root: Path | None = None,
                 now_fn=lambda: datetime.now(timezone.utc)):
        self.root = (claude_root or Path.home() / ".claude" / "projects")
        self._now = now_fn
        # path -> (mtime_ns, list[UsageEvent])
        self._file_cache: dict[Path, tuple[int, list[UsageEvent]]] = {}

    def refresh(self) -> Snapshot:
        events: list[UsageEvent] = []
        session_files_today: set[Path] = set()
        today_start = self._today_start()

        if not self.root.is_dir():
            log.debug("no claude projects dir at %s", self.root)
            return Snapshot()

        for project_dir in sorted(self.root.iterdir()):
            if not project_dir.is_dir():
                continue
            project = project_display_name(project_dir.name)
            for jsonl in project_dir.glob("*.jsonl"):
                try:
                    mtime_ns = jsonl.stat().st_mtime_ns
                except OSError:
                    continue
                cached = self._file_cache.get(jsonl)
                if cached and cached[0] == mtime_ns:
                    file_events = cached[1]
                else:
                    fallback = datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc)
                    file_events = load_usage_events(jsonl, project,
                                                    fallback_ts=fallback)
                    self._file_cache[jsonl] = (mtime_ns, file_events)
                events.extend(file_events)
                if any(ev.timestamp >= today_start for ev in file_events):
                    session_files_today.add(jsonl)

        return self._summarize(events, len(session_files_today))

    def _summarize(self, events: list[UsageEvent], sessions_today: int) -> Snapshot:
        snap = Snapshot(sessions_today=sessions_today)
        now = self._now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=6)
        burn_window_start = now - timedelta(hours=1)

        per_proj_today: dict[str, Bucket] = defaultdict(Bucket)
        per_proj_life: dict[str, Bucket] = defaultdict(Bucket)
        per_model_today: dict[str, Bucket] = defaultdict(Bucket)
        burn_total = 0.0
        # Resuming a session copies the prior conversation into the new
        # transcript, so the same request can appear in several files. Charge
        # each `message.id` once — first file wins. Events without an id
        # (older transcripts) are always distinct.
        seen_ids: set[str] = set()

        for ev in events:
            if ev.message_id:
                if ev.message_id in seen_ids:
                    continue
                seen_ids.add(ev.message_id)
            snap.lifetime.add(ev)
            per_proj_life[ev.project].add(ev)
            if ev.timestamp >= today_start:
                snap.today.add(ev)
                per_proj_today[ev.project].add(ev)
                per_model_today[_model_family(ev.model)].add(ev)
            if ev.timestamp >= week_start:
                snap.week.add(ev)
            if ev.timestamp >= burn_window_start:
                burn_total += estimate_cost(
                    ev.model, ev.input_tokens, ev.output_tokens,
                    ev.cache_write_tokens, ev.cache_read_tokens,
                )

        snap.per_project_today = dict(per_proj_today)
        snap.per_project_lifetime = dict(per_proj_life)
        snap.per_model_today = dict(per_model_today)
        snap.burn_rate_per_hour = burn_total
        return snap

    def _today_start(self) -> datetime:
        now = self._now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
