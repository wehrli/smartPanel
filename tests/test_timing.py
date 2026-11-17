"""smartpanel.timing (SP-2702): the 50 ms main-loop budget, made visible."""
from __future__ import annotations

import logging
import types

import pytest

from smartpanel import timing


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance_ms(self, ms: float) -> None:
        self.t += ms / 1000.0


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(timing, "clock", c)
    timing.reset()
    timing.set_budget(timing.BUDGET_MS)
    yield c
    timing.uninstall()
    timing.reset()
    timing.set_budget(timing.BUDGET_MS)


def test_fast_call_is_silent(clock, caplog):
    @timing.timed("fast")
    def fast():
        clock.advance_ms(12)
        return 7

    with caplog.at_level(logging.WARNING, "smartpanel.timing"):
        assert fast() == 7
    assert caplog.records == []
    st = timing.stats()["fast"]
    assert (st.calls, st.slow) == (1, 0)
    assert st.worst_ms == pytest.approx(12)


def test_slow_call_logs_once_with_name_and_ms(clock, caplog):
    @timing.timed
    def scan():
        clock.advance_ms(83.4)

    with caplog.at_level(logging.WARNING, "smartpanel.timing"):
        scan()
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "test_timing.test_slow_call_logs_once_with_name_and_ms.<locals>.scan" in msg
    assert "83.4 ms" in msg and "budget 50 ms" in msg


def test_exactly_on_budget_is_not_slow(clock):
    assert timing.record("x", 50.0) is False
    assert timing.record("x", 50.1) is True


def test_exception_is_still_timed(clock):
    @timing.timed("boom")
    def boom():
        clock.advance_ms(70)
        raise RuntimeError

    with pytest.raises(RuntimeError):
        boom()
    assert timing.stats()["boom"].slow == 1


def test_measure_block_and_custom_budget(clock):
    with timing.measure("block", budget=5):
        clock.advance_ms(6)
    assert timing.stats()["block"].slow == 1


def test_callback_name_for_methods_and_partials():
    import functools

    class Svc:
        def tick(self):
            pass

    name = timing.callback_name(Svc().tick)
    assert name.endswith("Svc.tick")
    assert timing.callback_name(functools.partial(Svc().tick)) == name
    assert timing.callback_name(print) == "builtins.print"


def test_install_wraps_glib_sources_and_uninstall_restores(clock):
    calls = []

    def timeout_add(interval, fn, *args, **kw):
        calls.append(("t", interval, fn, args, kw))
        return 1

    def timeout_add_seconds(interval, fn, *args, **kw):
        calls.append(("s", interval, fn, args, kw))
        return 2

    def idle_add(fn, *args, **kw):
        calls.append(("i", fn, args, kw))
        return 3

    glib = types.SimpleNamespace(timeout_add=timeout_add,
                                 timeout_add_seconds=timeout_add_seconds,
                                 idle_add=idle_add)

    def slow_cb(arg):
        clock.advance_ms(120)
        return False

    timing.install(glib=glib, watchdog=False)
    timing.install(glib=glib, watchdog=False)        # idempotent: wrappers are not stacked
    assert timing.installed()
    assert glib.timeout_add(1000, slow_cb, "a", priority=5) == 1
    assert glib.idle_add(slow_cb, "b") == 3
    assert glib.timeout_add_seconds(2, slow_cb) == 2
    _, interval, wrapped, args, kw = calls[0]
    assert (interval, args, kw) == (1000, ("a",), {"priority": 5})
    assert wrapped is not slow_cb and wrapped("a") is False
    calls[1][1]("b")
    name = timing.callback_name(slow_cb)
    assert timing.stats()[name].slow == 2

    timing.uninstall()
    assert not timing.installed()
    assert glib.timeout_add is timeout_add and glib.idle_add is idle_add
    assert glib.timeout_add_seconds is timeout_add_seconds


