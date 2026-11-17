"""SP-0901: the pure budget forecast."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from smartpanel.claude_stats.forecast import forecast
from smartpanel.state.model import Forecast

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 11, 17, 14, 32, tzinfo=PARIS)


def test_no_budget_is_an_empty_forecast():
    assert forecast(23.84, None, 3.10, NOW) == Forecast()
    assert forecast(23.84, 0, 3.10, NOW) == Forecast()
    assert forecast(23.84, "garbage", 3.10, NOW) == Forecast()


def test_eta_is_local_hh_mm_today():
    # 16.16 left at 3.10/h = 5.2129 h -> 19:44.8 -> 19:45 (the prototype's figure)
    fc = forecast(23.84, 40.0, 3.10, NOW)
    assert fc.budget_usd == 40.0
    assert fc.pct == pytest.approx(59.6)
    assert fc.eta == "19:45"
    assert fc.eta_reason is None
    assert fc.over_by_usd == 0.0


def test_zero_burn_never_runs_out():
    fc = forecast(10.0, 40.0, 0.0, NOW)
    assert fc.eta is None and fc.eta_reason == "never"
    assert forecast(10.0, 40.0, None, NOW).eta_reason == "never"


def test_already_over():
    fc = forecast(46.5, 40.0, 3.10, NOW)
    assert fc.eta is None and fc.eta_reason == "over"
    assert fc.over_by_usd == pytest.approx(6.5)
    assert fc.pct == pytest.approx(116.25)


def test_exactly_at_budget_counts_as_reached():
    fc = forecast(40.0, 40.0, 3.10, NOW)
    assert fc.eta_reason == "over" and fc.over_by_usd == 0.0


def test_crossing_midnight_is_tomorrow():
    late = datetime(2026, 11, 17, 22, 0, tzinfo=PARIS)
    fc = forecast(30.0, 40.0, 3.0, late)          # 3.33 h -> 01:20 next day
    assert fc.eta is None and fc.eta_reason == "tomorrow"


def test_tiny_burn_does_not_overflow():
    fc = forecast(1.0, 40.0, 1e-12, NOW)
    assert fc.eta_reason == "tomorrow"


def test_rounding_to_2400_rolls_into_tomorrow():
    late = datetime(2026, 11, 17, 23, 59, 40, tzinfo=PARIS)
    fc = forecast(39.99, 40.0, 3.6, late)         # +10 s -> 23:59:50 -> rounds to 00:00
    assert fc.eta_reason == "tomorrow"


def test_naive_now_is_wall_clock():
    fc = forecast(0.0, 10.0, 5.0, datetime(2026, 11, 17, 9, 0))
    assert fc.eta == "11:00"


def test_eta_across_a_dst_change_uses_real_elapsed_time():
    # 29 Mar 2026 Paris: 02:00 CET -> 03:00 CEST. 01:30 + 1 h of burn = 03:30 local.
    early = datetime(2026, 3, 29, 1, 30, tzinfo=PARIS)
    assert forecast(0.0, 1.0, 1.0, early).eta == "03:30"


def test_utc_now_converts_back_to_its_own_zone():
    utc_now = datetime(2026, 11, 17, 13, 32, tzinfo=timezone.utc)
    assert forecast(0.0, 1.0, 1.0, utc_now).eta == "14:32"
