"""Daily-budget forecast (SP-0901): pure, GTK-free, unit-tested.

    forecast(spent, budget, burn_per_hour, now) -> state.Forecast

- No budget (None or <= 0)       -> Forecast() — every surface hides the budget.
- Already at / over the budget   -> eta None, eta_reason "over", over_by_usd > 0.
- No burn (None or <= 0)          -> eta None, eta_reason "never".
- Runs out after local midnight   -> eta None, eta_reason "tomorrow".
- Otherwise                       -> eta "HH:MM", local wall-clock time today.

`now` is the local time the figures were taken at. An aware datetime is advanced
in UTC and converted back, so a DST change between now and the ETA is honoured;
a naive one is treated as local wall-clock time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from smartpanel.state.model import Forecast

NEVER = "never"
TOMORROW = "tomorrow"
OVER = "over"


def forecast(spent: float | None, budget: float | None,
             burn_per_hour: float | None, now: datetime) -> Forecast:
    if budget is None:
        return Forecast()
    try:
        budget = float(budget)
    except (TypeError, ValueError):
        return Forecast()
    if budget <= 0:
        return Forecast()
    spent = float(spent or 0.0)
    pct = spent / budget * 100.0

    if spent >= budget:
        return Forecast(budget_usd=budget, pct=pct, eta=None, eta_reason=OVER,
                        over_by_usd=spent - budget)
    if not burn_per_hour or burn_per_hour <= 0:
        return Forecast(budget_usd=budget, pct=pct, eta=None, eta_reason=NEVER)

    hours = (budget - spent) / float(burn_per_hour)
    # A burn so small the ETA overflows datetime is just "not today".
    if hours >= 48:
        return Forecast(budget_usd=budget, pct=pct, eta=None, eta_reason=TOMORROW)
    step = timedelta(hours=hours)
    if now.tzinfo is not None:
        at = (now.astimezone(timezone.utc) + step).astimezone(now.tzinfo)
    else:
        at = now + step
    # Round to the nearest minute, as the UI shows HH:MM.
    at = (at + timedelta(seconds=30)).replace(second=0, microsecond=0)
    if at.date() != now.date():
        return Forecast(budget_usd=budget, pct=pct, eta=None, eta_reason=TOMORROW)
    return Forecast(budget_usd=budget, pct=pct, eta=at.strftime("%H:%M"), eta_reason=None)