def test_install_on_real_glib_times_an_idle_callback(clock):
    from gi.repository import GLib

    orig = GLib.idle_add
    seen = []

    def cb():
        clock.advance_ms(60)
        seen.append(1)
        return GLib.SOURCE_REMOVE

    timing.install()
    try:
        GLib.idle_add(cb)
        ctx = GLib.MainContext.default()
        for _ in range(50):
            if seen:
                break
            ctx.iteration(False)
    finally:
        timing.uninstall()
    assert seen == [1]
    assert GLib.idle_add is orig
    assert timing.stats()[timing.callback_name(cb)].slow == 1


def test_report_lists_worst_first(clock):
    timing.record("a.fast", 3)
    timing.record("b.slow", 90)
    timing.record("b.slow", 10)
    out = timing.report().splitlines()
    assert out[0].split() == ["callback", "calls", "slow", "mean", "ms", "worst", "ms"]
    assert out[1].split() == ["b.slow", "2", "1", "50.0", "90.0"]
    assert out[2].split()[0] == "a.fast"
    timing.reset()
    assert timing.report() == "no callbacks timed"


# ── names a profiling run can act on ──

def test_forwarding_closure_is_named_after_its_target():
    from smartpanel.services.spend import GLibScheduler

    posted = []
    sched = GLibScheduler.__new__(GLibScheduler)
    sched._GLib = types.SimpleNamespace(idle_add=posted.append)

    class SpendService:
        def _apply(self):
            pass

    sched.idle_add(SpendService()._apply)
    name = timing.callback_name(posted[0])
    assert name.startswith("services.spend.GLibScheduler.idle_add.<locals>.once -> ")
    assert name.endswith("SpendService._apply")


def test_explicit_name_wins():
    def cb():
        pass
    cb.__smartpanel_name__ = "tray.repaint"
    assert timing.callback_name(cb) == "tray.repaint"


def test_closure_over_data_keeps_its_own_name():
    x = 3

    def cb():
        return x
    assert timing.callback_name(cb).endswith("test_closure_over_data_keeps_its_own_name.<locals>.cb")


# ── loop-lag watchdog: catches what the wrappers can't (signal / D-Bus handlers) ──

def test_watchdog_logs_lag_over_budget_with_last_callback(clock, caplog):
    timing.record("tray.Tray._paint", 4)
    dog = timing.Watchdog(interval_ms=25)
    with caplog.at_level(logging.WARNING, "smartpanel.timing"):
        assert dog.tick(0.0) == 0.0
        assert dog.tick(0.030) == pytest.approx(5)        # 5 ms late: fine
        lag = dog.tick(0.030 + 0.145)                     # blocked by a click handler
    assert lag == pytest.approx(120)
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "blocked ~120 ms" in msg and "tray.Tray._paint" in msg
    st = timing.stats()[timing.LAG_NAME]
    assert (st.slow, round(st.worst_ms)) == (1, 120)


def test_install_starts_and_uninstall_stops_the_watchdog(clock):
    added, removed = [], []
    glib = types.SimpleNamespace(
        timeout_add=lambda ms, fn, *a, **k: added.append((ms, fn)) or 41,
        timeout_add_seconds=lambda *a, **k: 2,
        idle_add=lambda *a, **k: 3,
        source_remove=removed.append)
    timing.install(glib=glib)
    assert added and added[0][0] == timing.HEARTBEAT_MS
    assert added[0][1]() is True                          # heartbeat keeps itself alive
    timing.uninstall()
    assert removed == [41]


def test_run_strips_the_flag_installs_and_reports(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(timing, "install", lambda: seen.setdefault("installed", True))
    import smartpanel.cli as cli
    monkeypatch.setattr(cli, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    assert timing.run(["--debug-timing", "--demo", "busy"]) == 0
    assert seen == {"installed": True, "argv": ["--demo", "busy"]}
    assert capsys.readouterr().err.strip()          # the report


def test_watchdog_labels_the_first_late_tick_as_startup(clock, caplog):
    timing.record("tray.Tray._paint", 4)
    dog = timing.Watchdog(interval_ms=25)
    dog.last = 0.0                                        # as start() leaves it
    with caplog.at_level(logging.WARNING, "smartpanel.timing"):
        dog.tick(0.5)
    assert "startup, before the first heartbeat" in caplog.records[0].getMessage()
