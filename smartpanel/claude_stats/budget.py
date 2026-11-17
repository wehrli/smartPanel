"""Budget notification policy (SP-0902): pure, so "once per local day" is testable.

    alerts = BudgetAlerts()
    for kind in alerts.update(spent, budget, today):   # kind: "budget_80" | "budget_100"
        notify(kind, *budget_message(kind, ...))

One notification at 80 % and one at 100 %, at most once each per local day. A tick
that jumps straight past 100 % sends only `budget_100` (and counts 80 as done — a
second bubble saying "80 %" right after "reached" would be noise). No budget, no
alerts. The fired set resets when the local date changes.
"""
from __future__ import annotations

from datetime import date

from smartpanel.claude_stats.forecast import forecast

BUDGET_80 = "budget_80"
BUDGET_100 = "budget_100"
THRESHOLDS = ((100.0, BUDGET_100), (80.0, BUDGET_80))   # highest first


class BudgetAlerts:
    def __init__(self) -> None:
        self.day: date | None = None
        self.fired: set[str] = set()

    def update(self, spent: float | None, budget: float | None, today: date) -> list[str]:
        if today != self.day:
            self.day, self.fired = today, set()
        if spent is None or budget is None:
            return []
        try:
            budget = float(budget)
        except (TypeError, ValueError):
            return []
        if budget <= 0:
            return []
        pct = float(spent) / budget * 100.0
        for threshold, kind in THRESHOLDS:
            if pct >= threshold:
                if kind in self.fired:
                    return []
                # Mark this and every lower level as done for the day.
                out = [kind]
                for t, k in THRESHOLDS:
                    if t <= threshold:
                        self.fired.add(k)
                return out
        return []


def budget_message(kind: str, spent: float, budget: float,
                   burn_per_hour: float | None, now) -> tuple[str, str]:
    """(title, body) for a budget notification."""
    if kind == BUDGET_100:
        over = spent - budget
        body = f"${spent:,.2f} of ${budget:,.2f} spent today"
        if over > 0.005:
            body += f" · over by ${over:,.2f}"
        return "Daily budget reached", body
    fc = forecast(spent, budget, burn_per_hour, now)
    body = f"${spent:,.2f} of ${budget:,.2f} spent today"
    if fc.eta:
        body += f" · at ${burn_per_hour:,.2f}/h you reach it at {fc.eta}"
    return f"{fc.pct:.0f}% of your daily budget used", body
