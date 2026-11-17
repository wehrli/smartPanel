"""Demo scenarios mirror the prototype's data (SP-0012, SP-0013)."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest

from smartpanel.demo import DEMO_NOW, SCENARIOS, build_state
from smartpanel.demo import scenarios as sc
from smartpanel.state import Store, to_dict
from smartpanel.state.model import (IDLE, LEVEL_CRITICAL, LEVEL_OK, OUTCOME_JOURNALED,
                                    OUTCOME_TIMEOUT, RUNNING, WAITING)
from tests.helpers_state import (dump_fixture, fixture_path, load_fixture, state_for,
                                 state_from_dict, store_for)

UTC_NOW = datetime(2026, 11, 17, 14, 32, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def names(state):
    return [s.name for s in state.sessions.items]


# ── every scenario ──

@pytest.mark.parametrize("name", SCENARIOS)
def test_every_scenario_builds_and_serialises(name):
    st = build_state(name, DEMO_NOW)
    assert st.app.demo == name
    store = Store(st)
    store.to_json()
    assert state_from_dict(to_dict(st)) == st          # the harness round-trips
    assert set(sc.DESCRIPTIONS) == set(SCENARIOS)


@pytest.mark.parametrize("name", SCENARIOS)
def test_build_state_is_deterministic(name):
    assert build_state(name, DEMO_NOW) == build_state(name, DEMO_NOW)


def test_unknown_scenario_raises():
    with pytest.raises(ValueError, match="busy"):
        build_state("nope")


def test_default_now_is_wall_clock():
    st = build_state("busy")
    assert abs(st.sessions.updated_at - time.time()) < 5


# ── busy (the prototype's DEFAULTS) ──

def test_busy_sessions_match_prototype():
    st = state_for("busy")
    items = st.sessions.items
    # display order: waiting, running, running, idle
    assert [(s.name, s.title, s.activity) for s in items] == [
        ("claude-smartPanel", "Fix swap alert hysteresis", WAITING),
        ("claude-tacomap", "Truck search filters", RUNNING),
        ("claude-JobScout", "Stripe webhook retries", RUNNING),
        ("claude-ledgerly", "Migrate goose 0023", IDLE),
    ]
    w = items[0]
    assert w.attached and w.cost_usd == 2.90
    assert DEMO_NOW - w.created == 62 * 60
    assert w.prompt.question == "Do you want to make this edit to alerts.py?"
    assert w.prompt.options[1] == "Yes, allow all edits this session"
    assert w.prompt.kind == "permission"
    assert w.prompt.fingerprint == sc.fingerprint(w.prompt.question, w.prompt.options)
    assert w.waiting_since == DEMO_NOW - 240
    assert items[1].doing == "Bash · npm test -- filters"
    assert items[2].doing == "Waiting for 2 background agents"
    assert items[3].doing is None and items[3].prompt is None
    assert DEMO_NOW - items[3].created == 190 * 60
    assert st.sessions.count(WAITING) == 1 and st.sessions.count(RUNNING) == 2


def test_busy_spend_matches_prototype():
    sp = state_for("busy").spend
    assert (sp.cost_today, sp.cost_week, sp.cost_lifetime) == (23.84, 142.10, 2318.55)
    assert sp.tokens_today == 41_200_000 and sp.tokens_today_human == "41.2M"
    assert sp.burn_per_hour == 3.10 and sp.cache_hit_pct == 94
    assert round(sum(sp.hourly_today), 2) == 23.84
    assert sp.hourly_today[14] == 0.74 and sp.hourly_today[13] == 1.4
    assert sp.hourly_today[5] == 0.4 and sum(sp.hourly_today[15:]) == 0
    assert [(p.name, p.cost_usd) for p in sp.projects_today] == [
        ("smartPanel", 9.20), ("tacomap", 7.45), ("JobScout", 5.10), ("ledgerly", 2.09)]
    assert [(m.family, m.cost_usd) for m in sp.models_today] == [
        ("Opus", 19.6), ("Sonnet", 3.91), ("Haiku", 0.33)]
    assert sp.praise_cost_today == 0.07 and sp.praise_echo_today == 0.21
    assert len(sp.days) == 14
    assert [d.label for d in sp.days[:4]] == ["Today", "Yesterday", "Sun 15 Nov", "Sat 14 Nov"]
    assert sp.days[-1].label == "Wed 4 Nov" and sp.days[-1].date == "2026-11-04"
    assert sp.days[1].cost_usd == 31.02
    assert sp.days[3].cost_usd == 0 and sp.days[3].projects == [] and sp.days[3].models == []
    assert sp.days[0].cost_usd == 23.84
    fc = sp.forecast
    assert fc.budget_usd == 40.0 and fc.pct == 59.6
    assert fc.eta == "19:45" and fc.eta_reason is None   # 14:32 + 16.16/3.10 h


def test_busy_system_matches_prototype():
    sy = state_for("busy").system
    assert sy.cpu.percent == 23 and sy.cpu.temp_c == 61 and sy.cpu.cores == 8
    assert len(sy.cpu.history) == 30
    assert sy.memory.percent == 68 and sy.memory.used_gb == 10.88 and sy.memory.total_gb == 16
    assert sy.swap.percent == 12 and sy.swap.used_gb == 0.96 and sy.swap.total_gb == 8
    assert sy.swap.level == LEVEL_OK and (sy.swap.warn_at, sy.swap.crit_at) == (60, 85)
    assert (sy.disk.mount, sy.disk.percent, sy.disk.used_gb, sy.disk.total_gb) == \
        ("/", 41, 196, 476)
    assert sy.net.down_bps == 2.4e6 and sy.net.up_bps == 180_000 and sy.net.iface == "wlp2s0"
    assert len(sy.net.history_down) == 12
    assert sy.gpu.available is False and sy.gpu.percent is None
    assert all(0 <= v <= 100 for v in sy.cpu.history + sy.memory.history + sy.swap.history)


def test_busy_journal_has_three_seed_receipts():
    j = state_for("busy").journal
    assert [e.title for e in j.entries] == ["Map clustering", "Résumé parser",
                                            "Pricing table refresh"]
    assert [e.outcome for e in j.entries] == [OUTCOME_JOURNALED, OUTCOME_TIMEOUT,
                                              OUTCOME_JOURNALED]
    first = j.entries[0]
    assert datetime.fromtimestamp(first.time).strftime("%H:%M") == "12:02"
    assert first.id == "2026-11-17-1202-tacomap"
    assert first.path == "~/.claude/smartpanel/journals/2026-11-17-1202-tacomap.md"
    assert first.duration_s == 9000
    assert first.tools == {"Read": 14, "Edit": 9, "Bash": 6, "Grep": 4}
    assert first.commands == ["npm test -- map", "npm run build"]
    assert j.entries[1].confirmation.startswith("The /journal skill timed out")
    assert j.monthly_log_path == "~/dev/omdev/JOURNAL/2026-11.md"


def test_morning_now_never_puts_receipts_in_the_future():
    morning = datetime(2026, 11, 17, 8, 0).timestamp()
    j = build_state("busy", morning).journal
    assert all(e.time < morning for e in j.entries)


# ── the other scenarios ──

def test_calm():
    st = state_for("calm")
    assert {s.activity for s in st.sessions.items} == {IDLE}
    assert all(s.prompt is None and s.doing is None for s in st.sessions.items)
    sp = st.spend
    assert sp.cost_today == 8.10 and sp.burn_per_hour == 0.4 and sp.tokens_today_human == "14.1M"
    assert round(sum(sp.hourly_today), 2) == 8.10
    assert st.system.cpu.percent == 6 and st.system.memory.percent == 41
    assert sp.forecast.eta_reason == "tomorrow"


def test_alert():
    st = state_for("alert")
    assert st.system.swap.percent == 88 and st.system.swap.level == LEVEL_CRITICAL
    assert st.system.swap.history[-2:] == [86, 88] and len(st.system.swap.history) == 30
    assert st.sessions.count(WAITING) == 2
    q = next(s for s in st.sessions.items if s.project == "ledgerly")
    assert q.prompt.question == "Which migration strategy should I use for 0023?"
    assert q.prompt.kind == "question"
    assert st.spend.cost_today == 41.20 and st.spend.burn_per_hour == 7.8
    assert st.spend.tokens_today_human == "72.4M"
    assert st.spend.forecast.eta_reason == "over" and st.spend.forecast.over_by_usd == 1.20
    assert st.system.cpu.percent == 92 and st.system.memory.percent == 97


def test_alert_swap_level_follows_thresholds():
    from smartpanel.alerts import Thresholds
    st = build_state("alert", DEMO_NOW, thresholds=Thresholds(warning=60, critical=90))
    assert st.system.swap.level == 1 and st.system.swap.crit_at == 90


def test_empty():
    st = state_for("empty")
    assert st.sessions.items == [] and st.sessions.tmux_ok
    assert st.spend.cost_today == 23.84


def test_many_has_nine_unique_sessions():
    st = state_for("many")
    assert len(st.sessions.items) == 9
    assert len(set(names(st))) == 9
    assert "claude-tacomap-2" in names(st) and "claude-ledgerly-2" in names(st)
    assert st.sessions.count(WAITING) == 2 and st.sessions.count(RUNNING) == 3
    acts = [s.activity for s in st.sessions.items]
    assert acts == sorted(acts, key=lambda a: {WAITING: 0, RUNNING: 1, IDLE: 4}[a])


def test_cold_is_loading_with_unknown_spend():
    st = state_for("cold")
    assert (st.app.loading.done, st.app.loading.total) == (0, 1284)
    assert st.spend.cost_today is None and st.spend.days == []
    assert len(st.sessions.items) == 4


def test_first_run():
    st = state_for("first")
    assert st.app.first_run and st.sessions.items == [] and st.journal.entries == []
    sp = st.spend
    assert (sp.cost_today, sp.cost_week, sp.cost_lifetime, sp.tokens_today) == (0, 0, 0, 0)
    assert sp.burn_per_hour == 0 and sp.days == [] and sum(sp.hourly_today) == 0


def test_notmux():
    st = state_for("notmux")
    assert st.sessions.tmux_ok is False and st.sessions.items == []


def test_noswap_is_not_zero_percent():
    sw = state_for("noswap").system.swap
    assert sw.percent is None and sw.used_gb is None and sw.total_gb is None
    assert sw.level == LEVEL_OK and sw.history == []


def test_settings_section_is_prefilled():
    from smartpanel.config import DEFAULTS
    assert state_for("busy").settings.values == DEFAULTS


# ── pure helpers ──

@pytest.mark.parametrize("hour,expect_total", [(14, 23.84), (23, 23.84), (3, 23.84)])
def test_hourly_preserves_the_total(hour, expect_total):
    now = datetime(2026, 11, 17, hour, 10).timestamp()
    h = sc.hourly_today(1.0, now)
    assert len(h) == 24 and round(sum(h), 2) == expect_total
    assert h[hour] == 0.74
    assert sum(h[hour + 1:]) == 0


@pytest.mark.parametrize("spent,burn,budget,eta,reason", [
    (23.84, 3.10, 40, "19:45", None), (41.2, 7.8, 40, None, "over"),
    (8.10, 0.4, 40, None, "tomorrow"), (10, 0, 40, None, "never"),
])
def test_forecast(spent, burn, budget, eta, reason):
    fc = sc.compute_forecast(spent, burn, budget, DEMO_NOW)
    assert (fc.eta, fc.eta_reason) == (eta, reason)


def test_forecast_without_budget_is_off():
    assert sc.compute_forecast(10, 1, None, DEMO_NOW).budget_usd is None


def test_unique_names():
    assert sc.unique_name("x", set()) == "claude-x"
    assert sc.unique_name("x", {"claude-x", "claude-x-2"}) == "claude-x-3"


def test_fingerprint_is_stable_and_sensitive():
    a = sc.fingerprint("Q?", ["Yes", "No"])
    assert a == sc.fingerprint("Q?", ("Yes", "No"))
    assert a != sc.fingerprint("Q?", ["Yes", "No", "Maybe"])
    assert a != sc.fingerprint("Q!", ["Yes", "No"])


# ── fixtures (tests/fixtures/demo/*.yaml) ──

@pytest.mark.parametrize("name", SCENARIOS)
def test_fixtures_match_build_state(name, utc):
    """The YAML fixtures are a UTC snapshot of build_state at the prototype's clock.
    Regenerate with SMARTPANEL_REGEN_FIXTURES=1 after an intended data change."""
    state = build_state(name, UTC_NOW)
    path = fixture_path(name)
    if os.environ.get("SMARTPANEL_REGEN_FIXTURES") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_fixture(state), encoding="utf-8")
    assert load_fixture(name) == state


def test_store_for_helper():
    store = store_for("many")
    assert len(store.state.sessions.items) == 9
