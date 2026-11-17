"""Snapshot -> `state.SpendState` (SP-0804, SP-1002, SP-1101). Pure and GTK-free.

The aggregator speaks Buckets; every v2 surface reads `SpendState`. This is the one
place the two meet, so the tray, the dashboard, D-Bus and `--praise-report` all see
the same numbers (SP-0811).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from smartpanel.claude_stats.aggregator import Bucket, DayBucket, Snapshot
from smartpanel.claude_stats.forecast import forecast
from smartpanel.render import humanize_tokens
from smartpanel.state.model import DaySpend, ModelSpend, ProjectSpend, SpendState

DAYS_SHOWN = 14

# Fixed English names: labels must not change with the process locale.
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def day_label(d: date, today: date) -> str:
    """'Today', 'Yesterday', else 'Mon 16 Nov'."""
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"


def projects(per_project: dict[str, Bucket]) -> list[ProjectSpend]:
    rows = [ProjectSpend(name=n, cost_usd=b.cost_usd, tokens=b.tokens_total)
            for n, b in per_project.items()]
    rows.sort(key=lambda r: (-r.cost_usd, r.name))
    return rows


def models(per_model: dict[str, Bucket]) -> list[ModelSpend]:
    rows = [ModelSpend(family=n, cost_usd=b.cost_usd, tokens=b.tokens_total)
            for n, b in per_model.items()]
    rows.sort(key=lambda r: (-r.cost_usd, r.family))
    return rows


def day_spend(d: date, bucket: DayBucket | None, today: date) -> DaySpend:
    if bucket is None:
        return DaySpend(date=d.isoformat(), label=day_label(d, today))
    t = bucket.total
    return DaySpend(
        date=d.isoformat(),
        label=day_label(d, today),
        cost_usd=t.cost_usd,
        tokens=t.tokens_total,
        projects=projects(bucket.per_project),
        models=models(bucket.per_model),
        praise_cost_usd=t.praise_cost_usd,
        praise_echo_cost_usd=t.praise_echo_cost_usd,
    )


def days(snap: Snapshot, count: int = DAYS_SHOWN, end: date | None = None) -> list[DaySpend]:
    """`count` consecutive local days ending at `end` (default today), newest first.

    Days without usage are included as zeros. Older days than the aggregator
    keeps (DAYS_KEPT) come back as zeros too, never as an error.
    """
    today = snap.today_date or date.today()
    end = end or today
    return [day_spend(d, snap.per_day.get(d), today)
            for d in (end - timedelta(days=i) for i in range(count))]


def build_spend_state(snap: Snapshot, budget_usd: float | None = None,
                      updated_at: float | None = None) -> SpendState:
    """The whole `spend` section. `snap.files_total == 0` (first run) leaves the
    money figures `None` — nothing is known, which is not the same as $0."""
    now: datetime = snap.now or datetime.now().astimezone()
    if snap.files_total == 0 and not snap.lifetime.tokens_total:
        return SpendState(
            forecast=forecast(None, budget_usd, None, now),
            days=days(snap),
            updated_at=updated_at,
        )
    t = snap.today
    has_cache_denominator = (t.tokens_cache_read + t.tokens_input + t.tokens_cache_write) > 0
    return SpendState(
        cost_today=t.cost_usd,
        cost_week=snap.week.cost_usd,
        cost_lifetime=snap.lifetime.cost_usd,
        tokens_today=t.tokens_total,
        tokens_today_human=humanize_tokens(t.tokens_total),
        burn_per_hour=snap.burn_rate_per_hour,
        cache_hit_pct=t.cache_hit_ratio if has_cache_denominator else None,
        hourly_today=list(snap.hourly_today),
        projects_today=projects(snap.per_project_today),
        models_today=models(snap.per_model_today),
        praise_cost_today=t.praise_cost_usd,
        praise_echo_today=t.praise_echo_cost_usd,
        days=days(snap),
        forecast=forecast(t.cost_usd, budget_usd, snap.burn_rate_per_hour, now),
        updated_at=updated_at,
    )
