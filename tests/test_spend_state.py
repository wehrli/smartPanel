"""Snapshot -> SpendState (SP-0804, SP-1002, SP-1101) and agreement with the CLI
report and the legacy sensor (SP-0811), on the realistic transcript fixtures."""
from __future__ import annotations

from datetime import date

import pytest

from smartpanel.claude_stats.aggregator import Aggregator, Snapshot
from smartpanel.claude_stats.report import _usd, praise_report
from smartpanel.claude_stats.spend_state import build_spend_state, day_label, days
from smartpanel.render import humanize_tokens
from smartpanel.state.model import SpendState
from smartpanel.state.store import Store
from tests.fixtures import PARIS, SPEND_NOW, copy_spend_tree


@pytest.fixture
def snap(tmp_path):
    root = copy_spend_tree(tmp_path)
    return Aggregator(claude_root=root, now_fn=lambda: SPEND_NOW, tz=PARIS).refresh()


def test_day_labels():
    today = date(2026, 11, 17)
    assert day_label(today, today) == "Today"
    assert day_label(date(2026, 11, 16), today) == "Yesterday"
    assert day_label(date(2026, 11, 15), today) == "Sun 15 Nov"
    assert day_label(date(2026, 11, 9), today) == "Mon 9 Nov"


def test_headline_figures(snap):
    s = build_spend_state(snap, budget_usd=None, updated_at=123.0)
    assert s.cost_today == pytest.approx(snap.today.cost_usd)
    assert s.cost_week == pytest.approx(snap.week.cost_usd)
    assert s.cost_lifetime == pytest.approx(snap.lifetime.cost_usd)
    assert s.tokens_today == snap.today.tokens_total
    assert s.tokens_today_human == humanize_tokens(snap.today.tokens_total)
    assert s.burn_per_hour == pytest.approx(snap.burn_rate_per_hour)
    assert s.cache_hit_pct == pytest.approx(snap.today.cache_hit_ratio)
    assert s.hourly_today == snap.hourly_today and len(s.hourly_today) == 24
    assert s.updated_at == 123.0
    assert s.forecast.budget_usd is None


def test_today_splits_are_sorted_by_cost(snap):
    s = build_spend_state(snap)
    assert [p.name for p in s.projects_today] == ["ledgerly"]
    costs = [m.cost_usd for m in s.models_today]
    assert costs == sorted(costs, reverse=True)
    assert {m.family for m in s.models_today} == {"Opus", "Sonnet"}
    assert sum(costs) == pytest.approx(s.cost_today)


def test_fourteen_days_newest_first_with_zero_days(snap):
    s = build_spend_state(snap)
    assert len(s.days) == 14
    assert [d.date for d in s.days[:3]] == ["2026-11-17", "2026-11-16", "2026-11-15"]
    assert [d.label for d in s.days[:3]] == ["Today", "Yesterday", "Sun 15 Nov"]
    assert s.days[-1].date == "2026-11-04"
    sun = s.days[2]
    assert sun.cost_usd == 0.0 and sun.tokens == 0 and sun.projects == [] and sun.models == []
    nov5 = next(d for d in s.days if d.date == "2026-11-05")
    assert nov5.cost_usd > 0 and nov5.projects[0].name == "smartPanel"
    assert nov5.models[0].family == "Opus"
    assert nov5.praise_cost_usd > 0          # "Great idea."


def test_day_praise_matches_today(snap):
    s = build_spend_state(snap)
    assert s.days[0].praise_cost_usd == pytest.approx(s.praise_cost_today)
    assert s.days[0].praise_echo_cost_usd == pytest.approx(s.praise_echo_today)
    assert s.days[0].cost_usd == pytest.approx(s.cost_today)


def test_seven_day_rows_sum_to_the_week(snap):
    s = build_spend_state(snap)
    assert sum(d.cost_usd for d in s.days[:7]) == pytest.approx(s.cost_week)


def test_days_on_demand_beyond_fourteen(snap):
    more = days(snap, count=30)
    assert len(more) == 30 and more[-1].date == "2026-10-19"
    older = days(snap, count=3, end=date(2026, 7, 2))
    assert [d.cost_usd for d in older] == [0.0, 0.0, 0.0]    # past the 90-day window


def test_forecast_uses_the_budget(snap):
    s = build_spend_state(snap, budget_usd=1.0)
    assert s.forecast.budget_usd == 1.0
    assert s.forecast.pct == pytest.approx(s.cost_today * 100.0)


def test_first_run_leaves_money_unknown(tmp_path):
    empty = Aggregator(claude_root=tmp_path, now_fn=lambda: SPEND_NOW, tz=PARIS).refresh()
    s = build_spend_state(empty, budget_usd=40.0)
    assert s.cost_today is None and s.cost_lifetime is None and s.burn_per_hour is None
    assert s.cache_hit_pct is None
    assert len(s.days) == 14 and s.days[0].label == "Today"
    assert s.forecast.budget_usd == 40.0 and s.forecast.pct == 0.0


def test_state_round_trips_through_the_store(snap):
    store = Store()
    store.replace("spend", build_spend_state(snap))
    d = store.to_dict({"spend"})["spend"]
    assert d["days"][0]["label"] == "Today"
    assert isinstance(store.state.spend, SpendState)
    store.to_json()


def test_default_snapshot_still_builds():
    s = build_spend_state(Snapshot())
    assert len(s.days) == 14


# ── SP-0811: the UI and the CLI never disagree ──

def test_state_figures_match_the_praise_report(snap):
    s = build_spend_state(snap)
    report = praise_report(snap)
    assert f"Lifetime spend {_usd(s.cost_lifetime)}" in report
    rows = {line.split()[0]: line.split() for line in report.splitlines()
            if line.split()[:1] in (["today"], ["week"], ["lifetime"])}
    # columns: name phrases tokens written echoed total of-spend
    assert rows["today"][3] == _usd(s.praise_cost_today)
    assert rows["today"][4] == _usd(s.praise_echo_today)
    assert rows["today"][5] == _usd(s.praise_cost_today + s.praise_echo_today)
    assert rows["today"][3] == _usd(s.days[0].praise_cost_usd)
    week_praise = sum(d.praise_cost_usd + d.praise_echo_cost_usd for d in s.days[:7])
    assert rows["week"][5] == _usd(week_praise)


def test_state_figures_match_the_legacy_sensor(snap, monkeypatch):
    from smartpanel.sensors import claude as sensor
    monkeypatch.setitem(sensor._CACHE, "snapshot", snap)
    monkeypatch.setitem(sensor._CACHE, "ts", float("inf"))
    v = sensor.claude_stats()
    s = build_spend_state(snap)
    assert v["COST_TODAY"] == pytest.approx(s.cost_today)
    assert v["COST_WEEK"] == pytest.approx(s.cost_week)
    assert v["COST_LIFETIME"] == pytest.approx(s.cost_lifetime)
    assert v["TOKENS_TODAY_HUMAN"] == s.tokens_today_human
    assert v["BURN_RATE"] == pytest.approx(s.burn_per_hour)
    assert v["CACHE_HIT_RATIO"] == pytest.approx(s.cache_hit_pct)
