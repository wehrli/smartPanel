"""Local-time days, hourly buckets, per-day buckets and per-file costs (SP-0802,
SP-0803, SP-1001, SP-1007, SP-1101) on the realistic transcript fixtures."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from smartpanel.claude_stats.aggregator import DAYS_KEPT, Aggregator
from smartpanel.claude_stats.flattery import scan
from smartpanel.claude_stats.pricing import estimate_cost
from tests.fixtures import ORIGINAL, OTHER, PARIS, RESUMED, SPEND_NOW, copy_spend_tree

COST = {
    "m1": estimate_cost("claude-opus-4-7", 12, 800, 4000, 20000),
    "m2": estimate_cost("claude-opus-4-7", 6, 1200, 2000, 30000),
    "m3": estimate_cost("claude-opus-4-7", 3, 400, 1000, 60000),
    "m4": estimate_cost("claude-sonnet-4-6", 40, 600, 10000, 50000),
    "m5": estimate_cost("claude-haiku-4-5", 100, 300, 0, 5000),
    "m6": estimate_cost("claude-opus-4-7", 20, 900, 8000, 40000),
    "m7": estimate_cost("claude-sonnet-4-6", 500, 2000, 20000, 0),
}


@pytest.fixture
def snap(tmp_path):
    root = copy_spend_tree(tmp_path)
    agg = Aggregator(claude_root=root, now_fn=lambda: SPEND_NOW, tz=PARIS)
    s = agg.refresh()
    s.root = root  # handy for the per-file tests
    return s


def approx(x):
    return pytest.approx(x, rel=1e-9, abs=1e-12)


# ── SP-0802: local midnight, not UTC midnight ──

def test_today_starts_at_local_midnight(snap):
    # m1 is 23:30 Paris yesterday; m2 is 00:30 Paris today (still yesterday in UTC).
    assert snap.today.cost_usd == approx(COST["m2"] + COST["m4"] + COST["m3"])
    assert snap.per_day[date(2026, 11, 16)].total.cost_usd == approx(COST["m1"])


def test_utc_would_have_put_the_0030_message_on_the_wrong_day(tmp_path):
    root = copy_spend_tree(tmp_path)
    utc = Aggregator(claude_root=root, now_fn=lambda: SPEND_NOW, tz=timezone.utc).refresh()
    assert utc.today.cost_usd == approx(COST["m4"] + COST["m3"])
    assert utc.per_day[date(2026, 11, 16)].total.cost_usd == approx(COST["m1"] + COST["m2"])


def test_local_tz_none_uses_the_system_zone(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    try:
        root = copy_spend_tree(tmp_path)
        s = Aggregator(claude_root=root, now_fn=lambda: SPEND_NOW).refresh()
        assert s.today.cost_usd == approx(COST["m2"] + COST["m4"] + COST["m3"])
        assert s.now.utcoffset() == timedelta(hours=1)
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_a_day_boundary_in_another_zone(tmp_path):
    """Same transcripts, New York: 23:30Z on the 16th is 18:30 local on the 16th."""
    root = copy_spend_tree(tmp_path)
    ny = ZoneInfo("America/New_York")
    s = Aggregator(claude_root=root, now_fn=lambda: SPEND_NOW, tz=ny).refresh()
    assert s.now.date() == date(2026, 11, 17)
    assert s.per_day[date(2026, 11, 16)].total.cost_usd == approx(COST["m1"] + COST["m2"])


# ── SP-0803: hourly ──

def test_hourly_today_has_24_local_buckets(snap):
    assert len(snap.hourly_today) == 24
    assert snap.hourly_today[0] == approx(COST["m2"])     # 00:30 Paris
    assert snap.hourly_today[10] == approx(COST["m4"])    # 10:00 Paris
    assert snap.hourly_today[14] == approx(COST["m3"])    # 14:10 Paris
    assert sum(snap.hourly_today) == approx(snap.today.cost_usd)


def test_burn_rate_is_the_last_hour(snap):
    assert snap.burn_rate_per_hour == approx(COST["m3"])


# ── SP-1001 / SP-1002 / SP-1007: per-day buckets ──

def test_per_day_buckets_split_by_project_and_model(snap):
    today = snap.per_day[date(2026, 11, 17)]
    assert set(today.per_project) == {"ledgerly"}
    assert set(today.per_model) == {"Opus", "Sonnet"}
    assert today.per_model["Sonnet"].cost_usd == approx(COST["m4"])
    nov5 = snap.per_day[date(2026, 11, 5)]
    assert set(nov5.per_project) == {"smartPanel"}


def test_days_older_than_the_window_are_not_kept(snap):
    assert min(snap.per_day) >= date(2026, 11, 17) - timedelta(days=DAYS_KEPT - 1)
    assert date(2026, 7, 1) not in snap.per_day
    assert snap.lifetime.cost_usd == approx(sum(COST.values()))


def test_seven_days_sum_to_the_week_and_all_days_to_lifetime(snap):
    week = [snap.per_day[d].total.cost_usd
            for d in (date(2026, 11, 17) - timedelta(days=i) for i in range(7))
            if d in snap.per_day]
    assert sum(week) == approx(snap.week.cost_usd)
    in_window = sum(b.total.cost_usd for b in snap.per_day.values())
    # Only m7 (July) is older than the 90-day window.
    assert in_window + COST["m7"] == approx(snap.lifetime.cost_usd)
    tokens = sum(b.total.tokens_total for b in snap.per_day.values())
    assert tokens + (500 + 2000 + 20000) == snap.lifetime.tokens_total


def test_resumed_session_duplicates_are_counted_once(snap):
    # m1 and m2 are in both files; each is charged once.
    assert snap.lifetime.cost_usd == approx(sum(COST.values()))
    assert snap.per_day[date(2026, 11, 16)].total.cost_usd == approx(COST["m1"])


def test_per_day_praise_cost_and_echo(snap):
    today = snap.per_day[date(2026, 11, 17)].total
    # m2 "Good question." and m4 "You're absolutely right." are today.
    assert today.praise_phrases == 2
    assert today.praise_cost_usd > 0
    # m2 is followed by no request in the original file -> no echo there; m4 is
    # followed by m3 in the resumed file -> one echo at the Sonnet cache-read rate.
    m4_tokens = scan("You're absolutely right. I'll fix the index.").tokens
    assert today.praise_echo_cost_usd == approx(m4_tokens * 1 * 0.30 / 1e6)
    assert snap.per_day[date(2026, 11, 5)].total.praise_phrases == 1   # "Great idea."


# ── per-file cost (SP-1401 back end) ──

def test_per_file_cost_charges_copied_messages_to_the_original(snap):
    costs = snap.per_file_cost
    assert costs[str(snap.root / ORIGINAL)] == approx(COST["m1"] + COST["m2"])
    assert costs[str(snap.root / RESUMED)] == approx(COST["m4"] + COST["m3"])
    assert costs[str(snap.root / OTHER)] == approx(COST["m5"] + COST["m6"] + COST["m7"])
    assert sum(costs.values()) == approx(snap.lifetime.cost_usd)


def test_files_total_and_sessions_today(snap):
    assert snap.files_total == 3
    assert snap.sessions_today == 2    # original (00:30) and resumed (10:00, 14:10)


def test_progress_reports_every_file(tmp_path):
    root = copy_spend_tree(tmp_path)
    seen = []
    Aggregator(claude_root=root, now_fn=lambda: SPEND_NOW, tz=PARIS).refresh(
        progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_a_failing_progress_callback_does_not_stop_the_scan(tmp_path):
    root = copy_spend_tree(tmp_path)

    def boom(d, t):
        raise RuntimeError("ui gone")
    s = Aggregator(claude_root=root, now_fn=lambda: SPEND_NOW, tz=PARIS).refresh(progress=boom)
    assert s.files_total == 3


def test_missing_root_is_an_empty_snapshot(tmp_path):
    s = Aggregator(claude_root=tmp_path / "nope", now_fn=lambda: SPEND_NOW, tz=PARIS).refresh()
    assert s.files_total == 0 and s.lifetime.cost_usd == 0.0


def test_deleted_transcripts_drop_out_of_the_cache(tmp_path):
    root = copy_spend_tree(tmp_path)
    agg = Aggregator(claude_root=root, now_fn=lambda: SPEND_NOW, tz=PARIS)
    agg.refresh()
    (root / OTHER).unlink()
    s = agg.refresh()
    assert s.files_total == 2
    assert str(root / OTHER) not in s.per_file_cost
    assert len(agg._file_cache) == 2


def test_dst_day_hourly_bucket(tmp_path):
    """29 Mar 2026: Paris jumps 02:00 -> 03:00. 01:30Z is 03:30 local (CEST)."""
    pdir = tmp_path / "-p"
    pdir.mkdir()
    line = {"type": "assistant", "timestamp": "2026-03-29T01:30:00Z",
            "message": {"id": "x", "model": "claude-haiku-4-5", "role": "assistant",
                        "usage": {"input_tokens": 1000, "output_tokens": 0}}}
    (pdir / "s.jsonl").write_text(json.dumps(line) + "\n")
    now = datetime(2026, 3, 29, 10, 0, tzinfo=timezone.utc)
    s = Aggregator(claude_root=tmp_path, now_fn=lambda: now, tz=PARIS).refresh()
    assert s.hourly_today[3] == approx(0.001)
    assert s.today.cost_usd == approx(0.001)
