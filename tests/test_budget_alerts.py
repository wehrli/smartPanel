"""SP-0902: budget notifications fire at 80 % and 100 %, once each per local day."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from smartpanel.claude_stats.budget import BUDGET_80, BUDGET_100, BudgetAlerts, budget_message

D1, D2 = date(2026, 11, 17), date(2026, 11, 18)


def test_nothing_without_a_budget():
    a = BudgetAlerts()
    assert a.update(500.0, None, D1) == []
    assert a.update(500.0, 0, D1) == []
    assert a.update(None, 40.0, D1) == []


def test_80_then_100_once_each():
    a = BudgetAlerts()
    assert a.update(10.0, 40.0, D1) == []
    assert a.update(32.0, 40.0, D1) == [BUDGET_80]
    assert a.update(35.0, 40.0, D1) == []
    assert a.update(40.0, 40.0, D1) == [BUDGET_100]
    assert a.update(55.0, 40.0, D1) == []


def test_jumping_straight_past_100_sends_only_100():
    a = BudgetAlerts()
    assert a.update(45.0, 40.0, D1) == [BUDGET_100]
    assert a.update(45.0, 40.0, D1) == []


def test_resets_on_a_new_local_day():
    a = BudgetAlerts()
    assert a.update(45.0, 40.0, D1) == [BUDGET_100]
    assert a.update(1.0, 40.0, D2) == []
    assert a.update(33.0, 40.0, D2) == [BUDGET_80]


def test_raising_the_budget_mid_day_does_not_refire():
    a = BudgetAlerts()
    assert a.update(33.0, 40.0, D1) == [BUDGET_80]
    assert a.update(33.0, 100.0, D1) == []
    assert a.update(81.0, 100.0, D1) == []        # 80 already sent today


def test_messages():
    now = datetime(2026, 11, 17, 14, 32, tzinfo=ZoneInfo("Europe/Paris"))
    title, body = budget_message(BUDGET_80, 32.0, 40.0, 3.10, now)
    assert title == "80% of your daily budget used"
    assert body == "$32.00 of $40.00 spent today · at $3.10/h you reach it at 17:07"
    title, body = budget_message(BUDGET_100, 46.5, 40.0, 3.10, now)
    assert title == "Daily budget reached"
    assert body == "$46.50 of $40.00 spent today · over by $6.50"
